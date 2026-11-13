"use client";

import { Database, Link2, Link2Off, Zap } from "lucide-react";
import { useEffect, useState } from "react";
import { fetchHealth, type Health } from "@/lib/api";
import { useOrderStore, type BridgeStatus } from "@/store/orderStore";

const BRIDGE: Record<BridgeStatus, { label: string; className: string; connected: boolean }> = {
  checking: { label: "Connecting…", className: "bg-white/10 text-blazer-100", connected: false },
  connected: { label: "Billtrust cart linked", className: "bg-emerald-500/20 text-emerald-100", connected: true },
  standalone: { label: "Standalone mode", className: "bg-white/10 text-blazer-100", connected: false },
  "embedded-no-bridge": { label: "Cart bridge missing", className: "bg-amber-400/20 text-amber-100", connected: false },
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
    <header className="bg-blazer-800 text-white shadow-md">
      <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-3 px-4 py-3 sm:px-6">
        <div className="flex items-center gap-3">
          <span className="flex size-10 items-center justify-center rounded-lg bg-blazer-500 shadow-inner" aria-hidden>
            <Zap className="size-6 fill-yellow-300 text-yellow-300" />
          </span>
          <div className="leading-tight">
            <p className="text-lg font-extrabold tracking-wider">BLAZER ELECTRIC</p>
            <p className="text-xs font-medium tracking-wide text-blazer-200 uppercase">AI Order Desk</p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-xs font-medium">
          <span
            className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 ${healthError ? "bg-rose-500/20 text-rose-100" : "bg-white/10 text-blazer-100"}`}
          >
            <Database className="size-3.5" aria-hidden />
            {healthError
              ? "Catalog offline"
              : health
                ? `${health.totalProducts.toLocaleString()} products`
                : "Loading catalog…"}
          </span>
          <span className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 ${b.className}`}>
            <BridgeIcon className="size-3.5" aria-hidden />
            {b.label}
          </span>
        </div>
      </div>
    </header>
  );
}
