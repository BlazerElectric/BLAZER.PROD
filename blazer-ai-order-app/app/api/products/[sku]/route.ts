import type { NextRequest } from "next/server";
import { getEngine } from "@/lib/catalog/server";

export async function GET(_request: NextRequest, ctx: RouteContext<"/api/products/[sku]">) {
  const { sku } = await ctx.params;
  const engine = getEngine();
  const product = engine.get(sku);
  if (!product) return Response.json({ error: "Unknown SKU" }, { status: 404 });
  return Response.json({
    product: engine.summary(sku),
    accessories: engine.summariesFor(product.accessories),
    similar: engine.summariesFor(product.similar),
  });
}
