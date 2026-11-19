import type { ProductSummary } from "../types";

export interface BulkPackageQuantity {
  quantity: number;
  packageLength: number;
  excessLength: number;
  assumedLength: boolean;
}

export interface BoxPackageQuantity {
  quantity: number;
  piecesPerBox: number;
  excessPieces: number;
}

export function catalogPackageLength(
  product: Pick<ProductSummary, "category" | "subcategory" | "title" | "specs">,
): number | undefined {
  if (product.category !== "Wire, Cable & Cord" && product.category !== "Conduit, Strut & Raceways") {
    return undefined;
  }
  const text = `${product.title} ${product.specs}`;
  const match = text.match(/\b([\d,]+)\s*(?:feet|foot|ft)\b/i);
  if (match) return Number.parseInt(match[1].replace(/,/g, ""), 10);

  const isEmtConduit =
    product.category === "Conduit, Strut & Raceways" &&
    product.subcategory === "Conduit - Metallic" &&
    /\bemt\b/i.test(text) &&
    !/\b(?:elbow|coupling|connector|nipple)\b/i.test(text);
  return isEmtConduit ? 10 : undefined;
}

export function quantityInCatalogPackages(
  quantity: number,
  unit: string,
  product: Pick<ProductSummary, "category" | "subcategory" | "title" | "specs">,
): BulkPackageQuantity | undefined {
  if (unit !== "ft" || !Number.isFinite(quantity) || quantity < 1) return undefined;
  const packageLength = catalogPackageLength(product);
  if (!packageLength) return undefined;
  const packageQuantity = Math.ceil(quantity / packageLength);
  return {
    quantity: packageQuantity,
    packageLength,
    excessLength: packageQuantity * packageLength - quantity,
    assumedLength: !/\b([\d,]+)\s*(?:feet|foot|ft)\b/i.test(`${product.title} ${product.specs}`),
  };
}

export function quantityInCatalogBoxes(
  quantity: number,
  unit: string,
  product: Pick<ProductSummary, "title" | "specs">,
): BoxPackageQuantity | undefined {
  if (unit !== "ea" || !Number.isFinite(quantity) || quantity < 1) return undefined;
  const match = `${product.title} ${product.specs}`.match(
    /\bsold by the box\s*\(\s*([\d,]+)\s*(?:ct|count)\s*\)/i,
  );
  if (!match) return undefined;
  const piecesPerBox = Number.parseInt(match[1].replace(/,/g, ""), 10);
  if (!piecesPerBox) return undefined;
  const packageQuantity = Math.ceil(quantity / piecesPerBox);
  return {
    quantity: packageQuantity,
    piecesPerBox,
    excessPieces: packageQuantity * piecesPerBox - quantity,
  };
}
