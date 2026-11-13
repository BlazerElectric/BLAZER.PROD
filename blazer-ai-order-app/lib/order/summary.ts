import type { OrderLine } from "../types";

/** Deterministic shipping-instructions summary (used directly and as the AI fallback). */
export function buildShippingSummary(lines: OrderLine[], extraNotes = ""): string {
  const matched = lines.filter((l) => l.product);
  const unmatched = lines.filter((l) => !l.product);
  const commented = matched.filter((l) => l.comment.trim());
  const cutLines = commented.filter((l) => /cut/i.test(l.comment));

  const parts: string[] = [];
  parts.push(`AI Order Desk: ${matched.length} catalog line${matched.length === 1 ? "" : "s"}.`);
  if (cutLines.length) {
    parts.push(
      `Wire/conduit cuts: ${cutLines.map((l) => `${l.product!.sku} (${l.comment.trim()})`).join("; ")}.`,
    );
  }
  const otherNotes = commented.filter((l) => !cutLines.includes(l));
  if (otherNotes.length) {
    parts.push(`Line notes: ${otherNotes.map((l) => `${l.product!.sku}: ${l.comment.trim()}`).join("; ")}.`);
  }
  if (unmatched.length) {
    parts.push(
      `Please source (not in catalog): ${unmatched
        .map((l) => `${l.quantity} ${l.unit} ${(l.manualText || l.rawText).trim()}`)
        .join("; ")}.`,
    );
  }
  if (extraNotes.trim()) parts.push(extraNotes.trim());
  return parts.join("\n");
}
