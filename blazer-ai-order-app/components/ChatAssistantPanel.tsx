"use client";

import { Bot, Loader2, PlusCircle, RotateCcw, SendHorizonal, Sparkles, User } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { sendChat } from "@/lib/api";
import type { ChatSuggestion } from "@/lib/types";
import { useOrderStore, type ChatEntry } from "@/store/orderStore";
import { ProductMeta } from "./ProductMeta";

const STARTERS = [
  "What size ground wire do I need for a 200A subpanel?",
  "Build me a rough-in kit for 10 kitchen receptacles on 20A circuits",
  "Material list for 100ft of 3/4\" EMT with 4 boxes",
  "What breaker and wire for a 50A 240V EV charger?",
];

function SuggestionList({ entry }: { entry: ChatEntry }) {
  const addLines = useOrderStore((s) => s.addLines);
  const suggestions = entry.suggestions ?? [];
  const [selected, setSelected] = useState<Set<number>>(
    () => new Set(suggestions.map((s, i) => (s.match.product ? i : -1)).filter((i) => i >= 0)),
  );
  const [added, setAdded] = useState(false);

  const add = (items: ChatSuggestion[]) => {
    addLines(
      items.map((s) => ({
        source: "chat" as const,
        rawText: s.description,
        quantity: s.quantity,
        unit: s.unit,
        comment: s.note,
        match: s.match,
        reason: [s.reason, s.match.note].filter(Boolean).join(" · ") || undefined,
      })),
    );
    setAdded(true);
  };

  return (
    <div className="mt-3 rounded-lg border border-blazer-200 bg-white">
      <p className="border-b border-blazer-100 px-3 py-2 text-xs font-semibold tracking-wide text-blazer-800 uppercase">
        Suggested bill of materials
      </p>
      <ul className="divide-y divide-slate-100">
        {suggestions.map((s, i) => (
          <li key={i} className="flex items-start gap-2 px-3 py-2">
            <input
              type="checkbox"
              aria-label={`Include ${s.description}`}
              checked={selected.has(i)}
              onChange={(e) =>
                setSelected((prev) => {
                  const next = new Set(prev);
                  if (e.target.checked) next.add(i);
                  else next.delete(i);
                  return next;
                })
              }
              className="mt-1 accent-blazer-700"
            />
            <div className="min-w-0 flex-1">
              <p className="text-sm text-slate-900">
                <span className="font-semibold tabular-nums">
                  {s.quantity} {s.unit}
                </span>{" "}
                · {s.match.product ? s.match.product.title : s.description}
              </p>
              {s.match.product ? (
                <ProductMeta product={s.match.product} compact />
              ) : (
                <p className="text-xs text-rose-700">No catalog match — will be added for manual sourcing.</p>
              )}
              {(s.reason || s.note) && (
                <p className="mt-0.5 text-xs text-slate-500">{[s.reason, s.note && `Note: ${s.note}`].filter(Boolean).join(" · ")}</p>
              )}
              {s.match.product && s.match.confidence !== "high" && (
                <p className="text-xs text-amber-700">Best guess — you can review alternatives in the order.</p>
              )}
            </div>
          </li>
        ))}
      </ul>
      <div className="flex justify-end border-t border-blazer-100 px-3 py-2">
        <button
          type="button"
          disabled={selected.size === 0 || added}
          onClick={() => add(suggestions.filter((_, i) => selected.has(i)))}
          className="inline-flex items-center gap-1.5 rounded-lg bg-blazer-800 px-3 py-1.5 text-sm font-semibold text-white hover:bg-blazer-700 disabled:opacity-50"
        >
          <PlusCircle className="size-4" aria-hidden />
          {added ? "Added to order" : `Add ${selected.size} to order`}
        </button>
      </div>
    </div>
  );
}

