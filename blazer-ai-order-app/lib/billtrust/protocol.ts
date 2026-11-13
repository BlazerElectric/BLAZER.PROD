/**
 * postMessage protocol between the embedded order app (iframe on Vercel) and
 * public/billtrust-bridge.js running on the Billtrust storefront (buyblazer.com).
 * The bridge re-validates everything it receives; keep the limits here in sync with it.
 */

export const APP_SOURCE = "blazer-order-app";
export const BRIDGE_SOURCE = "billtrust-bridge";
export const PROTOCOL_VERSION = 1;

export const LIMITS = {
  maxItems: 200,
  maxQuantity: 99999,
  maxCommentLength: 250,
  maxShippingLength: 1000,
} as const;

export const SKU_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$/;

export interface SubmitItem {
  sku: string;
  quantity: number;
}

export interface LineComment {
  sku: string;
  comment: string;
}

export interface SubmitPayload {
  items: SubmitItem[];
  lineComments: LineComment[];
  shippingInstructions: string;
}

export type AppToBridgeMessage =
  | { source: typeof APP_SOURCE; version: number; type: "PING" }
  | { source: typeof APP_SOURCE; version: number; type: "RESIZE"; height: number }
  | { source: typeof APP_SOURCE; version: number; type: "SUBMIT_ORDER"; requestId: string; payload: SubmitPayload };

export interface SubmitResult {
  ok: boolean;
  quickPad: "injected" | "injected_and_submitted" | "redirecting" | "not_found";
  commentsQueued: number;
  shippingQueued: boolean;
  error?: string;
}

export type BridgeToAppMessage =
  | { source: typeof BRIDGE_SOURCE; version: number; type: "READY"; page: string }
  | ({ source: typeof BRIDGE_SOURCE; version: number; type: "SUBMIT_RESULT"; requestId: string } & SubmitResult);

/** Remove control characters and collapse whitespace; the bridge only ever assigns `.value`. */
export function sanitizeText(value: string, maxLength: number): string {
  return value
    .replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/g, "")
    .replace(/[<>]/g, "")
    .replace(/[ \t]+/g, " ")
    .trim()
    .slice(0, maxLength);
}

export interface SubmittableLine {
  sku: string | null | undefined;
  quantity: number;
  comment: string;
}

/** Merge duplicate SKUs, validate, and clamp everything before it leaves the iframe. */
export function buildSubmitPayload(lines: SubmittableLine[], shippingInstructions: string): SubmitPayload {
  const merged = new Map<string, { quantity: number; comments: string[] }>();
  for (const line of lines) {
    const sku = (line.sku ?? "").trim();
    if (!SKU_PATTERN.test(sku)) continue;
    const qty = Math.floor(Number(line.quantity));
    if (!Number.isFinite(qty) || qty < 1) continue;
    const entry = merged.get(sku) ?? { quantity: 0, comments: [] };
    entry.quantity = Math.min(LIMITS.maxQuantity, entry.quantity + qty);
    const comment = sanitizeText(line.comment ?? "", LIMITS.maxCommentLength);
    if (comment) entry.comments.push(comment);
    merged.set(sku, entry);
  }

  const items: SubmitItem[] = [];
  const lineComments: LineComment[] = [];
  for (const [sku, entry] of [...merged].slice(0, LIMITS.maxItems)) {
    items.push({ sku, quantity: entry.quantity });
    if (entry.comments.length) {
      lineComments.push({ sku, comment: sanitizeText(entry.comments.join(" | "), LIMITS.maxCommentLength) });
    }
  }
  return {
    items,
    lineComments,
    shippingInstructions: sanitizeText(shippingInstructions.replace(/\r\n/g, "\n"), LIMITS.maxShippingLength),
  };
}

/** Render items in QuickPad's line format. Default "{sku},{qty}" must match the bridge's lineTemplate. */
export function formatQuickPad(items: SubmitItem[], template = "{sku},{qty}"): string {
  return items.map((i) => template.replace("{sku}", i.sku).replace("{qty}", String(i.quantity))).join("\n");
}

export function isBridgeMessage(data: unknown): data is BridgeToAppMessage {
  if (!data || typeof data !== "object") return false;
  const d = data as Record<string, unknown>;
  return d.source === BRIDGE_SOURCE && d.version === PROTOCOL_VERSION && typeof d.type === "string";
}
