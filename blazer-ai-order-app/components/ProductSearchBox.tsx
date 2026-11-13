"use client";

import { Loader2, Search } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { searchProducts } from "@/lib/api";
import type { ProductSummary, SearchHit } from "@/lib/types";
import { ProductMeta } from "./ProductMeta";

interface Props {
  onSelect: (product: ProductSummary) => void;
  placeholder?: string;
  initialQuery?: string;
  autoFocus?: boolean;
  size?: "md" | "sm";
}

export function ProductSearchBox({ onSelect, placeholder = "Search SKU, part #, or description…", initialQuery = "", autoFocus, size = "md" }: Props) {
  const [query, setQuery] = useState(initialQuery);
  const [fetched, setResults] = useState<SearchHit[]>([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const [loading, setLoading] = useState(false);
  const listId = useId();
  const results = query.trim().length < 2 ? [] : fetched;
  const wrapper = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const q = query.trim();
    if (q.length < 2) return;
    const controller = new AbortController();
    const timer = window.setTimeout(async () => {
      setLoading(true);
      try {
        setResults(await searchProducts(q, controller.signal));
        setActive(0);
      } catch (error) {
        if ((error as Error).name !== "AbortError") setResults([]);
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }, 140);
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [query]);

  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      if (wrapper.current && !wrapper.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, []);

  const choose = (hit: SearchHit | undefined) => {
    if (!hit) return;
    onSelect(hit.product);
    setQuery("");
    setResults([]);
    setOpen(false);
  };

  const showList = open && query.trim().length >= 2;
  const pad = size === "sm" ? "py-1.5 text-sm" : "py-2.5";

  return (
    <div ref={wrapper} className="relative">
      <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-slate-400" aria-hidden />
      <input
        type="search"
        role="combobox"
        aria-expanded={showList}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-label={placeholder}
        autoFocus={autoFocus}
        value={query}
        placeholder={placeholder}
        onChange={(e) => {
          setQuery(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") {
            e.preventDefault();
            setActive((a) => Math.min(a + 1, results.length - 1));
          } else if (e.key === "ArrowUp") {
            e.preventDefault();
            setActive((a) => Math.max(a - 1, 0));
          } else if (e.key === "Enter") {
            e.preventDefault();
            choose(results[active]);
          } else if (e.key === "Escape") {
            setOpen(false);
          }
        }}
        className={`w-full rounded-lg border border-slate-300 bg-white pr-9 pl-9 shadow-sm placeholder:text-slate-400 focus:border-blazer-600 ${pad}`}
      />
      {loading && <Loader2 className="absolute top-1/2 right-3 size-4 -translate-y-1/2 animate-spin text-blazer-600" aria-hidden />}
      {showList && (
        <ul
          id={listId}
          role="listbox"
          className="absolute z-30 mt-1 max-h-80 w-full overflow-auto rounded-lg border border-slate-200 bg-white py-1 shadow-xl"
        >
          {results.length === 0 && !loading && <li className="px-3 py-2 text-sm text-slate-500">No catalog matches.</li>}
          {results.map((hit, i) => (
            <li
              key={hit.product.sku}
              role="option"
              aria-selected={i === active}
              onMouseEnter={() => setActive(i)}
              onMouseDown={(e) => {
                e.preventDefault();
                choose(hit);
              }}
              className={`cursor-pointer px-3 py-2 ${i === active ? "bg-blazer-50" : ""}`}
            >
              <p className="text-sm leading-snug text-slate-900">{hit.product.title}</p>
              <ProductMeta product={hit.product} compact />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
