import type { ChatMessage, ChatResponse, ProductSummary, ResolvedLine, SearchHit } from "./types";

async function json<T>(res: Response): Promise<T> {
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error((body as { error?: string }).error || `Request failed (${res.status})`);
  return body as T;
}

export async function searchProducts(q: string, signal?: AbortSignal): Promise<SearchHit[]> {
  const res = await fetch(`/api/search?q=${encodeURIComponent(q)}&limit=8`, { signal });
  return (await json<{ results: SearchHit[] }>(res)).results;
}

export interface ParsedOrder {
  lines: ResolvedLine[];
  skipped: string[];
}

export async function parseOrder(text: string): Promise<ParsedOrder> {
  const res = await fetch("/api/parse", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  const body = await json<Partial<ParsedOrder>>(res);
  return { lines: body.lines ?? [], skipped: body.skipped ?? [] };
}

export interface RelatedProducts {
  product: ProductSummary;
  accessories: ProductSummary[];
  similar: ProductSummary[];
}

export async function fetchRelated(sku: string): Promise<RelatedProducts> {
  return json<RelatedProducts>(await fetch(`/api/products/${encodeURIComponent(sku)}`));
}

export async function sendChat(messages: ChatMessage[], cartSummary: string): Promise<ChatResponse> {
  const res = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ messages, cartSummary }),
  });
  return json<ChatResponse>(res);
}

export async function draftSummary(orderText: string, fallback: string): Promise<{ summary: string; source: "ai" | "rules" }> {
  const res = await fetch("/api/summary", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ orderText, fallback }),
  });
  return json(res);
}

export interface Health {
  totalProducts: number;
  lastUpdated: string;
  aiEnabled: boolean;
}

export async function fetchHealth(): Promise<Health> {
  return json<Health>(await fetch("/api/health"));
}
