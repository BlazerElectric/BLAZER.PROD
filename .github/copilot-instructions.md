# Repository Instructions & Operating Guidelines

## Overview
This repository manages product catalog enrichment and recommendation datasets for an electrical wholesale e-commerce storefront. It generates two storefront carousels:
1. **Carousel 1 (`product_accessories.csv`):** Jobsite installation complements (conduit, wire, rough-in boxes, covers, fittings, tools, PPE, wire nuts).
2. **Carousel 2 (`similar_products.csv`):** Direct substitutes and sibling ratings (alternative sizes, amperages, phases, finishes).

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
- Generates `similar_products.csv`.

### Step 4: Generate Accessories & Companions (Carousel 1)
```bash
python3 generate_accessories.py --checkpoint product_accessories.csv
```
- Uses `product_accessories.csv` as the checkpoint: existing products are preserved, and only new/unprocessed products build candidate pools and invoke Gemini.
- Preserves all locked hero packages in `curated_accessories.csv`.
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
- Fills any short rows (under 10 items) to guarantee full 10/10 coverage across all 8,477 items.
- Enforces trade sanity rules (e.g. strips expensive tools off low-cost fasteners).
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
GitHub Actions also runs this suite on pushes and pull requests.
