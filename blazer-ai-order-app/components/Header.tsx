"use client";

import Image from "next/image";
import { Database, Link2, Link2Off } from "lucide-react";
import { useEffect, useState } from "react";
import { fetchHealth, type Health } from "@/lib/api";
import { useOrderStore, type BridgeStatus } from "@/store/orderStore";

const BRIDGE: Record<BridgeStatus, { label: string; className: string; connected: boolean }> = {
  checking: { label: "Connecting…", className: "border-smoke-700 bg-smoke-400 text-slate-600", connected: false },
  connected: { label: "Billtrust cart linked", className: "border-emerald-200 bg-emerald-50 text-emerald-800", connected: true },
  standalone: { label: "Standalone mode", className: "border-smoke-700 bg-smoke-400 text-slate-600", connected: false },
  "embedded-no-bridge": { label: "Cart bridge missing", className: "border-amber-200 bg-amber-50 text-amber-800", connected: false },
};

export function Header({ onHealth }: { onHealth?: (h: Health | null) => void }) {
  const bridge = useOrderStore((s) => s.bridge);
  const [health, setHealth] = useState<Health | null>(null);
  const [healthError, setHealthError] = useState(false);

  useEffect(() => {
    fetchHealth()
      .then((h) => {
        setHealth(h);
        onHealth?.(h);
      })
      .catch(() => {
        setHealthError(true);
        onHealth?.(null);
      });
  }, [onHealth]);

  const b = BRIDGE[bridge];
  const BridgeIcon = b.connected ? Link2 : Link2Off;

  return (
    <header className="relative border-b border-smoke-700 bg-white shadow-[0_3px_14px_rgba(23,45,97,0.06)]">
      <div className="absolute inset-x-0 top-0 h-1 bg-blazer-orange" aria-hidden />
      <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-x-6 gap-y-3 px-4 pt-3 pb-3 sm:px-6">
        <div className="flex min-w-0 items-center gap-3 sm:gap-4">
          <Image
            src="/blazer-logo-blue.png"
            alt="Blazer Electric Supply Company"
            width={425}
            height={351}
            priority
            className="h-[76px] w-[92px] shrink-0 object-contain sm:h-[96px] sm:w-[116px]"
          />
          <span className="hidden h-12 w-px bg-smoke-600 sm:block" aria-hidden />
          <div className="leading-tight">
            <p className="text-[10px] font-bold tracking-[0.18em] text-blazer-orange uppercase sm:text-[11px]">
              Material ordering
            </p>
            <p className="mt-1 text-lg font-bold tracking-tight text-blazer-950 sm:text-2xl">AI Order Desk</p>
            <p className="mt-1 hidden text-xs text-slate-500 sm:block">From job list to counter-ready order</p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-[11px] font-semibold sm:text-xs">
          <span
            className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 ${
              healthError ? "border-rose-200 bg-rose-50 text-rose-800" : "border-smoke-700 bg-smoke-400 text-slate-700"
            }`}
          >
            <Database className={`size-3.5 ${healthError ? "text-rose-600" : "text-blazer-700"}`} aria-hidden />
            {healthError
              ? "Catalog offline"
              : health
                ? `${health.totalProducts.toLocaleString()} products`
                : "Loading catalog…"}
          </span>
          <span className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 ${b.className}`}>
            <BridgeIcon className="size-3.5" aria-hidden />
            {b.label}
          </span>
        </div>
      </div>
    </header>
  );
}
