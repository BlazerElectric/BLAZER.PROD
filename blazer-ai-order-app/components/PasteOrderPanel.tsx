"use client";

import { Eraser, FileText, Loader2, NotebookPen, Plus, SendHorizonal, X } from "lucide-react";
import { useState } from "react";
import { parseOrder, sendChat } from "@/lib/api";
import { isMaterialList } from "@/lib/order/conversation";
import { useOrderStore } from "@/store/orderStore";
import { ProductSearchBox } from "./ProductSearchBox";
import { ChatAssistantPanel } from "./ChatAssistantPanel";

const EXAMPLE = `Smith residence - rough in
20 sticks 3/4" EMT
50 - 3/4 emt sscr conn
(25) 3/4 emt couplings
500' #12 THHN blk str
12 thhn white (5 cuts @ 50ft)
30 4 sq box 2-1/8 deep
10 QO120
QO230 - 4
romex 12-2 250ft coil x 3
a roll of 14/2 romex
50 colorado jims
100 red heads
gfci 20a tr wr (for kitchen island)
2 1/2 rigid 90 w/ coupling`;

interface Stats {
  total: number;
  high: number;
  medium: number;
  none: number;
}

export function PasteOrderPanel({ aiEnabled, text, setText }: { aiEnabled: boolean | null; text: string; setText: (value: string) => void }) {
  const addLines = useOrderStore((s) => s.addLines);
  const addProduct = useOrderStore((s) => s.addProduct);
  const clearOrder = useOrderStore((s) => s.clearOrder);
  const appendChat = useOrderStore((s) => s.appendChat);
  const shippingInstructions = useOrderStore((s) => s.shippingInstructions);
  const setShippingInstructions = useOrderStore((s) => s.setShippingInstructions);
  const [skipped, setSkipped] = useState<string[]>([]);
  const [replace, setReplace] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stats, setStats] = useState<Stats | null>(null);

  const build = async () => {
    if (!text.trim() || loading) return;
    setLoading(true);
    setError(null);
    try {
      if (!isMaterialList(text)) {
        if (aiEnabled === false) throw new Error("Conversation is unavailable on this deployment. Material lists and catalog search are still available.");
        if (text.length > 4000) throw new Error("Please keep a conversational message under 4,000 characters.");
        const state = useOrderStore.getState();
        const messages = [...state.chat.map(({ role, content }) => ({ role, content })), { role: "user" as const, content: text }].slice(-12);
        const orderLines = state.lines.slice(0, 100).map((line) => ({
          id: line.id,
          description: `${line.rawText}${line.product ? ` | #${line.product.sku} ${line.product.title}` : ""}`.slice(0, 1000),
          quantity: line.quantity,
          unit: line.unit,
          comment: line.comment,
        }));
        const response = await sendChat(messages, "", orderLines);
        appendChat({ role: "user", content: text });
        appendChat({ role: "assistant", content: response.reply, suggestions: response.suggestions });
        setText("");
        return;
      }
      const { lines, skipped: headings } = await parseOrder(text);
      if (replace && lines.length) clearOrder();
      const offset = replace ? 0 : useOrderStore.getState().lines.length;
      setSkipped(headings);
      addLines(
        lines.map((l) => ({
          source: "paste" as const,
          rawText: l.raw,
          quantity: l.quantity,
          unit: l.unit,
          comment: l.comment,
          match: { confidence: l.confidence, product: l.product, alternatives: l.alternatives, similar: l.similar },
          reason: l.note,
        })),
      );
      setStats({
        total: lines.length,
        high: lines.filter((l) => l.confidence === "high").length,
        medium: lines.filter((l) => l.confidence === "medium").length,
        none: lines.filter((l) => l.confidence === "none").length,
      });
      appendChat({ role: "user", content: text.slice(0, 4000) });
      const review = lines.map((line, index) => ({ line, number: offset + index + 1 })).filter(({ line }) => line.confidence !== "high");
      appendChat({ role: "assistant", content: [
        `${replace && lines.length ? "Replaced the order with" : "Added"} ${lines.length} item${lines.length === 1 ? "" : "s"}. ${review.length ? `${review.length} need attention.` : ""}`,
        ...review.slice(0, 3).map(({ line, number }) => `Line ${number} (${line.raw}): ${line.note || (line.product ? "Please verify the selected product and specifications." : "No catalog match. Can you provide a manufacturer or part number?")}`),
      ].join("\n") });
      if (lines.length || headings.length) setText("");
      setReplace(false);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  const dropSkipped = (value: string) => setSkipped((list) => list.filter((s) => s !== value));
  const addHeadingAsItem = (value: string) => {
    addLines([
      { source: "paste", rawText: value, quantity: 1, unit: "ea", match: { confidence: "none", product: null, alternatives: [], similar: [] } },
    ]);
    dropSkipped(value);
  };
  const addHeadingToNotes = (value: string) => {
    setShippingInstructions(shippingInstructions.trim() ? `${shippingInstructions.trim()}\n${value}` : value);
    dropSkipped(value);
  };

  return (
    <section aria-labelledby="paste-heading" className="space-y-4">
      <div>
        <h2 id="paste-heading" className="text-lg font-semibold text-blazer-900">
          What do you need?
        </h2>
      </div>

      <ChatAssistantPanel loading={loading} />
      {aiEnabled === false && <p role="status" className="text-xs text-amber-800">Conversation unavailable · Catalog ordering is online</p>}

      <div className="overflow-hidden rounded-xl border border-smoke-700 bg-white shadow-sm transition focus-within:border-blazer-600 focus-within:ring-2 focus-within:ring-blazer-100">
        <label htmlFor="order-text" className="sr-only">
          Order text
        </label>
        <textarea
          id="order-text"
          maxLength={30000}
          disabled={loading}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
              e.preventDefault();
              void build();
            }
          }}
          rows={6}
          spellCheck={false}
          placeholder={"20 sticks 3/4 EMT\n50 3/4 emt sscr conn\n500' #12 THHN blk str\n\nWhat else do I need for six outdoor outlets?"}
          className="block w-full resize-y border-0 bg-transparent p-4 font-mono text-sm leading-6 outline-none placeholder:text-slate-400"
        />
        <div className="flex flex-wrap items-center gap-2 border-t border-smoke-600 bg-smoke-400 px-3 py-2">
          <button
            type="button"
            disabled={loading}
            onClick={() => setText(EXAMPLE)}
            className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium text-slate-600 hover:bg-white"
          >
            <FileText className="size-3.5" aria-hidden /> Load example
          </button>
          <button
            type="button"
            onClick={() => setText("")}
            disabled={!text || loading}
            className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium text-slate-600 hover:bg-white disabled:opacity-40"
          >
            <Eraser className="size-3.5" aria-hidden /> Clear
          </button>
          <label className="ml-auto inline-flex items-center gap-1.5 text-xs text-slate-600">
            <input type="checkbox" checked={replace} disabled={loading || !isMaterialList(text)} onChange={(e) => setReplace(e.target.checked)} className="accent-blazer-700" />
            Replace order with list
          </label>
          <button
            type="button"
            onClick={() => void build()}
            disabled={!text.trim() || loading}
            className="inline-flex items-center gap-1.5 rounded-lg bg-blazer-800 px-4 py-2 text-sm font-semibold text-white shadow-sm transition hover:bg-blazer-700 focus-visible:ring-4 focus-visible:ring-blazer-200 disabled:opacity-50"
          >
            {loading ? <Loader2 className="size-4 animate-spin" aria-hidden /> : <SendHorizonal className="size-4" aria-hidden />}
            {loading ? "Working…" : replace && isMaterialList(text) ? "Replace order" : "Send"}
          </button>
        </div>
      </div>
      {error && <p className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-800">{error}</p>}
      {stats && (
        <p role="status" className="rounded-lg bg-blazer-50 px-3 py-2 text-sm text-blazer-900">
          Added {stats.total} line{stats.total === 1 ? "" : "s"}: <strong className="text-emerald-700">{stats.high} matched</strong>,{" "}
          <strong className="text-amber-700">{stats.medium} to review</strong>, <strong className="text-rose-700">{stats.none} not found</strong>.
        </p>
      )}
      {skipped.length > 0 && (
        <div className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm">
          <p className="mb-1 text-xs font-semibold text-slate-600">Skipped — looks like a job name or heading, not a part:</p>
          <ul className="space-y-1">
            {skipped.map((value) => (
              <li key={value} className="flex flex-wrap items-center gap-2">
                <span className="min-w-0 flex-1 truncate font-mono text-xs text-slate-700" title={value}>
                  {value}
                </span>
                <button
                  type="button"
                  onClick={() => addHeadingToNotes(value)}
                  className="inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-xs font-medium text-blazer-700 hover:bg-blazer-50"
                >
                  <NotebookPen className="size-3.5" aria-hidden /> Add to order notes
                </button>
                <button
                  type="button"
                  onClick={() => addHeadingAsItem(value)}
                  className="inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-xs font-medium text-blazer-700 hover:bg-blazer-50"
                >
                  <Plus className="size-3.5" aria-hidden /> Add as item
                </button>
                <button
                  type="button"
                  onClick={() => dropSkipped(value)}
                  className="rounded-md p-0.5 text-slate-400 hover:text-slate-700"
                  aria-label={`Dismiss ${value}`}
                >
                  <X className="size-3.5" aria-hidden />
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="border-t border-slate-200 pt-4">
        <h3 className="mb-2 text-sm font-semibold text-blazer-900">Quick add a single item</h3>
        <ProductSearchBox onSelect={(p) => addProduct(p, 1, "search")} />
      </div>
    </section>
  );
}
