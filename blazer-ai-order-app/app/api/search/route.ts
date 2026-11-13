import type { NextRequest } from "next/server";
import { getEngine } from "@/lib/catalog/server";

export async function GET(request: NextRequest) {
  const q = (request.nextUrl.searchParams.get("q") ?? "").slice(0, 200);
  const limit = Math.min(25, Math.max(1, Number(request.nextUrl.searchParams.get("limit")) || 10));
  if (!q.trim()) return Response.json({ results: [] });
  const results = getEngine().search(q, { limit, prefixLast: true });
  return Response.json({ results });
}
