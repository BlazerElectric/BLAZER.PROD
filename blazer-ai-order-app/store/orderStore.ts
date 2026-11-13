"use client";

import { create } from "zustand";
import { createJSONStorage, persist, type StateStorage } from "zustand/middleware";
import type { ChatMessage, ChatSuggestion, LineSource, MatchResult, OrderLine, ProductSummary } from "@/lib/types";

export type BridgeStatus = "checking" | "connected" | "standalone" | "embedded-no-bridge";

export interface ChatEntry extends ChatMessage {
  id: string;
  suggestions?: ChatSuggestion[];
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
  setBridge: (status: BridgeStatus) => void;
}

const newId = () =>
  typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;

/** Embedded third-party iframes may have storage blocked or partitioned; fall back to memory. */
const memory = new Map<string, string>();
const safeStorage: StateStorage = {
  getItem: (name) => {
    try {
      return window.localStorage.getItem(name);
    } catch {
      return memory.get(name) ?? null;
    }
  },
  setItem: (name, value) => {
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
