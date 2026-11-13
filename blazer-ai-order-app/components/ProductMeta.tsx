import { Flame } from "lucide-react";
import type { ProductSummary } from "@/lib/types";

export function ProductMeta({ product, compact = false }: { product: ProductSummary; compact?: boolean }) {
  const mpn = product.mpn && product.mpn.toUpperCase() !== "N/A" ? product.mpn : "";
  return (
    <div className={`flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-slate-600 ${compact ? "" : "mt-1"}`}>
      <span className="rounded bg-blazer-100 px-1.5 py-0.5 font-mono font-semibold text-blazer-800">#{product.sku}</span>
      {mpn && <span className="font-mono">MPN {mpn}</span>}
      {product.brand && <span>{product.brand}</span>}
      {!compact && product.subcategory && <span className="text-slate-500">· {product.subcategory}</span>}
      {product.rank > 0 && product.rank <= 250 && (
        <span className="inline-flex items-center gap-0.5 font-medium text-orange-700">
          <Flame className="size-3" aria-hidden /> Top seller
        </span>
      )}
    </div>
  );
}
