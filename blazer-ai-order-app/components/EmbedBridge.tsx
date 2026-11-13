"use client";

import { useEffect } from "react";
import { isEmbedded, pingBridge, startAutoResize } from "@/lib/billtrust/client";
import { useOrderStore } from "@/store/orderStore";

/** Detects billtrust-bridge.js on the parent page and keeps the iframe height in sync. */
export function EmbedBridge() {
  const setBridge = useOrderStore((s) => s.setBridge);

  useEffect(() => {
    if (!isEmbedded()) {
      setBridge("standalone");
      return;
    }
    document.documentElement.dataset.embedded = "true";
    let cancelled = false;
    const stopResize = startAutoResize();
    (async () => {
      // The bridge may load after the iframe; retry a few times before giving up.
      for (let attempt = 0; attempt < 3 && !cancelled; attempt++) {
        if (await pingBridge(1500)) {
          if (!cancelled) setBridge("connected");
          return;
        }
      }
      if (!cancelled) setBridge("embedded-no-bridge");
    })();
    return () => {
      cancelled = true;
      stopResize();
    };
  }, [setBridge]);

  return null;
}
