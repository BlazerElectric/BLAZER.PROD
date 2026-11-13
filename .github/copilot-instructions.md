# Repository Instructions & Operating Guidelines

## Overview
This repository manages product catalog enrichment and recommendation datasets for an electrical wholesale e-commerce storefront. It generates two storefront carousels:
1. **Carousel 1 (`product_accessories.csv`):** Jobsite installation complements (conduit, wire, rough-in boxes, covers, fittings, tools, PPE, wire nuts).
2. **Carousel 2 (`similar_products.csv`):** Direct substitutes and sibling ratings (alternative sizes, amperages, phases, finishes).

## Architecture
Flat, single-directory Python project: a six-stage data pipeline that transforms `products.csv` (raw `BLAZER ID,PRODUCT` rows) into `blazer_web_catalog.json` for the Vercel-hosted storefront.

The one exception is `blazer-ai-order-app/`, a separate Next.js 16 + TypeScript + Tailwind app (the AI Order Desk). It reads `blazer_web_catalog.json`, which is copied into `data/` by `npm run sync-catalog` / `prebuild`. The Python pipeline must keep the JSON schema stable for it.
- Work inside that directory with npm: `npm run lint`, `npm run typecheck`, `npm test` (Vitest) and `npm run build`. CI runs these in `.github/workflows/order-app.yml`.
- Gemini runs only server-side (`lib/ai/gemini.ts`). AI suggestions must be resolved to real catalog SKUs through `lib/search/engine.ts`.
- Billtrust DOM automation lives in `public/billtrust-bridge.js`. It talks to the app through the origin-checked postMessage protocol in `lib/billtrust/protocol.ts`; keep both in sync, and never use `"*"` origins or inject HTML.

```
products.csv ──► enrich_catalog.py (Gemini) ──► enriched_products.csv
                                                   │
          ┌────────────────────────────────────────┼─────────────────────────┐
          ▼                                        ▼                         ▼
  map_taxonomy.py                 generate_similar_products.py   generate_accessories.py (Gemini)
  product_taxonomy.csv                 similar_products.csv        product_accessories.csv
                                                   │                         │
                                                   └──────────┬──────────────┘
                                                              ▼
                                        audit_and_fix_accessories.py ──► export_web_catalog.py
                                                                          blazer_web_catalog.json
```

- **Only `enrich_catalog.py` and `generate_accessories.py` call Gemini** (`gemini-3.1-flash-lite`, async with concurrency 12, Pydantic structured output, tenacity retries, checkpoint every 100 rows). Every other script is deterministic and free — never introduce API calls into them.
- **`generate_accessories.py` doubles as the shared rules library.** `map_taxonomy.py`, `generate_similar_products.py`, and `audit_and_fix_accessories.py` all `import generate_accessories as ga` for product-pattern overrides, candidate pools, and trade-size parsing. Changes to its helpers ripple across the whole pipeline.
- **Checkpoint safety:** Gemini scripts call `checkpoint_safety.require_checkpoint(path, allow_new)`, which raises if the checkpoint is missing unless `--allow-new-checkpoint` is passed. Preserve this guard in any new Gemini workflow.
- **`manufacturer_normalization.py`** applies deterministic manufacturer-prefix corrections (data in `MFR Prefix.csv`) to both `enriched_products.csv` and the checkpoint; it is also runnable standalone for zero-API correction refreshes.

## Data Conventions
- Input catalogs key on `BLAZER ID`; generated relationship CSVs key on `PROD_ID`. Accessories use columns `PROD_ID_REL1`–`PROD_ID_REL10`; similar products use `SIMILAR_ID_1`–`SIMILAR_ID_10`. IDs are **strings** — always read CSVs with `pd.read_csv(..., dtype=str, keep_default_na=False)` and never let pandas coerce them to numbers or NaN.
- `BLAZER.RANK.csv` holds 6-month order counts (`TIMES ORDERED PAST 6 MONTHS`) used for popularity ranking; its header starts with a BOM, so read it with `encoding="utf-8-sig"`.
- `curated_accessories.csv` contains locked hero packages keyed by `PROD_ID`. Generators and the audit must preserve these rows verbatim; missing accessory IDs in locked packages for active products are a hard error — fix the curated rows, don't bypass.
- `accessory_rules.csv` maps taxonomy paths to accessory taxonomy paths with brand/color/size constraints; `notes=verified_fit` bypasses popularity filtering only for restrictive, manufacturer-verified model matches.
- Tracked in Git: `enriched_products.checkpoint.csv`, `product_accessories.csv`, `similar_products.csv`. Ignored: all other `*.checkpoint*.csv`, `*.sample.csv`, and `.env` (see `.gitignore`).

## Developer Environment
- Python (CI uses 3.14); dependencies in `requirements.txt` (`google-genai`, `pandas`, `pydantic`, `tqdm`, `python-dotenv`, `tenacity`). Install with `python -m pip install -r requirements.txt`.
- `GEMINI_API_KEY` must be set in the environment or a local `.env` file. Never commit secrets.
- Run all scripts from the repository root; modules import each other as top-level names.

---

## Standard Workflow for Adding New Products or Refreshing the Catalog

When `products.csv` or `BLAZER.RANK.csv` is updated with new products, dropped SKUs, or refreshed 6-month sales volume, run the following pipeline:

The canonical `enriched_products.checkpoint.csv`, `product_accessories.csv`, and `similar_products.csv` are tracked in Git; other checkpoints and samples remain ignored. Restore missing checkpoints from trusted backups; do not use `--allow-new-checkpoint` without explicit approval because it may incur Gemini costs. A new sample also requires this option the first time.