export function ChatAssistantPanel() {
  const chat = useOrderStore((s) => s.chat);
  const lines = useOrderStore((s) => s.lines);
  const appendChat = useOrderStore((s) => s.appendChat);
  const clearChat = useOrderStore((s) => s.clearChat);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scroller = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: "smooth" });
  }, [chat.length, loading]);

  const send = async (text: string) => {
    const content = text.trim();
    if (!content || loading) return;
    setInput("");
    setError(null);
    appendChat({ role: "user", content });
    setLoading(true);
    try {
      const history = [...useOrderStore.getState().chat].map(({ role, content: c }) => ({ role, content: c }));
      const cartSummary = lines
        .filter((l) => l.product)
        .slice(0, 60)
        .map((l) => `${l.quantity} ${l.unit} #${l.product!.sku} ${l.product!.title}`)
        .join("\n");
      const res = await sendChat(history, cartSummary);
      appendChat({ role: "assistant", content: res.reply, suggestions: res.suggestions });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <section aria-labelledby="chat-heading" className="flex flex-col gap-4">
      <div className="flex items-start justify-between gap-2">
        <div>
          <h2 id="chat-heading" className="text-lg font-semibold text-blazer-900">
            AI trade assistant
          </h2>
          <p className="text-sm text-slate-600">
            Ask code and sizing questions or describe the job. Suggestions are matched to real Blazer SKUs before they
            reach your order.
          </p>
        </div>
        {chat.length > 0 && (
          <button
            type="button"
            onClick={clearChat}
            className="inline-flex shrink-0 items-center gap-1 rounded-md px-2 py-1 text-xs text-slate-600 hover:bg-slate-100"
          >
            <RotateCcw className="size-3.5" aria-hidden /> New chat
          </button>
        )}
      </div>

      <div
        ref={scroller}
        className="max-h-[60vh] min-h-64 space-y-4 overflow-y-auto rounded-xl border border-slate-200 bg-slate-50 p-4"
        aria-live="polite"
      >
        {chat.length === 0 && (
          <div className="space-y-3">
            <p className="flex items-center gap-2 text-sm font-medium text-blazer-900">
              <Sparkles className="size-4 text-blazer-600" aria-hidden /> Try asking:
            </p>
            <div className="flex flex-wrap gap-2">
              {STARTERS.map((s) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => void send(s)}
                  className="rounded-full border border-blazer-200 bg-white px-3 py-1.5 text-left text-sm text-blazer-800 hover:border-blazer-400 hover:bg-blazer-50"
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}
        {chat.map((m) => (
          <div key={m.id} className={`flex gap-2 ${m.role === "user" ? "flex-row-reverse" : ""}`}>
            <span
              className={`mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full ${
                m.role === "user" ? "bg-slate-200 text-slate-700" : "bg-blazer-800 text-white"
              }`}
              aria-hidden
            >
              {m.role === "user" ? <User className="size-4" /> : <Bot className="size-4" />}
            </span>
            <div
              className={`max-w-[85%] rounded-2xl px-4 py-2.5 text-sm leading-relaxed whitespace-pre-wrap shadow-sm ${
                m.role === "user" ? "bg-blazer-800 text-white" : "bg-white text-slate-800 ring-1 ring-slate-200"
              }`}
            >
              <span className="sr-only">{m.role === "user" ? "You:" : "Assistant:"}</span>
              {m.content}
              {m.role === "assistant" && m.suggestions && m.suggestions.length > 0 && <SuggestionList entry={m} />}
            </div>
          </div>
        ))}
        {loading && (
          <p className="flex items-center gap-2 text-sm text-slate-500">
            <Loader2 className="size-4 animate-spin" aria-hidden /> Checking the catalog…
          </p>
        )}
      </div>

      {error && <p className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-800">{error}</p>}

      <form
        onSubmit={(e) => {
          e.preventDefault();
          void send(input);
        }}
        className="flex items-end gap-2"
      >
        <label htmlFor="chat-input" className="sr-only">
          Message the assistant
        </label>
        <textarea
          id="chat-input"
          rows={2}
          value={input}
          maxLength={4000}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void send(input);
            }
          }}
          placeholder="e.g. What size ground wire do I need for a 200A subpanel?"
          className="min-w-0 flex-1 resize-none rounded-xl border border-slate-300 px-3 py-2 text-sm shadow-sm focus:border-blazer-600"
        />
        <button
          type="submit"
          disabled={!input.trim() || loading}
          className="inline-flex h-11 items-center gap-1.5 rounded-xl bg-blazer-800 px-4 text-sm font-semibold text-white hover:bg-blazer-700 disabled:opacity-50"
        >
          <SendHorizonal className="size-4" aria-hidden />
          <span className="sr-only sm:not-sr-only">Send</span>
        </button>
      </form>
      <p className="text-xs text-slate-500">
        AI guidance is a starting point — verify against the adopted NEC edition and your local AHJ.
      </p>
    </section>
  );
}
