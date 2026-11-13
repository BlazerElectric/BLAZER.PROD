import { AlertTriangle, CheckCircle2, HelpCircle } from "lucide-react";
import type { OrderLine } from "@/lib/types";
import { needsReview } from "@/store/orderStore";

export type LineState = "ready" | "review" | "unmatched";

export function lineState(line: OrderLine): LineState {
  if (!line.product) return "unmatched";
  return needsReview(line) ? "review" : "ready";
}

const STYLES: Record<LineState, { className: string; label: string; Icon: typeof CheckCircle2 }> = {
  ready: { className: "bg-emerald-50 text-emerald-800 ring-emerald-600/20", label: "Matched", Icon: CheckCircle2 },
  review: { className: "bg-amber-50 text-amber-800 ring-amber-600/30", label: "Review match", Icon: AlertTriangle },
  unmatched: { className: "bg-rose-50 text-rose-800 ring-rose-600/20", label: "Not found", Icon: HelpCircle },
};

export const STATE_BORDER: Record<LineState, string> = {
  ready: "border-l-emerald-500",
  review: "border-l-amber-400",
  unmatched: "border-l-rose-400",
};

export function ConfidenceBadge({ line }: { line: OrderLine }) {
  const state = lineState(line);
  const { className, Icon } = STYLES[state];
  const label = state === "ready" && line.confirmed && line.confidence !== "high" ? "Confirmed" : STYLES[state].label;
  return (
    <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-semibold ring-1 ring-inset ${className}`}>
      <Icon className="size-3.5" aria-hidden />
      {label}
    </span>
  );
}
