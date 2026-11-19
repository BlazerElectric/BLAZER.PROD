"use client";

import { ClipboardList, Trash2, Undo2 } from "lucide-react";
import { useMemo, useState } from "react";
import { needsReview, useOrderStore } from "@/store/orderStore";
import type { OrderLine } from "@/lib/types";
import { BomLineCard } from "./BomLineCard";
import { lineState } from "./ConfidenceBadge";
import { SubmitPanel } from "./SubmitPanel";

export function useOrderCounts() {
  const lines = useOrderStore((s) => s.lines);
  return useMemo(() => {
    const counts = { total: lines.length, ready: 0, review: 0, unmatched: 0 };
    for (const l of lines) counts[lineState(l)]++;
    return counts;
  }, [lines]);
}

export function OrderPanel({ onClarify }: { onClarify: (line: OrderLine, number: number) => void }) {
  const lines = useOrderStore((s) => s.lines);
  const clearOrder = useOrderStore((s) => s.clearOrder);
  const removeLine = useOrderStore((s) => s.removeLine);
  const restoreLine = useOrderStore((s) => s.restoreLine);
  const counts = useOrderCounts();
  const [confirmClear, setConfirmClear] = useState(false);
  const [attentionOnly, setAttentionOnly] = useState(false);
  const [removed, setRemoved] = useState<{ line: OrderLine; index: number } | null>(null);
  const visible = lines.map((line, index) => ({ line, index })).filter(({ line }) => !attentionOnly || needsReview(line));

  return (
    <aside
      id="order-panel"
      aria-labelledby="order-heading"
      className="embed-auto-height min-w-0 scroll-mt-4 border-t-[3px] border-blazer-500 bg-white pt-4 lg:sticky lg:top-4 lg:flex lg:max-h-[calc(100vh-2rem)] lg:flex-col"
    >
      <div className="flex items-center justify-between gap-2">
        <h2 id="order-heading" className="flex items-center gap-2 text-lg font-semibold text-blazer-900">
          <ClipboardList className="size-5" aria-hidden /> Active order
          <span className="rounded-full bg-blazer-100 px-2 py-0.5 text-xs font-bold text-blazer-800 tabular-nums">{counts.total}</span>
        </h2>
        {lines.length > 0 &&
          (confirmClear ? (
            <span className="flex items-center gap-1 text-xs">
              <button
                type="button"
                onClick={() => {
                  clearOrder();
                  setRemoved(null);
                  setConfirmClear(false);
                }}
                className="rounded-md bg-rose-600 px-2 py-1 font-semibold text-white hover:bg-rose-700"
              >
                Clear all
              </button>
              <button type="button" onClick={() => setConfirmClear(false)} className="rounded-md px-2 py-1 text-slate-600 hover:bg-slate-100">
                Cancel
              </button>
            </span>
          ) : (
            <button
              type="button"
              onClick={() => setConfirmClear(true)}
              className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs text-slate-600 hover:bg-slate-100"
            >
              <Trash2 className="size-3.5" aria-hidden /> Clear
            </button>
          ))}
      </div>

      {lines.length > 0 && (
        <div className="mt-3 flex flex-wrap items-center gap-2 text-xs font-medium">
          <div role="group" aria-label="Order filter" className="inline-flex rounded-lg border border-slate-300 p-0.5">
            <button type="button" aria-pressed={!attentionOnly} onClick={() => setAttentionOnly(false)} className={`rounded-md px-2.5 py-1.5 ${!attentionOnly ? "bg-blazer-800 text-white" : "text-slate-600"}`}>All {counts.total}</button>
            <button type="button" aria-pressed={attentionOnly} onClick={() => setAttentionOnly(true)} className={`rounded-md px-2.5 py-1.5 ${attentionOnly ? "bg-amber-100 text-amber-950" : "text-slate-600"}`}>Needs attention {counts.review + counts.unmatched}</button>
          </div>
          <span className="text-emerald-800">{counts.ready} ready</span>
        </div>
      )}

      {removed && <div role="status" className="mt-3 flex items-center justify-between gap-2 border-y border-slate-200 py-2 text-xs text-slate-600"><span className="min-w-0 truncate">Removed {removed.line.rawText}</span><button type="button" onClick={() => { restoreLine(removed.line, removed.index); setRemoved(null); }} className="inline-flex shrink-0 items-center gap-1 font-semibold text-blazer-800"><Undo2 className="size-4" aria-hidden /> Undo remove</button></div>}

      <div className="mt-3 min-h-0 flex-1 lg:overflow-y-auto lg:pr-1">
        {lines.length === 0 ? (
          <div className="rounded-xl border-2 border-dashed border-slate-200 p-6 text-center">
            <span className="mx-auto flex size-12 items-center justify-center rounded-full bg-blazer-50">
              <ClipboardList className="size-6 text-blazer-600" aria-hidden />
            </span>
            <p className="mt-3 text-sm font-semibold text-blazer-950">Your order is empty</p>
          </div>
        ) : (
          <ul className="space-y-3">
            {visible.map(({ line, index }) => (
              <BomLineCard key={line.id} line={line} number={index + 1} onClarify={() => onClarify(line, index + 1)} onRemove={() => { setRemoved({ line, index }); removeLine(line.id); }} />
            ))}
            {visible.length === 0 && <li className="py-6 text-center text-sm text-emerald-800">All items are ready for review at checkout.</li>}
          </ul>
        )}
      </div>

      {lines.length > 0 && (
        <div className="mt-3">
          <SubmitPanel />
        </div>
      )}
    </aside>
  );
}
