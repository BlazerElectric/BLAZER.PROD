import { z } from "zod";
import { aiConfigured, summarizeForShipping } from "@/lib/ai/gemini";
import { clientKey, rateLimit } from "@/lib/ratelimit";

const Body = z.object({ orderText: z.string().min(1).max(20_000), fallback: z.string().max(4000) });

export async function POST(request: Request) {
  const parsed = Body.safeParse(await request.json().catch(() => null));
  if (!parsed.success) return Response.json({ error: "Invalid summary request." }, { status: 400 });
  const { orderText, fallback } = parsed.data;

  if (!aiConfigured() || !rateLimit(`summary:${clientKey(request)}`, 20, 60_000)) {
    return Response.json({ summary: fallback, source: "rules" });
  }
  try {
    return Response.json({ summary: await summarizeForShipping(orderText, fallback), source: "ai" });
  } catch (error) {
    console.error("summary failed", error);
    return Response.json({ summary: fallback, source: "rules" });
  }
}
