import { afterEach, expect, it, vi } from "vitest";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});

it("does not overwrite a saved order when bridge detection precedes hydration", async () => {
  const saved = JSON.stringify({ state: { lines: [{ id: "saved-line", quantity: 20 }], chat: [], shippingInstructions: "Dock 2" }, version: 1 });
  const localStorage = { getItem: vi.fn(() => saved), setItem: vi.fn(), removeItem: vi.fn() };
  vi.stubGlobal("window", { localStorage });
  const { useOrderStore } = await import("../store/orderStore");
  useOrderStore.getState().setBridge("standalone");
  expect(localStorage.setItem).not.toHaveBeenCalled();
  await useOrderStore.persist.rehydrate();
  expect(useOrderStore.getState().lines).toHaveLength(1);
  expect(useOrderStore.getState().shippingInstructions).toBe("Dock 2");
  useOrderStore.getState().setShippingInstructions("Dock 3");
  expect(localStorage.setItem).toHaveBeenCalled();
});

async function freshStore() {
  vi.stubGlobal("window", { localStorage: { getItem: () => null, setItem: vi.fn(), removeItem: vi.fn() } });
  const { useOrderStore } = await import("../store/orderStore");
  await useOrderStore.persist.rehydrate();
  return useOrderStore;
}

const unmatched = { confidence: "none" as const, product: null, alternatives: [], similar: [] };

it("updates the existing line once, preserves handling notes and does not create duplicates", async () => {
  const store = await freshStore();
  store.getState().addLines([{ source: "paste", rawText: "12 thhn white", quantity: 250, unit: "ft", comment: "5 cuts @ 50ft", match: unmatched }]);
  const original = store.getState().lines[0];
  store.getState().appendChat({ role: "assistant", content: "Use stranded white THHN?", suggestions: [{ lineId: original.id, description: "12 THHN white stranded", quantity: 250, unit: "ft", note: "", reason: "Stranding clarified", match: unmatched }] });
  const entry = store.getState().chat[0];
  store.getState().applySuggestions(entry.id, [0, 0]);
  store.getState().applySuggestions(entry.id, [0]);
  expect(store.getState().lines).toHaveLength(1);
  expect(store.getState().lines[0]).toMatchObject({ id: original.id, rawText: original.rawText, quantity: 250, unit: "ft", comment: "5 cuts @ 50ft", confirmed: false });
  expect(store.getState().chat[0].appliedSuggestions).toEqual([0]);
});

it("does not turn a clarification for a removed line into a new item", async () => {
  const store = await freshStore();
  store.getState().appendChat({ role: "assistant", content: "Update", suggestions: [{ lineId: "removed", description: "12 THHN", quantity: 1, unit: "ft", note: "", reason: "", match: unmatched }] });
  store.getState().applySuggestions(store.getState().chat[0].id, [0]);
  expect(store.getState().lines).toEqual([]);
  expect(store.getState().chat[0].appliedSuggestions).toEqual([]);
});

it("adds a new proposal only once and can undo a removal at its original position", async () => {
  const store = await freshStore();
  store.getState().appendChat({ role: "assistant", content: "Add item", suggestions: [{ description: "10 QO120", quantity: 10, unit: "ea", note: "", reason: "", match: unmatched }] });
  const entry = store.getState().chat[0];
  store.getState().applySuggestions(entry.id, [0]);
  store.getState().applySuggestions(entry.id, [0]);
  expect(store.getState().lines).toHaveLength(1);
  const line = store.getState().lines[0];
  store.getState().removeLine(line.id);
  store.getState().restoreLine(line, 0);
  store.getState().restoreLine(line, 0);
  expect(store.getState().lines).toEqual([line]);
});