### Step 1: Enrich New Products
```bash
python3 enrich_catalog.py
```
- Uses `enriched_products.checkpoint.csv` so **only brand-new SKUs** call Gemini.
- Existing SKUs load from checkpoint in seconds for $0.00.
- Normalizes manufacturer prefixes deterministically via `manufacturer_normalization.py`.
- Verified model corrections also repair cached metadata. For a zero-API correction refresh, run `manufacturer_normalization.py` with each of `enriched_products.csv` and `enriched_products.checkpoint.csv` as its own input/output, then continue with taxonomy, similarity, audit and export. Do not rewrite raw descriptions or guess unverified specifications.
- Generates `enriched_products.csv`.

### Step 2: Map Website Taxonomy
```bash
python3 map_taxonomy.py
```
- Matches enriched products against `CATEGORY.csv` using weighted token (TF-IDF) scoring, then applies explicit product-pattern overrides from `generate_accessories.py`.
- Automatically assigns `Main Category`, `Subcategory 1`, `Subcategory 2`, etc.
- Generates `product_taxonomy.csv` in ~2 seconds with 0 API calls.

### Step 3: Regenerate Similar Products (Carousel 2)
```bash
python3 generate_similar_products.py
```
- 100% deterministic rule-based similarity scoring (about 2 minutes for the current 8,477-product catalog in this environment; 0 API tokens).
- Scores compatibility across trade sizes, breaker series, phase count (1-phase vs 3-phase), breaker amperage, voltage ratings (600V vs 250V), tool sub-types, conduit schedules (Sch 40 vs Sch 80), and cable types.
- Ranks candidates by compatibility score, then 6-month sales volume from `BLAZER.RANK.csv`.
- Functional types are hard-filtered before scoring; exact fit ranks ahead of brand/popularity. Preserve grounding/neutral conductor roles, WR GFCI requirements, and breaker standard/tandem, protection and neutral-connection distinctions. Sibling ratings/sizes are not certified substitutes.
- Generates `similar_products.csv`.

### Step 4: Generate Accessories & Companions (Carousel 1)
```bash
python3 generate_accessories.py --checkpoint product_accessories.csv
```
- Uses `product_accessories.csv` as the checkpoint: existing products are preserved, and only new/unprocessed products build candidate pools and invoke Gemini.
- Preserves all locked hero packages in `curated_accessories.csv`.
- Generation and auditing reject missing accessory IDs in locked packages for active catalog products; repair the curated rows before retrying.
- If specific rule sets (e.g. rules 560-879) need targeted rebuilding, use:
  ```bash
  python3 generate_accessories.py --checkpoint product_accessories.csv --rebuild-rules
  ```
- Generates `product_accessories.csv`.

### Step 5: Deterministic Quality Audit & Fill (0 API Calls)
```bash
python3 audit_and_fix_accessories.py
```
- Preserves all 57+ locked hero packages in `curated_accessories.csv`.
- Fills short rows up to 10 items where compatible candidates exist. Short or empty rows are intentional when no verified accessories exist; never bypass compatibility guards to force ten items.
- `notes=verified_fit` in `accessory_rules.csv` bypasses popularity filtering only for restrictive, manufacturer-verified model matches.
- Enforces trade sanity rules (e.g. strips expensive tools off low-cost fasteners).
- Uses explicit box knockout sizes, blank-cover/device cutouts, square-cover 8-32 threads, ground-rod clamp diameters, threaded-body transitions, and H-164 spring-nut fit. Locked rows bypass cleanup; leave uncertain curated changes for review.
- Runs locally with $0.00 API cost; runtime varies by environment and catalog size.
- Automatically executes Step 6 upon completion.

### Step 6: Export Web Catalog Master JSON
```bash
python3 export_web_catalog.py
```
- Compiles core catalog data, rankings, and dual-carousel relationship matrices into `blazer_web_catalog.json`. The exported category and subcategory fields come from `enriched_products.csv`; `product_taxonomy.csv` is not merged into the JSON.
- Requires both carousel CSVs by default; `--allow-partial` permits an intentional partial export with warnings and empty arrays for missing carousels. The audit's automatic export remains strict.
- Serves as the single source of truth data bridge for the Vercel-hosted ordering app.
- Runs locally in <1 second with $0.00 API cost.

---

## Quality Assurance & Verification
Always verify changes by running the unit test suite:
```bash
python3 -m unittest discover tests
```
- Tests use plain `unittest` (not pytest) in `tests/test_<module>.py`, importing pipeline modules as top-level names — run from the repository root.
- Scope runs while iterating: `python3 -m unittest tests.test_generate_accessories`.
- Tests build small in-memory `pandas` DataFrames/dicts instead of reading the real catalog CSVs; follow that pattern for new cases.
- GitHub Actions (`.github/workflows/tests.yml`) runs the suite on pushes and pull requests.

## Cost & Safety Rules
- Prefer deterministic steps (2, 3, 5, 6) for verification; they cost $0.00. Only run Gemini steps (1, 4) when new SKUs actually need processing.
- Never delete or regenerate tracked checkpoints without explicit approval; `--allow-new-checkpoint` can incur significant Gemini costs across the ~8,500-product catalog.
- Do not rewrite raw `PRODUCT` descriptions or invent unverified manufacturer specifications; corrections belong in `manufacturer_normalization.py` / `MFR Prefix.csv`.
