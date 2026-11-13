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

      <main className="mx-auto grid max-w-7xl gap-6 px-4 py-6 sm:px-6 lg:grid-cols-[minmax(0,1fr)_420px] lg:items-start">
        <div className="min-w-0 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm sm:p-6">
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
                    active ? "bg-white text-blazer-800 shadow" : "text-slate-600 hover:text-blazer-800"
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
