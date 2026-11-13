"use client";

import { Loader2, Plus } from "lucide-react";
import { useEffect, useState } from "react";
import { fetchRelated } from "@/lib/api";
import type { ProductSummary } from "@/lib/types";
import { useOrderStore } from "@/store/orderStore";

/** "Complete the job" — Carousel 1 accessories for a matched product. */
export function AccessoryRail({ sku }: { sku: string }) {
  const addProduct = useOrderStore((s) => s.addProduct);
  const [items, setItems] = useState<ProductSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [added, setAdded] = useState<Set<string>>(new Set());

  useEffect(() => {
    let cancelled = false;
    fetchRelated(sku)
      .then((r) => !cancelled && setItems(r.accessories))
      .catch((e: Error) => !cancelled && setError(e.message));
    return () => {
      cancelled = true;
    };
  }, [sku]);

  if (error) return <p className="text-xs text-rose-700">{error}</p>;
  if (!items) {
    return (
      <p className="flex items-center gap-1 text-xs text-slate-500">
        <Loader2 className="size-3 animate-spin" aria-hidden /> Loading accessories…
      </p>
    );
  }
  if (items.length === 0) return <p className="text-xs text-slate-500">No verified accessories for this item.</p>;

  return (
    <ul className="grid gap-1.5 sm:grid-cols-2">
      {items.map((p) => (
        <li key={p.sku} className="flex items-start gap-2 rounded-lg border border-slate-200 bg-slate-50 p-2">
          <div className="min-w-0 flex-1">
            <p className="line-clamp-2 text-xs leading-snug text-slate-800">{p.title}</p>
            <p className="mt-0.5 font-mono text-[11px] text-blazer-700">#{p.sku}</p>
          </div>
          <button
            type="button"
            onClick={() => {
              addProduct(p, 1, "search");
              setAdded((s) => new Set(s).add(p.sku));
            }}
            className="inline-flex shrink-0 items-center gap-0.5 rounded-md bg-white px-2 py-1 text-xs font-semibold text-blazer-700 ring-1 ring-blazer-200 hover:bg-blazer-50"
            aria-label={`Add ${p.title} to order`}
          >
            <Plus className="size-3" aria-hidden />
            {added.has(p.sku) ? "Added" : "Add"}
          </button>
        </li>
      ))}
    </ul>
  );
}
