"use client";

import { create } from "zustand";
import { createJSONStorage, persist, type StateStorage } from "zustand/middleware";
import type { ChatMessage, ChatSuggestion, LineSource, MatchResult, OrderLine, ProductSummary } from "@/lib/types";

export type BridgeStatus = "checking" | "connected" | "standalone" | "embedded-no-bridge";

export interface ChatEntry extends ChatMessage {
  id: string;
  suggestions?: ChatSuggestion[];
  appliedSuggestions?: number[];
}

export interface NewLineInput {
  source: LineSource;
  rawText: string;
  quantity: number;
  unit: string;
  comment?: string;
  match: MatchResult;
  reason?: string;
}

interface OrderState {
  lines: OrderLine[];
  shippingInstructions: string;
  chat: ChatEntry[];
  bridge: BridgeStatus;

  addLines: (inputs: NewLineInput[]) => void;
  addProduct: (product: ProductSummary, quantity?: number, source?: LineSource) => void;
  updateLine: (id: string, patch: Partial<Pick<OrderLine, "quantity" | "comment" | "manualText" | "unit">>) => void;
  selectProduct: (id: string, product: ProductSummary) => void;
  confirmLine: (id: string) => void;
  removeLine: (id: string) => void;
  clearOrder: () => void;
  setShippingInstructions: (value: string) => void;
  appendChat: (entry: Omit<ChatEntry, "id">) => void;
  clearChat: () => void;
  applySuggestions: (entryId: string, indices: number[]) => void;
  restoreLine: (line: OrderLine, index: number) => void;
  setBridge: (status: BridgeStatus) => void;
}

const newId = () =>
  typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;

/** Embedded third-party iframes may have storage blocked or partitioned; fall back to memory. */
const memory = new Map<string, string>();
let storageRestored = false;
const safeStorage: StateStorage = {
  getItem: (name) => {
    storageRestored = true;
    try {
      return window.localStorage.getItem(name);
    } catch {
      return memory.get(name) ?? null;
    }
  },
  setItem: (name, value) => {
    if (!storageRestored) return;
    try {
      window.localStorage.setItem(name, value);
    } catch {
      memory.set(name, value);
    }
  },
  removeItem: (name) => {
    try {
      window.localStorage.removeItem(name);
    } catch {
      memory.delete(name);
    }
  },
};

const clampQty = (q: number) => Math.max(1, Math.min(99999, Math.round(Number.isFinite(q) ? q : 1)));

export const useOrderStore = create<OrderState>()(
  persist(
    (set) => ({
      lines: [],
      shippingInstructions: "",
      chat: [],
      bridge: "checking",

      addLines: (inputs) =>
        set((state) => ({
          lines: [
            ...state.lines,
            ...inputs.map<OrderLine>((input) => ({
              id: newId(),
              source: input.source,
              rawText: input.rawText,
              quantity: clampQty(input.quantity),
              unit: input.unit || "ea",
              comment: input.comment ?? "",
              confidence: input.match.confidence,
              confirmed: false,
              product: input.match.product,
              alternatives: input.match.alternatives,
              similar: input.match.similar,
              manualText: input.match.product ? "" : input.rawText,
              reason: input.reason,
            })),
          ],
        })),

      addProduct: (product, quantity = 1, source = "search") =>
        set((state) => {
          const existing = state.lines.find((l) => l.product?.sku === product.sku && !l.comment);
          if (existing) {
            return {
              lines: state.lines.map((l) =>
                l.id === existing.id ? { ...l, quantity: clampQty(l.quantity + quantity) } : l,
              ),
            };
          }
          const line: OrderLine = {
            id: newId(),
            source,
            rawText: product.title,
            quantity: clampQty(quantity),
            unit: "ea",
            comment: "",
            confidence: "high",
            confirmed: true,
            product,
            alternatives: [],
            similar: [],
            manualText: "",
          };
          return { lines: [...state.lines, line] };
        }),

      updateLine: (id, patch) =>
        set((state) => ({
          lines: state.lines.map((l) =>
            l.id === id ? { ...l, ...patch, ...(patch.quantity !== undefined ? { quantity: clampQty(patch.quantity) } : {}) } : l,
          ),
        })),

      selectProduct: (id, product) =>
        set((state) => ({
          lines: state.lines.map((l) => {
            if (l.id !== id) return l;
            const pool = [l.product, ...l.alternatives].filter((p): p is ProductSummary => Boolean(p));
            return {
              ...l,
              product,
              confirmed: true,
              alternatives: pool.filter((p) => p.sku !== product.sku),
            };
          }),
        })),

      confirmLine: (id) =>
        set((state) => ({ lines: state.lines.map((l) => (l.id === id ? { ...l, confirmed: true } : l)) })),

      removeLine: (id) => set((state) => ({ lines: state.lines.filter((l) => l.id !== id) })),

      clearOrder: () => set({ lines: [], shippingInstructions: "" }),

      setShippingInstructions: (value) => set({ shippingInstructions: value }),

      appendChat: (entry) => set((state) => ({ chat: [...state.chat, { ...entry, id: newId() }].slice(-60) })),

      clearChat: () => set({ chat: [] }),

      applySuggestions: (entryId, indices) => set((state) => {
        const entry = state.chat.find((message) => message.id === entryId);
        if (!entry?.suggestions) return {};
        const applied = new Set(entry.appliedSuggestions ?? []);
        const lines = [...state.lines];
        for (const index of new Set(indices)) {
          const suggestion: ChatSuggestion | undefined = entry.suggestions[index];
          if (!suggestion || applied.has(index)) continue;
          const target = suggestion.lineId ? lines.findIndex((line) => line.id === suggestion.lineId) : -1;
          if (suggestion.lineId && target < 0) continue;
          const previous = target >= 0 ? lines[target] : undefined;
          const line: OrderLine = {
            id: previous?.id ?? newId(),
            source: previous?.source ?? "chat",
            rawText: previous?.rawText ?? suggestion.description,
            quantity: clampQty(suggestion.quantity),
            unit: suggestion.unit,
            comment: suggestion.note || previous?.comment || "",
            confidence: suggestion.match.confidence,
            confirmed: false,
            product: suggestion.match.product,
            alternatives: suggestion.match.alternatives,
            similar: suggestion.match.similar,
            manualText: suggestion.match.product ? "" : suggestion.description,
            reason: [suggestion.reason, suggestion.match.note].filter(Boolean).join(" · "),
          };
          if (target >= 0) lines[target] = line;
          else lines.push(line);
          applied.add(index);
        }
        return { lines, chat: state.chat.map((message) => message.id === entryId ? { ...message, appliedSuggestions: [...applied] } : message) };
      }),

      restoreLine: (line, index) => set((state) => {
        if (state.lines.some((item) => item.id === line.id)) return {};
        const lines = [...state.lines];
        lines.splice(Math.min(index, lines.length), 0, line);
        return { lines };
      }),

      setBridge: (status) => set({ bridge: status }),
    }),
    {
      name: "blazer-order-v1",
      version: 1,
      storage: createJSONStorage(() => safeStorage),
      skipHydration: true,
      partialize: (state) => ({
        lines: state.lines,
        shippingInstructions: state.shippingInstructions,
        chat: state.chat,
      }),
    },
  ),
);

/** A line is "resolved" when it has a product and either high confidence or a human confirmation. */
export function needsReview(line: OrderLine): boolean {
  if (!line.product) return true;
  return line.confidence !== "high" && !line.confirmed;
}
