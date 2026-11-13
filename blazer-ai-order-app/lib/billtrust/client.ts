"use client";

import {
  APP_SOURCE,
  PROTOCOL_VERSION,
  isBridgeMessage,
  type AppToBridgeMessage,
  type BridgeToAppMessage,
  type SubmitPayload,
  type SubmitResult,
} from "./protocol";

const DEFAULT_ORIGINS = "https://buyblazer.com,https://www.buyblazer.com";

export function allowedParentOrigins(): string[] {
  return (process.env.NEXT_PUBLIC_BILLTRUST_ORIGINS || DEFAULT_ORIGINS)
    .split(",")
    .map((o) => o.trim().replace(/\/$/, ""))
    .filter(Boolean);
}

export function isEmbedded(): boolean {
  if (typeof window === "undefined") return false;
  try {
    return window.self !== window.top;
  } catch {
    return true;
  }
}

/**
 * Resolve the exact parent origin so postMessage never broadcasts with "*".
 * Only origins on the allowlist are accepted.
 */
export function resolveParentOrigin(): string | null {
  if (!isEmbedded()) return null;
  const allowed = allowedParentOrigins();
  const candidates: string[] = [];
  const ancestors = (window.location as Location & { ancestorOrigins?: DOMStringList }).ancestorOrigins;
  if (ancestors && ancestors.length > 0) candidates.push(ancestors[0]);
  if (document.referrer) {
    try {
      candidates.push(new URL(document.referrer).origin);
    } catch {
      /* ignore malformed referrer */
    }
  }
  return candidates.find((o) => allowed.includes(o)) ?? null;
}

function post(message: AppToBridgeMessage, origin: string) {
  window.parent.postMessage(message, origin);
}

function waitFor<T extends BridgeToAppMessage>(
  origin: string,
  predicate: (m: BridgeToAppMessage) => m is T,
  timeoutMs: number,
): Promise<T | null> {
  return new Promise((resolve) => {
    const onMessage = (event: MessageEvent) => {
      if (event.origin !== origin || event.source !== window.parent) return;
      if (!isBridgeMessage(event.data) || !predicate(event.data)) return;
      cleanup();
      resolve(event.data);
    };
    const timer = window.setTimeout(() => {
      cleanup();
      resolve(null);
    }, timeoutMs);
    const cleanup = () => {
      window.clearTimeout(timer);
      window.removeEventListener("message", onMessage);
    };
    window.addEventListener("message", onMessage);
  });
}

/** Ask the parent page whether billtrust-bridge.js is installed. */
export async function pingBridge(timeoutMs = 1500): Promise<{ origin: string; page: string } | null> {
  const origin = resolveParentOrigin();
  if (!origin) return null;
  const ready = waitFor(origin, (m): m is Extract<BridgeToAppMessage, { type: "READY" }> => m.type === "READY", timeoutMs);
  post({ source: APP_SOURCE, version: PROTOCOL_VERSION, type: "PING" }, origin);
  const msg = await ready;
  return msg ? { origin, page: msg.page } : null;
}

export async function submitToBilltrust(payload: SubmitPayload, timeoutMs = 10000): Promise<SubmitResult> {
  const origin = resolveParentOrigin();
  if (!origin) {
    return { ok: false, quickPad: "not_found", commentsQueued: 0, shippingQueued: false, error: "Not embedded in an allowed Billtrust page." };
  }
  const requestId = crypto.randomUUID();
  const result = waitFor(
    origin,
    (m): m is Extract<BridgeToAppMessage, { type: "SUBMIT_RESULT" }> => m.type === "SUBMIT_RESULT" && m.requestId === requestId,
    timeoutMs,
  );
  post({ source: APP_SOURCE, version: PROTOCOL_VERSION, type: "SUBMIT_ORDER", requestId, payload }, origin);
  const msg = await result;
  if (!msg) {
    return { ok: false, quickPad: "not_found", commentsQueued: 0, shippingQueued: false, error: "Billtrust bridge did not respond." };
  }
  return {
    ok: msg.ok,
    quickPad: msg.quickPad,
    commentsQueued: msg.commentsQueued,
    shippingQueued: msg.shippingQueued,
    error: msg.error,
  };
}

/** Keep the parent iframe sized to our content so the embed never shows inner scrollbars. */
export function startAutoResize(): () => void {
  const origin = resolveParentOrigin();
  if (!origin) return () => {};
  let last = 0;
  const send = () => {
    const height = Math.ceil(document.documentElement.scrollHeight);
    if (Math.abs(height - last) < 4) return;
    last = height;
    post({ source: APP_SOURCE, version: PROTOCOL_VERSION, type: "RESIZE", height }, origin);
  };
  const observer = new ResizeObserver(send);
  observer.observe(document.body);
  send();
  return () => observer.disconnect();
}
