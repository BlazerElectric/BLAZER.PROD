"use client";

import { Bot, ClipboardPaste } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import type { Health } from "@/lib/api";
import { useOrderStore } from "@/store/orderStore";
import { ChatAssistantPanel } from "./ChatAssistantPanel";
import { EmbedBridge } from "./EmbedBridge";
import { Header } from "./Header";
import { OrderPanel, useOrderCounts } from "./OrderPanel";
import { PasteOrderPanel } from "./PasteOrderPanel";

type Mode = "paste" | "chat";

const TABS: { id: Mode; label: string; hint: string; Icon: typeof Bot }[] = [
  { id: "paste", label: "Paste Order", hint: "Type or paste a material list", Icon: ClipboardPaste },
  { id: "chat", label: "AI Assistant", hint: "Ask trade questions, build a BOM", Icon: Bot },
];

export function OrderDesk() {
  const [mode, setMode] = useState<Mode>("paste");
  const [aiEnabled, setAiEnabled] = useState<boolean | null>(null);
  const counts = useOrderCounts();

  useEffect(() => {
    void useOrderStore.persist.rehydrate();
  }, []);

  const onHealth = useCallback((h: Health | null) => setAiEnabled(h ? h.aiEnabled : null), []);

  return (
    <div className="embed-auto-height min-h-screen pb-24 lg:pb-8">
      <EmbedBridge />
      <Header onHealth={onHealth} />

      <main className="mx-auto grid max-w-7xl gap-5 px-4 py-6 sm:px-6 sm:py-8 lg:grid-cols-[minmax(0,1fr)_400px] lg:items-start">
        <div className="flex flex-col gap-5 border-b border-smoke-700 pb-5 sm:flex-row sm:items-end sm:justify-between lg:col-span-2">
          <div className="max-w-3xl">
            <p className="flex items-center gap-2 text-[11px] font-bold tracking-[0.16em] text-blazer-700 uppercase">
              <span className="h-1 w-6 rounded-full bg-blazer-orange" aria-hidden />
              Built for the counter · Ready for the jobsite
            </p>
            <h1 className="mt-2 text-3xl leading-tight font-bold tracking-tight text-blazer-950 sm:text-4xl">
              Turn job lists into <span className="text-blazer-900">ready-to-review orders.</span>
            </h1>
            <p className="mt-2 max-w-2xl text-sm leading-relaxed text-slate-600 sm:text-base">
              Paste a customer takeoff or build one with the AI assistant. Check every match before it reaches your Blazer order.
            </p>
          </div>
          <ol aria-label="Order workflow" className="flex shrink-0 flex-wrap items-center gap-2 text-xs font-semibold text-slate-700">
            {[
              ["01", "Build"],
              ["02", "Review"],
              ["03", "Send"],
            ].map(([number, label], index) => (
              <li key={number} className="flex items-center gap-2">
                {index > 0 && <span className="hidden h-px w-3 bg-smoke-700 sm:block" aria-hidden />}
                <span className="inline-flex items-center gap-1.5 rounded-full border border-smoke-700 bg-white px-2.5 py-1.5 shadow-sm">
                  <span className={`font-mono text-[10px] ${index === 0 ? "text-blazer-orange" : "text-blazer-700"}`}>{number}</span>
                  {label}
                </span>
              </li>
            ))}
          </ol>
        </div>

        <div className="min-w-0 rounded-2xl border border-smoke-700 border-t-[3px] border-t-blazer-orange bg-white p-4 shadow-[0_8px_24px_rgba(23,45,97,0.06)] sm:p-6">
          <div role="tablist" aria-label="Ordering mode" className="mb-6 grid grid-cols-2 gap-1 rounded-xl bg-slate-100 p-1">
            {TABS.map(({ id, label, hint, Icon }) => {
              const active = mode === id;
              return (
                <button
                  key={id}
                  id={`tab-${id}`}
                  role="tab"
                  type="button"
                  aria-selected={active}
                  aria-controls={`panel-${id}`}
                  onClick={() => setMode(id)}
                  onKeyDown={(e) => {
                    if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
                      const next = mode === "paste" ? "chat" : "paste";
                      setMode(next);
                      document.getElementById(`tab-${next}`)?.focus();
                    }
                  }}
                  tabIndex={active ? 0 : -1}
                  className={`flex items-center justify-center gap-2 rounded-lg px-3 py-2.5 text-sm font-semibold transition sm:justify-start ${
                    active
                      ? "bg-white text-blazer-900 shadow-sm ring-1 ring-slate-200"
                      : "text-slate-600 hover:bg-white/70 hover:text-blazer-800"
                  }`}
                >
                  <Icon className="size-5 shrink-0" aria-hidden />
                  <span className="text-left leading-tight">
                    {label}
                    <span className="hidden text-xs font-normal text-slate-500 sm:block">{hint}</span>
                  </span>
                </button>
              );
            })}
          </div>

          <div id="panel-paste" role="tabpanel" aria-labelledby="tab-paste" hidden={mode !== "paste"}>
            <PasteOrderPanel />
          </div>
          <div id="panel-chat" role="tabpanel" aria-labelledby="tab-chat" hidden={mode !== "chat"}>
            {aiEnabled === false ? (
              <p className="rounded-lg bg-amber-50 p-4 text-sm text-amber-900">
                The AI assistant isn&apos;t configured on this deployment yet. Paste ordering and catalog search still work.
              </p>
            ) : (
              <ChatAssistantPanel />
            )}
          </div>
        </div>

        <OrderPanel />
      </main>

      {counts.total > 0 && (
        <div className="embed-hide fixed inset-x-0 bottom-0 z-40 border-t border-slate-200 bg-white/95 px-4 py-3 shadow-[0_-4px_12px_rgba(0,0,0,0.06)] backdrop-blur lg:hidden">
          <div className="mx-auto flex max-w-7xl items-center justify-between gap-3">
            <p className="text-sm text-slate-700">
              <strong className="text-blazer-900">{counts.total}</strong> item{counts.total === 1 ? "" : "s"}
              {counts.review + counts.unmatched > 0 && (
                <span className="text-amber-700"> · {counts.review + counts.unmatched} need attention</span>
              )}
            </p>
            <button
              type="button"
              onClick={() => document.getElementById("order-panel")?.scrollIntoView({ behavior: "smooth", block: "start" })}
              className="rounded-lg bg-blazer-800 px-4 py-2 text-sm font-semibold text-white"
            >
              View order
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
