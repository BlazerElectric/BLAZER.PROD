"use client";

import { AlertTriangle, Check, CheckCircle2, ClipboardCopy, Loader2, Send, Sparkles, X, XCircle } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { draftSummary } from "@/lib/api";
import { submitToBilltrust } from "@/lib/billtrust/client";
import { LIMITS, buildSubmitPayload, formatQuickPad, type SubmitPayload, type SubmitResult } from "@/lib/billtrust/protocol";
import { buildShippingSummary } from "@/lib/order/summary";
import { needsReview, useOrderStore } from "@/store/orderStore";

function CopyBlock({ label, value, rows = 4 }: { label: string; value: string; rows?: number }) {
  const [copied, setCopied] = useState(false);
  const id = `copy-${label.replace(/\W+/g, "-").toLowerCase()}`;
  return (
    <div>
      <div className="mb-1 flex items-center justify-between">
        <label htmlFor={id} className="text-xs font-semibold text-slate-700">
          {label}
        </label>
        <button
          type="button"
          disabled={!value}
          onClick={async () => {
            try {
              await navigator.clipboard.writeText(value);
              setCopied(true);
              window.setTimeout(() => setCopied(false), 1500);
            } catch {
              (document.getElementById(id) as HTMLTextAreaElement | null)?.select();
            }
          }}
          className="inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-xs font-medium text-blazer-700 hover:bg-blazer-50 disabled:opacity-40"
        >
          {copied ? <Check className="size-3.5" aria-hidden /> : <ClipboardCopy className="size-3.5" aria-hidden />}
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <textarea
        id={id}
        readOnly
        rows={rows}
        value={value || "—"}
        className="w-full resize-y rounded-lg border border-slate-200 bg-slate-50 p-2 font-mono text-xs"
      />
    </div>
  );
}

