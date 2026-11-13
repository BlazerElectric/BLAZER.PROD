import { z } from "zod";
import { resolveOrderText } from "@/lib/catalog/server";

const Body = z.object({ text: z.string().max(50_000) });

export async function POST(request: Request) {
  const parsed = Body.safeParse(await request.json().catch(() => null));
  if (!parsed.success) {
    return Response.json({ error: "Expected { text } under 50,000 characters." }, { status: 400 });
  }
  return Response.json(resolveOrderText(parsed.data.text));
}
