"use client";

import { ClipboardList, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";
import { useOrderStore } from "@/store/orderStore";
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

export function OrderPanel() {
  const lines = useOrderStore((s) => s.lines);
  const clearOrder = useOrderStore((s) => s.clearOrder);
  const counts = useOrderCounts();
  const [confirmClear, setConfirmClear] = useState(false);

  return (
    <aside
      id="order-panel"
      aria-labelledby="order-heading"
      className="embed-auto-height scroll-mt-4 rounded-2xl border border-smoke-700 border-t-[3px] border-t-blazer-500 bg-white p-4 shadow-[0_8px_24px_rgba(23,45,97,0.06)] lg:sticky lg:top-4 lg:flex lg:max-h-[calc(100vh-2rem)] lg:flex-col"
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
        <div className="mt-2 flex flex-wrap gap-2 text-xs font-medium">
          <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-emerald-800">{counts.ready} ready</span>
          <span className="rounded-full bg-amber-50 px-2 py-0.5 text-amber-800">{counts.review} to review</span>
          <span className="rounded-full bg-rose-50 px-2 py-0.5 text-rose-800">{counts.unmatched} not found</span>
        </div>
      )}

      <div className="mt-3 min-h-0 flex-1 lg:overflow-y-auto lg:pr-1">
        {lines.length === 0 ? (
          <div className="rounded-xl border-2 border-dashed border-slate-200 p-6 text-center">
            <span className="mx-auto flex size-12 items-center justify-center rounded-full bg-blazer-50">
              <ClipboardList className="size-6 text-blazer-600" aria-hidden />
            </span>
            <p className="mt-3 text-sm font-semibold text-blazer-950">Your order is empty</p>
            <p className="text-xs text-slate-500">Paste a material list or ask the AI assistant to build one.</p>
          </div>
        ) : (
          <ul className="space-y-3">
            {lines.map((line) => (
              <BomLineCard key={line.id} line={line} />
            ))}
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
