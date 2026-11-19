"use client";

import { ArrowDown } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import type { Health } from "@/lib/api";
import { useOrderStore } from "@/store/orderStore";
import { EmbedBridge } from "./EmbedBridge";
import { Header } from "./Header";
import { OrderPanel, useOrderCounts } from "./OrderPanel";
import { PasteOrderPanel } from "./PasteOrderPanel";

export function OrderDesk() {
  const [aiEnabled, setAiEnabled] = useState<boolean | null>(null);
  const [restored, setRestored] = useState(false);
  const [draft, setDraft] = useState("");
  const counts = useOrderCounts();

  useEffect(() => {
    void Promise.resolve(useOrderStore.persist.rehydrate()).then(() => setRestored(true));
  }, []);

  const onHealth = useCallback((health: Health | null) => setAiEnabled(health ? health.aiEnabled : null), []);

  return (
    <div className="embed-auto-height min-h-screen pb-24 lg:pb-8">
      <EmbedBridge />
      <Header onHealth={onHealth} />
      <main className="mx-auto grid max-w-7xl items-start gap-6 px-4 py-5 sm:px-6 lg:grid-cols-[minmax(0,1.05fr)_minmax(0,1fr)]">
        {restored ? <>
          <div className="min-w-0 border-t-[3px] border-blazer-orange pt-4"><PasteOrderPanel aiEnabled={aiEnabled} text={draft} setText={setDraft} /></div>
          <OrderPanel onClarify={(line, number) => {
            setDraft(`For line ${number} (${line.rawText}), `);
            document.getElementById("order-text")?.focus();
            document.getElementById("order-text")?.scrollIntoView({ behavior: "smooth", block: "center" });
          }} />
        </> : <p role="status" className="py-8 text-sm text-slate-600">Restoring your order…</p>}
      </main>
      {counts.total > 0 && (
        <div className="embed-hide fixed inset-x-0 bottom-0 z-40 border-t border-slate-200 bg-white/95 px-4 py-3 shadow-sm backdrop-blur lg:hidden">
          <div className="mx-auto flex max-w-7xl items-center justify-between gap-3">
            <p className="text-sm text-slate-700"><strong>{counts.total}</strong> items{counts.review + counts.unmatched > 0 && <span className="text-amber-800"> · {counts.review + counts.unmatched} need attention</span>}</p>
            <button type="button" onClick={() => document.getElementById("order-panel")?.scrollIntoView({ behavior: "smooth", block: "start" })} className="inline-flex shrink-0 items-center gap-2 rounded-lg bg-blazer-800 px-3 py-2 text-sm font-semibold text-white"><ArrowDown className="size-4" aria-hidden /> View order</button>
          </div>
        </div>
      )}
    </div>
  );
}