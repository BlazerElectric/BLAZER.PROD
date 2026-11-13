"use client";

import { ChevronDown } from "lucide-react";
import type { ProductSummary } from "@/lib/types";

interface Props {
  id: string;
  current: ProductSummary | null;
  alternatives: ProductSummary[];
  similar: ProductSummary[];
  onSelect: (product: ProductSummary) => void;
}

const label = (p: ProductSummary) => {
  const title = p.title.length > 90 ? `${p.title.slice(0, 87)}…` : p.title;
  return `#${p.sku} — ${title}`;
};

/** Native select keeps this fast and accessible on contractor phones. */
export function AlternativeSelect({ id, current, alternatives, similar, onSelect }: Props) {
  const seen = new Set<string>(current ? [current.sku] : []);
  const others = alternatives.filter((p) => !seen.has(p.sku) && seen.add(p.sku));
  const subs = similar.filter((p) => !seen.has(p.sku) && seen.add(p.sku));
  const all = [current, ...others, ...subs].filter((p): p is ProductSummary => Boolean(p));
  if (all.length <= 1) return null;

  return (
    <div className="relative">
      <label htmlFor={id} className="sr-only">
        Choose a different product
      </label>
      <select
        id={id}
        value={current?.sku ?? ""}
        onChange={(e) => {
          const picked = all.find((p) => p.sku === e.target.value);
          if (picked) onSelect(picked);
        }}
        className="w-full appearance-none rounded-lg border border-amber-300 bg-amber-50/60 py-2 pr-9 pl-3 text-sm text-slate-900 shadow-sm hover:border-amber-400 focus:border-blazer-600"
      >
        {!current && <option value="">Select a product…</option>}
        {current && (
          <optgroup label="Current match">
            <option value={current.sku}>{label(current)}</option>
          </optgroup>
        )}
        {others.length > 0 && (
          <optgroup label="Other matches for your text">
            {others.map((p) => (
              <option key={p.sku} value={p.sku}>
                {label(p)}
              </option>
            ))}
          </optgroup>
        )}
        {subs.length > 0 && (
          <optgroup label="Similar / alternative products">
            {subs.map((p) => (
              <option key={p.sku} value={p.sku}>
                {label(p)}
              </option>
            ))}
          </optgroup>
        )}
      </select>
      <ChevronDown className="pointer-events-none absolute top-1/2 right-3 size-4 -translate-y-1/2 text-amber-700" aria-hidden />
    </div>
  );
}
