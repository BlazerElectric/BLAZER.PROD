import { z } from "zod";
import { AiNotConfiguredError, runChat } from "@/lib/ai/gemini";
import { clientKey, rateLimit } from "@/lib/ratelimit";

export const maxDuration = 60;

const Body = z.object({
  messages: z
    .array(z.object({ role: z.enum(["user", "assistant"]), content: z.string().min(1).max(4000) }))
    .min(1)
    .max(30),
  cartSummary: z.string().max(6000).optional().default(""),
  orderLines: z.array(z.object({
    id: z.string().min(1).max(100),
    description: z.string().max(1000),
    quantity: z.number().int().min(1).max(99999),
    unit: z.string().max(30),
    comment: z.string().max(1000),
  })).max(100).optional().default([]),
});

export async function POST(request: Request) {
  if (!rateLimit(`chat:${clientKey(request)}`, 20, 60_000)) {
    return Response.json({ error: "Too many requests. Please wait a minute and try again." }, { status: 429 });
  }
  const parsed = Body.safeParse(await request.json().catch(() => null));
  if (!parsed.success) return Response.json({ error: "Invalid chat request." }, { status: 400 });
  try {
    return Response.json(await runChat(parsed.data.messages.slice(-12), parsed.data.cartSummary, parsed.data.orderLines));
  } catch (error) {
    if (error instanceof AiNotConfiguredError) {
      return Response.json({ error: error.message }, { status: 503 });
    }
    console.error("chat failed", error);
    return Response.json({ error: "The AI assistant is temporarily unavailable." }, { status: 502 });
  }
}
