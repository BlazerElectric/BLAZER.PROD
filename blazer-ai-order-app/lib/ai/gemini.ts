import "server-only";
import { GoogleGenAI } from "@google/genai";
import { z } from "zod";
import { getEngine } from "../catalog/server";
import type { ChatMessage, ChatResponse, ChatSuggestion, MatchResult } from "../types";

export const DEFAULT_MODEL = "gemini-3.1-flash-lite";
const MAX_ITEMS = 30;

let client: GoogleGenAI | null = null;

export function aiConfigured(): boolean {
  return Boolean(process.env.GEMINI_API_KEY);
}

function ai(): GoogleGenAI {
  if (!process.env.GEMINI_API_KEY) throw new AiNotConfiguredError();
  client ??= new GoogleGenAI({ apiKey: process.env.GEMINI_API_KEY });
  return client;
}

function model(): string {
  return process.env.GEMINI_MODEL || DEFAULT_MODEL;
}

export class AiNotConfiguredError extends Error {
  constructor() {
    super("The AI assistant is not configured (GEMINI_API_KEY is missing).");
  }
}

const SYSTEM_PROMPT = `You are the Blazer Electric AI Order Desk, a counter-pro assistant for a wholesale electrical distributor.
Customers are licensed electricians and contractors.

Your jobs:
1. Answer technical trade questions concisely and practically. Cite NEC article/table numbers when relevant
   (e.g. "NEC 250.122 / Table 250.122"). Always remind users that the local AHJ and adopted code edition govern.
2. When the user wants material (or a question implies a material list), propose a bill of materials.

Rules for "items":
- Each item is ONE purchasable product. Write "search_query" in terse distributor shorthand that matches catalog
  titles, e.g. "3/4 EMT SSCR CONN", "THHN 6 GRN STR", "QO120", "4 SQ BOX 2-1/8 DP", "NM-B 12/2", "5/8 GROUND ROD".
- If a product listed in CATALOG CONTEXT is exactly right, put its SKU in "sku"; otherwise leave "sku" empty.
  Never invent SKUs.
- Use realistic contractor quantities; wire and conduit quantities are in feet ("ft"), everything else "ea".
- Put cutting/handling instructions (e.g. "5 cuts @ 50ft") in "note".
- "reason" is a short justification (code reference or purpose).
- Return an empty "items" array for purely informational questions.
- Keep "reply" under 180 words, plain text (no markdown tables).`;

const responseSchema = {
  type: "object",
  properties: {
    reply: { type: "string" },
    items: {
      type: "array",
      items: {
        type: "object",
        properties: {
          description: { type: "string" },
          search_query: { type: "string" },
          sku: { type: "string" },
          quantity: { type: "number" },
          unit: { type: "string", enum: ["ea", "ft"] },
          note: { type: "string" },
          reason: { type: "string" },
        },
        required: ["description", "search_query", "quantity", "unit"],
      },
    },
  },
  required: ["reply", "items"],
} as const;

const AiReply = z.object({
  reply: z.string(),
  items: z
    .array(
      z.object({
        description: z.string(),
        search_query: z.string(),
        sku: z.string().optional().default(""),
        quantity: z.number(),
        unit: z.string().optional().default("ea"),
        note: z.string().optional().default(""),
        reason: z.string().optional().default(""),
      }),
    )
    .default([]),
});

function catalogContext(query: string): string {
  const engine = getEngine();
  const hits = engine.search(query, { limit: 15 }).filter((h) => h.quality >= 0.3);
  if (!hits.length) return "(no direct catalog hits for this message)";
  return hits.map((h) => `${h.product.sku} | ${h.product.title} | ${h.product.category}`).join("\n");
}

/** Shop-talk glossary from config/trade_slang.csv so the model understands contractor slang. */
function slangGlossary(): string {
  const entries = getEngine().slang.entries;
  if (!entries.length) return "";
  const lines = entries.map((e) => `- ${e.terms.join(" / ")}: ${e.notes || e.searchAs}`);
  return `TRADE SLANG customers may use (translate to catalog terms in "search_query"):\n${lines.join("\n")}`;
}

function resolveItem(sku: string, query: string): MatchResult {
  const engine = getEngine();
  const product = sku ? engine.summary(sku) : undefined;
  if (product) {
    const full = engine.get(product.sku)!;
    const searched = engine.match(query);
    return {
      confidence: "high",
      product,
      alternatives: searched.alternatives.filter((p) => p.sku !== product.sku),
      similar: engine.summariesFor(full.similar),
    };
  }
  return engine.match(query);
}

export async function runChat(messages: ChatMessage[], cartSummary: string): Promise<ChatResponse> {
  const lastUser = [...messages].reverse().find((m) => m.role === "user")?.content ?? "";
  const contents = messages.map((m) => ({
    role: m.role === "assistant" ? "model" : "user",
    parts: [{ text: m.content }],
  }));
  const context = `CATALOG CONTEXT (top matches for the latest message):\n${catalogContext(lastUser)}\n\nCURRENT ORDER:\n${cartSummary || "(empty)"}`;

  const response = await ai().models.generateContent({
    model: model(),
    contents,
    config: {
      systemInstruction: [SYSTEM_PROMPT, slangGlossary(), context].filter(Boolean).join("\n\n"),
      responseMimeType: "application/json",
      responseJsonSchema: responseSchema,
      temperature: 0.3,
    },
  });

  const parsed = AiReply.safeParse(JSON.parse(response.text ?? "{}"));
  if (!parsed.success) throw new Error("The AI returned an unexpected response format.");

  const suggestions: ChatSuggestion[] = parsed.data.items.slice(0, MAX_ITEMS).map((item) => ({
    description: item.description,
    quantity: Math.max(1, Math.min(99999, Math.round(item.quantity || 1))),
    unit: item.unit === "ft" ? "ft" : "ea",
    note: item.note,
    reason: item.reason,
    match: resolveItem(item.sku.trim(), item.search_query || item.description),
  }));
  return { reply: parsed.data.reply, suggestions };
}

export async function summarizeForShipping(orderText: string, fallback: string): Promise<string> {
  const response = await ai().models.generateContent({
    model: model(),
    contents: [{ role: "user", parts: [{ text: orderText }] }],
    config: {
      systemInstruction:
        "Write shipping/handling instructions for a wholesale electrical order, for warehouse pickers and the counter. " +
        "Plain text, at most 6 short lines, under 900 characters. Include every cut instruction with its SKU, " +
        "special handling notes, and list any items that could not be matched to the catalog so staff can source them. " +
        "Do not include prices. Do not invent information.",
      temperature: 0.2,
    },
  });
  const text = (response.text ?? "").trim();
  return text || fallback;
}
