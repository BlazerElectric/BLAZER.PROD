#!/usr/bin/env node
// Copy the pipeline's blazer_web_catalog.json into data/ so the server bundle can read it.
// Source: $CATALOG_SOURCE, else ../blazer_web_catalog.json (repo root, produced by export_web_catalog.py).
import { copyFileSync, existsSync, mkdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const appRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const source = path.resolve(appRoot, process.env.CATALOG_SOURCE || "../blazer_web_catalog.json");
const target = path.join(appRoot, "data", "blazer_web_catalog.json");

if (!existsSync(source)) {
  if (existsSync(target)) {
    console.warn(`[sync-catalog] ${source} not found; keeping existing ${path.relative(appRoot, target)}.`);
    process.exit(0);
  }
  console.error(`[sync-catalog] No catalog found at ${source} and no copy in data/. Run export_web_catalog.py first or set CATALOG_SOURCE.`);
  process.exit(1);
}

const catalog = JSON.parse(readFileSync(source, "utf8"));
if (!catalog || typeof catalog.products !== "object") {
  console.error(`[sync-catalog] ${source} has no "products" object.`);
  process.exit(1);
}
mkdirSync(path.dirname(target), { recursive: true });
copyFileSync(source, target);
const mb = (statSync(target).size / 1e6).toFixed(1);
console.log(`[sync-catalog] ${Object.keys(catalog.products).length} products (${mb} MB, updated ${catalog.last_updated}) -> data/`);
