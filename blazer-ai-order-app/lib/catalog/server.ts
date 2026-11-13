import "server-only";
import { readFileSync } from "node:fs";
import path from "node:path";
import { isLikelyHeading, parseOrderText } from "../parser/orderParser";
import { inferFittingMaterialContext, inferBreakerFamilyContext, splitQuoteSections } from "../search/quoteContext";
import { SearchEngine } from "../search/engine";
import { parseSlangCsv, type SlangEntry } from "../search/slang";
import type { CatalogFile, ResolvedLine } from "../types";
import { tokenize } from "../search/normalize";

interface CatalogState {
  engine: SearchEngine;
  lastUpdated: string;
  totalProducts: number;
  loadedAt: string;
  buildMs: number;
}

const globalForCatalog = globalThis as unknown as { __blazerCatalog?: CatalogState };

export function catalogPath(): string {
  return process.env.CATALOG_PATH || path.join(process.cwd(), "data", "blazer_web_catalog.json");
}

/** Load blazer_web_catalog.json once per server instance and build the in-memory search index. */
export function getCatalog(): CatalogState {
  if (globalForCatalog.__blazerCatalog) return globalForCatalog.__blazerCatalog;

  const started = Date.now();
  const override = process.env.CATALOG_PATH;
  // Keep the default path statically scoped so output tracing only bundles data/.
  const raw = override
    ? readFileSync(/*turbopackIgnore: true*/ override, "utf8")
    : readFileSync(path.join(process.cwd(), "data", "blazer_web_catalog.json"), "utf8");
  const file = JSON.parse(raw) as CatalogFile;
  if (!file || typeof file.products !== "object") {
    throw new Error(`Invalid catalog file at ${catalogPath()}: missing "products" object`);
  }
  const engine = new SearchEngine(file.products, { slang: loadSlang() });
  const state: CatalogState = {
    engine,
    lastUpdated: file.last_updated,
    totalProducts: engine.size,
    loadedAt: new Date().toISOString(),
    buildMs: Date.now() - started,
  };
  globalForCatalog.__blazerCatalog = state;
  return state;
}

/** Electrician slang dictionary (config/trade_slang.csv); a bad file must not take search down. */
function loadSlang(): SlangEntry[] {
  try {
    return parseSlangCsv(readFileSync(path.join(process.cwd(), "config", "trade_slang.csv"), "utf8"));
  } catch (error) {
    console.warn(`[catalog] trade slang not loaded: ${(error as Error).message}`);
    return [];
  }
}

export function getEngine(): SearchEngine {
  return getCatalog().engine;
}

export interface ResolvedOrder {
  lines: ResolvedLine[];
  /** Job names / section headings that were left out of the BOM. */
  skipped: string[];
}

export function resolveOrderText(text: string): ResolvedOrder {
  const engine = getEngine();
  const lines: ResolvedLine[] = [];
  const skipped: string[] = [];
  const parsed = splitQuoteSections(parseOrderText(text));
  skipped.push(...parsed.headings.map((line) => line.raw));
  for (const section of parsed.sections) {
    const sectionStart = lines.length;
    for (const [index, line] of section.entries()) {
      let materialContext = inferFittingMaterialContext(section, index);
      const previous = lines.length > sectionStart ? lines.at(-1) : undefined;
      if (!materialContext && tokenize(line.query).includes("4sq") && tokenize(line.query).includes("cover") &&
          previous?.product?.category === "Boxes & Enclosures" &&
          tokenize(`${previous.product.title} ${previous.product.specs}`).includes("steel")) {
        materialContext = { material: "steel", tradeSize: "4sq" };
      }
      if (line.needsClarification) {
        lines.push({ ...line, confidence: "none", product: null, alternatives: [], similar: [], note: line.reviewNote });
        continue;
      }
      const inferredFamily = inferBreakerFamilyContext(section, index);
      const match = engine.match(inferredFamily ? `${line.query} ${inferredFamily}` : line.query, { unit: line.unit, materialContext });
      const notes = [
        match.note, line.reviewNote,
        ...(inferredFamily ? [`${inferredFamily.toUpperCase()} family inferred from this quote's panel; confirm breaker compatibility`] : []),
      ].filter(Boolean);
      if (notes.length) {
        match.note = notes.join(". ");
        if (match.confidence === "high") match.confidence = "medium";
      }
      if (isLikelyHeading(line, match.confidence)) skipped.push(line.raw);
      else lines.push({ ...line, ...match });
    }
  }
  return { lines, skipped };
}