function ManualDialog({ payload, onClose }: { payload: SubmitPayload; onClose: () => void }) {
  const card = useRef<HTMLDivElement>(null);
  useEffect(() => {
    // In an auto-sized iframe the overlay spans the whole document; bring the card into the parent's view.
    card.current?.scrollIntoView({ block: "center", behavior: "smooth" });
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-slate-900/50 p-2 sm:items-center" role="dialog" aria-modal="true" aria-labelledby="manual-title">
      <div ref={card} className="embed-auto-height max-h-[92vh] w-full max-w-lg overflow-y-auto rounded-2xl bg-white p-5 shadow-xl">
        <div className="mb-3 flex items-start justify-between gap-2">
          <div>
            <h3 id="manual-title" className="text-base font-semibold text-blazer-900">
              Copy into Billtrust QuickPad
            </h3>
            <p className="text-sm text-slate-600">
              The Billtrust cart bridge isn&apos;t connected on this page. Paste the lines below into QuickPad, then add
              the notes in your cart.
            </p>
          </div>
          <button type="button" onClick={onClose} className="rounded-md p-1 text-slate-500 hover:bg-slate-100" aria-label="Close">
            <X className="size-5" aria-hidden />
          </button>
        </div>
        <div className="space-y-3">
          <CopyBlock label="QuickPad lines (SKU,QTY)" value={formatQuickPad(payload.items)} rows={Math.min(10, payload.items.length + 1)} />
          {payload.lineComments.length > 0 && (
            <CopyBlock label="Line comments" value={payload.lineComments.map((c) => `${c.sku}: ${c.comment}`).join("\n")} />
          )}
          <CopyBlock label="Shipping instructions" value={payload.shippingInstructions} />
        </div>
        <div className="mt-4 flex justify-end gap-2">
          <a
            href="https://buyblazer.com"
            target="_blank"
            rel="noopener noreferrer"
            className="rounded-lg px-3 py-2 text-sm font-medium text-blazer-700 hover:bg-blazer-50"
          >
            Open buyblazer.com
          </a>
          <button type="button" onClick={onClose} className="rounded-lg bg-blazer-800 px-4 py-2 text-sm font-semibold text-white hover:bg-blazer-700">
            Done
          </button>
        </div>
      </div>
    </div>
  );
}

const RESULT_TEXT: Record<SubmitResult["quickPad"], string> = {
  injected: "Items were placed in QuickPad — review and click Add to Cart on the page.",
  injected_and_submitted: "Items were added to your Billtrust cart.",
  redirecting: "Opening QuickPad — your items will be filled in automatically.",
  not_found: "QuickPad wasn't found on this page.",
};

export function SubmitPanel() {
  const lines = useOrderStore((s) => s.lines);
  const shipping = useOrderStore((s) => s.shippingInstructions);
  const setShipping = useOrderStore((s) => s.setShippingInstructions);
  const bridge = useOrderStore((s) => s.bridge);
  const [drafting, setDrafting] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [confirmReview, setConfirmReview] = useState(false);
  const [result, setResult] = useState<SubmitResult | null>(null);
  const [manual, setManual] = useState<SubmitPayload | null>(null);
  const closeManual = useCallback(() => setManual(null), []);

  const reviewCount = useMemo(() => lines.filter((l) => l.product && needsReview(l)).length, [lines]);
  const unmatchedCount = useMemo(() => lines.filter((l) => !l.product).length, [lines]);
  const matchedCount = lines.length - unmatchedCount;

  const draft = async () => {
    setDrafting(true);
    const fallback = buildShippingSummary(lines);
    try {
      const orderText = lines
        .map((l) => `${l.quantity} ${l.unit} ${l.product ? `#${l.product.sku} ${l.product.title}` : `[NOT IN CATALOG] ${l.manualText || l.rawText}`}${l.comment ? ` — note: ${l.comment}` : ""}`)
        .join("\n");
      const { summary } = await draftSummary(orderText, fallback);
      setShipping(summary.slice(0, LIMITS.maxShippingLength));
    } catch {
      setShipping(fallback.slice(0, LIMITS.maxShippingLength));
    } finally {
      setDrafting(false);
    }
  };

  const submit = async () => {
    if (reviewCount > 0 && !confirmReview) {
      setConfirmReview(true);
      return;
    }
    setConfirmReview(false);
    setResult(null);

    // Unmatched lines can't go into QuickPad; make sure the counter sees them in shipping instructions.
    let shippingText = shipping;
    if (unmatchedCount > 0 && !/not in catalog/i.test(shippingText)) {
      const sourcing = buildShippingSummary(lines.filter((l) => !l.product))
        .split("\n")
        .filter((s) => s.startsWith("Please source"));
      shippingText = [shippingText.trim(), ...sourcing].filter(Boolean).join("\n");
    }
    const payload = buildSubmitPayload(
      lines.map((l) => ({ sku: l.product?.sku, quantity: l.quantity, comment: l.comment })),
      shippingText,
    );

    if (bridge !== "connected") {
      setManual(payload);
      return;
    }
    setSubmitting(true);
    try {
      const res = await submitToBilltrust(payload);
      setResult(res);
      if (!res.ok || res.quickPad === "not_found") setManual(payload);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="space-y-3 border-t border-slate-200 pt-4">
      <div>
        <div className="mb-1 flex items-center justify-between">
          <label htmlFor="shipping" className="text-sm font-semibold text-blazer-900">
            Shipping instructions
          </label>
          <button
            type="button"
            onClick={() => void draft()}
            disabled={drafting || lines.length === 0}
            className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-semibold text-blazer-700 hover:bg-blazer-50 disabled:opacity-40"
          >
            {drafting ? <Loader2 className="size-3.5 animate-spin" aria-hidden /> : <Sparkles className="size-3.5" aria-hidden />}
            Draft summary
          </button>
        </div>
        <textarea
          id="shipping"
          rows={4}
          value={shipping}
          maxLength={LIMITS.maxShippingLength}
          onChange={(e) => setShipping(e.target.value)}
          placeholder="Delivery window, jobsite contact, cut instructions, will-call…"
          className="w-full resize-y rounded-lg border border-slate-300 p-2 text-sm focus:border-blazer-600"
        />
        <p className="text-right text-[11px] text-slate-500 tabular-nums">
          {shipping.length}/{LIMITS.maxShippingLength}
        </p>
      </div>

      {confirmReview && (
        <div role="alert" className="flex gap-2 rounded-lg bg-amber-50 p-3 text-sm text-amber-900 ring-1 ring-amber-300">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
          <p>
            {reviewCount} line{reviewCount === 1 ? " is" : "s are"} still marked <strong>Review match</strong>. Submit
            anyway? Click submit again to confirm.
          </p>
        </div>
      )}
      {unmatchedCount > 0 && (
        <p className="text-xs text-slate-600">
          {unmatchedCount} item{unmatchedCount === 1 ? "" : "s"} not in the catalog will be sent in the shipping
          instructions for the counter to source.
        </p>
      )}

      <button
        type="button"
        onClick={() => void submit()}
        disabled={submitting || matchedCount === 0}
        className="flex w-full items-center justify-center gap-2 rounded-xl bg-blazer-800 px-4 py-3 text-base font-bold text-white shadow-md hover:bg-blazer-700 focus-visible:ring-4 focus-visible:ring-blazer-200 disabled:opacity-50"
      >
        {submitting ? <Loader2 className="size-5 animate-spin" aria-hidden /> : <Send className="size-5" aria-hidden />}
        {confirmReview ? "Confirm & submit to Billtrust Cart" : "Submit to Billtrust Cart"}
      </button>
      {bridge !== "connected" && (
        <p className="text-center text-xs text-slate-500">
          {bridge === "checking" ? "Connecting to Billtrust…" : "Billtrust bridge not detected — you'll get copy-ready QuickPad text."}
        </p>
      )}

      {result && (
        <div
          role="status"
          className={`flex gap-2 rounded-lg p-3 text-sm ring-1 ${
            result.ok ? "bg-emerald-50 text-emerald-900 ring-emerald-200" : "bg-rose-50 text-rose-900 ring-rose-200"
          }`}
        >
          {result.ok ? <CheckCircle2 className="mt-0.5 size-4 shrink-0" aria-hidden /> : <XCircle className="mt-0.5 size-4 shrink-0" aria-hidden />}
          <div>
            <p>{result.error || RESULT_TEXT[result.quickPad]}</p>
            {result.ok && (result.commentsQueued > 0 || result.shippingQueued) && (
              <p className="mt-0.5 text-xs">
                {result.commentsQueued > 0 && `${result.commentsQueued} line comment${result.commentsQueued === 1 ? "" : "s"} `}
                {result.commentsQueued > 0 && result.shippingQueued && "and "}
                {result.shippingQueued && "shipping instructions "}
                will be filled in on the cart page.
              </p>
            )}
          </div>
        </div>
      )}

      {manual && <ManualDialog payload={manual} onClose={closeManual} />}
    </div>
  );
}
