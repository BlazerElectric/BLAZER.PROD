import "server-only";
import { readFileSync } from "node:fs";
import path from "node:path";
import { isLikelyHeading, parseOrderText } from "../parser/orderParser";
import { SearchEngine } from "../search/engine";
import { parseSlangCsv, type SlangEntry } from "../search/slang";
import type { CatalogFile, ResolvedLine } from "../types";

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
  for (const line of parseOrderText(text)) {
    const match = engine.match(line.query, { unit: line.unit });
    if (isLikelyHeading(line, match.confidence)) skipped.push(line.raw);
    else lines.push({ ...line, ...match });
  }
  return { lines, skipped };
}
