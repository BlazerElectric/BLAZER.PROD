"use client";

import { Bot, Check, Loader2, RotateCcw, User } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useOrderStore, type ChatEntry } from "@/store/orderStore";
import { ProductMeta } from "./ProductMeta";

function SuggestionList({ entry }: { entry: ChatEntry }) {
  const lines = useOrderStore((state) => state.lines);
  const applySuggestions = useOrderStore((state) => state.applySuggestions);
  const suggestions = entry.suggestions ?? [];
  const [selected, setSelected] = useState<Set<number>>(() => new Set());
  const applied = new Set(entry.appliedSuggestions ?? []);
  const applicable = suggestions.map((suggestion, index) => ({ suggestion, index }))
    .filter(({ suggestion, index }) => !applied.has(index) && (!suggestion.lineId || lines.some((line) => line.id === suggestion.lineId)));
  const selectedCount = applicable.filter(({ index }) => selected.has(index)).length;

  return (
    <div className="mt-3 border-t border-slate-200 pt-2">
      <p className="text-xs font-semibold text-blazer-900">Proposed order changes</p>
      <ul className="divide-y divide-slate-200">
        {suggestions.map((suggestion, index) => {
          const target = suggestion.lineId ? lines.findIndex((line) => line.id === suggestion.lineId) : -1;
          const unavailable = Boolean(suggestion.lineId && target < 0);
          return (
            <li key={index} className="flex items-start gap-2 py-3">
              <input type="checkbox" aria-label={`Apply ${suggestion.description}`} checked={applied.has(index) || selected.has(index)} disabled={applied.has(index) || unavailable}
                onChange={(event) => setSelected((previous) => {
                  const next = new Set(previous);
                  if (event.target.checked) next.add(index);
                  else next.delete(index);
                  return next;
                })} className="mt-1 accent-blazer-700" />
              <div className="min-w-0 flex-1 space-y-1">
                <p className="text-xs font-semibold text-blazer-800">{applied.has(index) ? "Applied" : unavailable ? "Original line removed" : suggestion.lineId ? `Update line ${target + 1}` : "Add item"}</p>
                <p className="text-sm font-medium">{suggestion.quantity} {suggestion.unit} · {suggestion.description}</p>
                {suggestion.match.product ? <ProductMeta product={suggestion.match.product} compact /> : <p className="text-xs text-rose-700">No catalog match</p>}
                {suggestion.match.product && <p className="text-xs text-slate-700">{suggestion.match.product.title}</p>}
                {(suggestion.note || suggestion.reason || suggestion.match.note) && <p className="text-xs text-slate-600">{[suggestion.note, suggestion.reason, suggestion.match.note].filter(Boolean).join(" · ")}</p>}
              </div>
            </li>
          );
        })}
      </ul>
      <button type="button" disabled={selectedCount === 0} onClick={() => applySuggestions(entry.id, [...selected])} className="inline-flex items-center gap-1.5 rounded-lg bg-blazer-800 px-3 py-2 text-sm font-semibold text-white hover:bg-blazer-700 disabled:opacity-50">
        <Check className="size-4" aria-hidden /> Apply {selectedCount || "selected"} change{selectedCount === 1 ? "" : "s"}
      </button>
    </div>
  );
}

export function ChatAssistantPanel({ loading = false }: { loading?: boolean }) {
  const chat = useOrderStore((state) => state.chat);
  const clearChat = useOrderStore((state) => state.clearChat);
  const scroller = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: "smooth" });
  }, [chat.length, loading]);

  if (!chat.length && !loading) return null;

  return (
    <section aria-label="Order conversation" className="space-y-2">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-blazer-900">Conversation</h3>
        <button type="button" title="Clear conversation" aria-label="Clear conversation" disabled={loading} onClick={clearChat} className="rounded-md p-2 text-slate-500 hover:bg-slate-100 disabled:opacity-50"><RotateCcw className="size-4" aria-hidden /></button>
      </div>
      <div ref={scroller} role="log" aria-label="Order messages" aria-busy={loading} className="max-h-[45vh] space-y-4 overflow-y-auto overscroll-contain border-y border-slate-200 py-3">
        {chat.map((message) => (
          <div key={message.id} className="flex gap-2">
            <span className={`mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full ${message.role === "user" ? "bg-slate-100 text-slate-600" : "bg-blazer-800 text-white"}`} aria-hidden>{message.role === "user" ? <User className="size-4" /> : <Bot className="size-4" />}</span>
            <div className="min-w-0 flex-1 break-words text-sm leading-relaxed text-slate-800">
              <p className="mb-1 text-xs font-semibold text-slate-500">{message.role === "user" ? "You" : "Blazer"}</p>
              <p className="whitespace-pre-wrap">{message.content}</p>
              {message.suggestions?.length ? <SuggestionList entry={message} /> : null}
            </div>
          </div>
        ))}
        {loading && <p className="flex items-center gap-2 text-sm text-slate-600"><Loader2 className="size-4 animate-spin" aria-hidden /> Checking your request…</p>}
      </div>
    </section>
  );
}