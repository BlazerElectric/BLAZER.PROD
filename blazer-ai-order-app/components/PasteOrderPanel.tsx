"use client";

import { Eraser, FileText, Loader2, NotebookPen, Plus, Wand2, X } from "lucide-react";
import { useState } from "react";
import { parseOrder } from "@/lib/api";
import { useOrderStore } from "@/store/orderStore";
import { ProductSearchBox } from "./ProductSearchBox";

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

export function PasteOrderPanel() {
  const addLines = useOrderStore((s) => s.addLines);
  const addProduct = useOrderStore((s) => s.addProduct);
  const clearOrder = useOrderStore((s) => s.clearOrder);
  const shippingInstructions = useOrderStore((s) => s.shippingInstructions);
  const setShippingInstructions = useOrderStore((s) => s.setShippingInstructions);
  const [skipped, setSkipped] = useState<string[]>([]);
  const [text, setText] = useState("");
  const [replace, setReplace] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stats, setStats] = useState<Stats | null>(null);

  const build = async () => {
    if (!text.trim() || loading) return;
    setLoading(true);
    setError(null);
    try {
      const { lines, skipped: headings } = await parseOrder(text);
      if (replace) clearOrder();
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
      if (lines.length || headings.length) setText("");
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
          Type or paste your order
        </h2>
        <p className="text-sm text-slate-600">
          Material lists, panel schedules, texts from the foreman — one item per line. Quantities, feet, and cut notes
          like <span className="font-mono text-xs">5 cuts @ 50ft</span> are picked up automatically.
        </p>
      </div>

      <div className="rounded-xl border border-slate-300 bg-white shadow-sm focus-within:border-blazer-600 focus-within:ring-2 focus-within:ring-blazer-100">
        <label htmlFor="order-text" className="sr-only">
          Order text
        </label>
        <textarea
          id="order-text"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
              e.preventDefault();
              void build();
            }
          }}
          rows={12}
          spellCheck={false}
          placeholder={"20 sticks 3/4 EMT\n50 3/4 emt sscr conn\n500' #12 THHN blk str\n10 QO120"}
          className="block w-full resize-y rounded-t-xl border-0 bg-transparent p-4 font-mono text-sm leading-6 outline-none placeholder:text-slate-400"
        />
        <div className="flex flex-wrap items-center gap-2 border-t border-slate-200 bg-slate-50 px-3 py-2">
          <button
            type="button"
            onClick={() => setText(EXAMPLE)}
            className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium text-slate-600 hover:bg-white"
          >
            <FileText className="size-3.5" aria-hidden /> Load example
          </button>
          <button
            type="button"
            onClick={() => setText("")}
            disabled={!text}
            className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium text-slate-600 hover:bg-white disabled:opacity-40"
          >
            <Eraser className="size-3.5" aria-hidden /> Clear
          </button>
          <label className="ml-auto inline-flex items-center gap-1.5 text-xs text-slate-600">
            <input type="checkbox" checked={replace} onChange={(e) => setReplace(e.target.checked)} className="accent-blazer-700" />
            Replace current order
          </label>
          <button
            type="button"
            onClick={() => void build()}
            disabled={!text.trim() || loading}
            className="inline-flex items-center gap-1.5 rounded-lg bg-blazer-800 px-4 py-2 text-sm font-semibold text-white shadow hover:bg-blazer-700 disabled:opacity-50"
          >
            {loading ? <Loader2 className="size-4 animate-spin" aria-hidden /> : <Wand2 className="size-4" aria-hidden />}
            Build bill of materials
          </button>
        </div>
      </div>
      <p className="text-xs text-slate-500">Tip: press Ctrl/⌘ + Enter to build.</p>

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

      <div className="rounded-xl border border-slate-200 bg-slate-50 p-4">
        <h3 className="mb-2 text-sm font-semibold text-blazer-900">Quick add a single item</h3>
        <ProductSearchBox onSelect={(p) => addProduct(p, 1, "search")} />
      </div>
    </section>
  );
}
