import { connection } from "next/server";
import { aiConfigured } from "@/lib/ai/gemini";
import { getCatalog } from "@/lib/catalog/server";

export async function GET() {
  await connection();
  const catalog = getCatalog();
  return Response.json({
    status: "ok",
    totalProducts: catalog.totalProducts,
    lastUpdated: catalog.lastUpdated,
    indexBuildMs: catalog.buildMs,
    aiEnabled: aiConfigured(),
  });
}
