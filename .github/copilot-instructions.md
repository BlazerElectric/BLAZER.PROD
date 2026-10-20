# Repository Instructions & Operating Guidelines

## Overview
This repository manages product catalog enrichment and recommendation datasets for an electrical wholesale e-commerce storefront. It generates two storefront carousels:
1. **Carousel 1 (`product_accessories.csv`):** Jobsite installation complements (conduit, wire, rough-in boxes, covers, fittings, tools, PPE, wire nuts).
2. **Carousel 2 (`similar_products.csv`):** Direct substitutes and sibling ratings (alternative sizes, amperages, phases, finishes).

---

## Standard Workflow for Adding New Products or Refreshing the Catalog

When `products.csv` or `BLAZER.RANK.csv` is updated with new products, dropped SKUs, or refreshed 6-month sales volume, run the following pipeline:

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
- Matches enriched products against `CATEGORY.csv` using weighted token (TF-IDF) scoring.
- Automatically assigns `Main Category`, `Subcategory 1`, `Subcategory 2`, etc.
- Generates `product_taxonomy.csv` in ~2 seconds with 0 API calls.

### Step 3: Regenerate Similar Products (Carousel 2)
```bash
python3 generate_similar_products.py
```
- 100% deterministic rule-based similarity scoring (runs in ~45 seconds, 0 API tokens).
- Scores compatibility across trade sizes, breaker series, phase count (1-phase vs 3-phase), breaker amperage, voltage ratings (600V vs 250V), tool sub-types, conduit schedules (Sch 40 vs Sch 80), and cable types.
- Ranks candidates by compatibility score, then 6-month sales volume from `BLAZER.RANK.csv`.
- Generates `similar_products.csv`.

### Step 4: Generate Accessories & Companions (Carousel 1)
```bash
python3 generate_accessories.py --checkpoint product_accessories.csv
```
- Uses `product_accessories.csv` as the checkpoint: existing products are preserved, and only new/unprocessed products build candidate pools and invoke Gemini.
- If specific rule sets (e.g. rules 560-755) need targeted rebuilding, use:
  ```bash
  python3 generate_accessories.py --checkpoint product_accessories.csv --rebuild-rules
  ```
- Generates `product_accessories.csv`.

---

## Quality Assurance & Verification
Always verify changes by running the unit test suite:
```bash
python3 -m unittest discover tests
```
