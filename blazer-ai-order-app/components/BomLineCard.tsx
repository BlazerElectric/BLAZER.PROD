"use client";

import { Bot, Check, ChevronDown, ClipboardPaste, MessageSquareText, Minus, Pencil, Plus, Search, Trash2, Wrench } from "lucide-react";
import { useState } from "react";
import type { OrderLine } from "@/lib/types";
import { needsReview, useOrderStore } from "@/store/orderStore";
import { AccessoryRail } from "./AccessoryRail";
import { AlternativeSelect } from "./AlternativeSelect";
import { ConfidenceBadge, STATE_BORDER, lineState } from "./ConfidenceBadge";
import { ProductMeta } from "./ProductMeta";
import { ProductSearchBox } from "./ProductSearchBox";

const SOURCE: Record<OrderLine["source"], { label: string; Icon: typeof Bot }> = {
  paste: { label: "Pasted", Icon: ClipboardPaste },
  chat: { label: "AI", Icon: Bot },
  search: { label: "Search", Icon: Search },
};

export function BomLineCard({ line }: { line: OrderLine }) {
  const updateLine = useOrderStore((s) => s.updateLine);
  const selectProduct = useOrderStore((s) => s.selectProduct);
  const confirmLine = useOrderStore((s) => s.confirmLine);
  const removeLine = useOrderStore((s) => s.removeLine);
  const [showAccessories, setShowAccessories] = useState(false);
  const [changing, setChanging] = useState(false);
  const reviewing = needsReview(line);
  const pick = (p: Parameters<typeof selectProduct>[1]) => {
    selectProduct(line.id, p);
    setChanging(false);
  };

  const state = lineState(line);
  const { label: sourceLabel, Icon: SourceIcon } = SOURCE[line.source];
  const showRaw = line.source !== "search" && line.rawText && line.rawText !== line.product?.title;

  return (
    <li className={`rounded-xl border border-l-4 border-slate-200 bg-white p-3 shadow-sm ${STATE_BORDER[state]}`}>
      <div className="flex items-start gap-2">
        <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
          <ConfidenceBadge line={line} />
          <span className="inline-flex items-center gap-1 text-[11px] font-medium tracking-wide text-slate-500 uppercase">
            <SourceIcon className="size-3" aria-hidden /> {sourceLabel}
          </span>
          {showRaw && (
            <span className="min-w-0 truncate text-xs text-slate-500" title={line.rawText}>
              “{line.rawText}”
            </span>
          )}
        </div>
        <button
          type="button"
          onClick={() => removeLine(line.id)}
          className="rounded-md p-1 text-slate-400 hover:bg-rose-50 hover:text-rose-700"
          aria-label="Remove line"
        >
          <Trash2 className="size-4" aria-hidden />
        </button>
      </div>

      {line.product ? (
        <div className="mt-2 flex items-start gap-2">
          <div className="min-w-0 flex-1">
            <p className="text-sm leading-snug font-medium text-slate-900">{line.product.title}</p>
            <ProductMeta product={line.product} />
            {line.reason && <p className="mt-1 text-xs text-slate-500 italic">{line.reason}</p>}
          </div>
          {!reviewing && (
            <button
              type="button"
              onClick={() => setChanging((v) => !v)}
              aria-expanded={changing}
              className="inline-flex shrink-0 items-center gap-1 rounded-md border border-slate-300 px-2 py-1 text-xs font-semibold text-slate-700 hover:border-blazer-600 hover:text-blazer-800"
            >
              <Pencil className="size-3.5" aria-hidden /> {changing ? "Done" : "Change"}
            </button>
          )}
        </div>
      ) : (
        <div className="mt-2 space-y-2">
          <label className="block text-xs font-medium text-slate-600" htmlFor={`manual-${line.id}`}>
            Not in catalog — describe it for the Blazer counter
          </label>
          <input
            id={`manual-${line.id}`}
            value={line.manualText}
            onChange={(e) => updateLine(line.id, { manualText: e.target.value })}
            className="w-full rounded-lg border border-rose-200 bg-rose-50/40 px-3 py-2 text-sm focus:border-blazer-600"
            placeholder="e.g. 2-1/2 rigid 90 w/ coupling"
          />
          <ProductSearchBox size="sm" placeholder="…or search the catalog to match it" onSelect={pick} />
          {line.alternatives.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              <span className="text-xs text-slate-500">Did you mean:</span>
              {line.alternatives.slice(0, 4).map((p) => (
                <button
                  key={p.sku}
                  type="button"
                  onClick={() => pick(p)}
                  className="max-w-full truncate rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-700 hover:bg-blazer-100"
                  title={p.title}
                >
                  {p.title.slice(0, 48)}
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      {line.product && (reviewing || changing) && (
        <div className="mt-2 space-y-2">
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
            <div className="min-w-0 flex-1">
              <AlternativeSelect
                id={`alt-${line.id}`}
                current={line.product}
                alternatives={line.alternatives}
                similar={line.similar}
                onSelect={pick}
              />
            </div>
            {reviewing && (
              <button
                type="button"
                onClick={() => confirmLine(line.id)}
                className="inline-flex items-center justify-center gap-1 rounded-lg bg-emerald-600 px-3 py-2 text-sm font-semibold text-white hover:bg-emerald-700"
              >
                <Check className="size-4" aria-hidden /> Looks right
              </button>
            )}
          </div>
          <ProductSearchBox size="sm" placeholder="Not it? Search the catalog for the right part…" onSelect={pick} />
        </div>
      )}

      <div className="mt-3 flex flex-col gap-2 sm:flex-row sm:items-center">
        <div className="flex items-center gap-1">
          <button
            type="button"
            aria-label="Decrease quantity"
            onClick={() => updateLine(line.id, { quantity: line.quantity - 1 })}
            className="rounded-md border border-slate-300 p-1.5 text-slate-600 hover:bg-slate-50"
          >
            <Minus className="size-3.5" aria-hidden />
          </button>
          <input
            type="number"
            inputMode="numeric"
            min={1}
            max={99999}
            aria-label="Quantity"
            value={line.quantity}
            onChange={(e) => updateLine(line.id, { quantity: Number(e.target.value) })}
            className="w-20 rounded-md border border-slate-300 px-2 py-1 text-center text-sm font-semibold tabular-nums"
          />
          <button
            type="button"
            aria-label="Increase quantity"
            onClick={() => updateLine(line.id, { quantity: line.quantity + 1 })}
            className="rounded-md border border-slate-300 p-1.5 text-slate-600 hover:bg-slate-50"
          >
            <Plus className="size-3.5" aria-hidden />
          </button>
          <span className="ml-1 w-10 text-xs text-slate-500 uppercase">{line.unit}</span>
        </div>
        <div className="relative min-w-0 flex-1">
          <MessageSquareText className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-slate-400" aria-hidden />
          <input
            aria-label="Line comment"
            value={line.comment}
            maxLength={250}
            onChange={(e) => updateLine(line.id, { comment: e.target.value })}
            placeholder='Line note, e.g. "5 cuts @ 50ft"'
            className="w-full rounded-md border border-slate-300 py-1.5 pr-2 pl-8 text-sm placeholder:text-slate-400 focus:border-blazer-600"
          />
        </div>
      </div>

      {line.product && (
        <div className="mt-2">
          <button
            type="button"
            onClick={() => setShowAccessories((v) => !v)}
            aria-expanded={showAccessories}
            className="inline-flex items-center gap-1 text-xs font-semibold text-blazer-700 hover:text-blazer-900"
          >
            <Wrench className="size-3.5" aria-hidden /> Complete the job
            <ChevronDown className={`size-3.5 transition-transform ${showAccessories ? "rotate-180" : ""}`} aria-hidden />
          </button>
          {showAccessories && (
            <div className="mt-2">
              <AccessoryRail sku={line.product.sku} />
            </div>
          )}
        </div>
      )}
    </li>
  );
}
