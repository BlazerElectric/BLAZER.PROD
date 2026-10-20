# BLAZER.PROD

Recommendation and catalog enrichment engine for electrical wholesale e-commerce.

## Dual-Carousel Architecture

The storefront recommendation engine uses two distinct, complementary carousels:

1. **Carousel 1: Accessories & Installation Hardware (`product_accessories.csv`)**
   - **Purpose:** Surfaces what an electrician physically needs to install and complete the assembly on the job. Includes mating connectors, rough-in boxes, covers, mounting hardware, fasteners, grounding electrodes, wire, and installation consumables.
   - **Engine:** `generate_accessories.py` powered by `accessory_rules.csv`, `job_kits.csv`, and Google Gemini (`gemini-3.1-flash-lite`).
   - **Format:** `PROD_ID`, `PROD_ID_REL1` through `PROD_ID_REL10`.
   - **Ordering Principle:** Strictly ordered by installation workflow:
     * **Slot 1 (`REL1`):** Direct mating part or required electrical connection (e.g. mating plug/receptacle, lamp, conduit connector, fuse, ground rod).
     * **Slot 2 (`REL2`):** Mounting hardware, rough-in box, or enclosure.
     * **Slot 3 (`REL3`):** Wire, cable, straps, or fasteners to route and secure the assembly.
     * **Slots 4–10 (`REL4`–`REL10`):** Finishing plates, safety tags/labels, anti-oxidant, tape, wire nuts.

2. **Carousel 2: Similar & Alternative Products (`similar_products.csv`)**
   - **Purpose:** Surfaces direct substitutes, sibling ratings, alternative sizes, and color variants (e.g. 15A vs 20A breaker, 12/2 vs 12/3 MC cable, White vs Gray receptacle, 3/4" vs 1" conduit body).
   - **Engine:** `generate_similar_products.py` (100% deterministic, 0 API tokens, ~30s execution).
   - **Format:** `PROD_ID`, `SIMILAR_ID_1` through `SIMILAR_ID_10`.
   - **Ordering Principle:** Scored by compatibility dimensions (exact series match, tool sub-type, fixture sub-type, brand, color/finish, wire construction), then sorted descending by:
     $$\text{Compatibility Score} \longrightarrow \text{6-Month Sales Volume} \longrightarrow \text{Product ID}$$
     Slot 1 (`SIMILAR_ID_1`) is always the #1 closest matching, highest-selling alternative.

---

## Enriched Catalog & Manufacturer Normalization

Known leading manufacturer-prefix errors are corrected deterministically from `MFR Prefix.csv` after enrichment, preserving true manufacturer identity without extra Gemini calls:
* `RAC` $\to$ **RACO** (Hubbell RACO steel boxes and conduit fittings; distinguished from `RAT` $\to$ Rack-A-Tiers)
* `DBL` $\to$ **Diablo**
* `COP` $\to$ **Copper Generic**
* `ALW` $\to$ **Aluminum Generic**
* `DOT` $\to$ **Dottie**
* `RAT` $\to$ **Rack-A-Tiers**
* Commodity codes `NIP`, `ELB`, `CON`, `PVC`, `LQT`, and `MC` normalize to generic product-class labels.
* Confirmed vendor prefixes `CUL`, `HDN`, `POR`, `GLA`, `VC`, `PNS`, and `DIO` are authoritative for Cully, Haydon Strut, Tiffin Insulators, Glas Masters, Vertical Cable, Pass & Seymour, and Diode LED.

To apply normalization to an enriched catalog:
```sh
python manufacturer_normalization.py \
  --input enriched_products.csv \
  --output enriched_products.csv
```

To run unit tests verifying normalization:
```sh
python -m unittest discover tests
```

---

## Carousel 1 Workflow (`generate_accessories.py`)

### 1. Preview Candidate Pools (Zero API Cost)
```sh
python generate_accessories.py --dry-run
```
Reports candidate pool health, identifies thin or zero pools, and displays top candidate frequencies.

### 2. Taxonomy Rules & Subcategory Interleaving
* Rules live in `accessory_rules.csv`. Each row maps a target `Main Category > Subcategory 1` to an accessory node.
* **Bidirectional Relationships:** Key trade relationships are wired bidirectionally (Boxes $\leftrightarrow$ Devices, Disconnects $\leftrightarrow$ Emergency Warning Labels, Conduit $\leftrightarrow$ Fittings, Strut $\leftrightarrow$ Threaded Rod, Solar Wire $\leftrightarrow$ MC4 Connectors, Fuses $\leftrightarrow$ Disconnects).
* **Always-Interleaved Balancing:** `build_candidates()` round-robins across distinct subcategories before presenting candidates to Gemini, preventing a single category (e.g. bare copper wire) from dominating the top candidate slots.
* **Capacity:** `MIN_SUGGESTIONS = 10` and `FALLBACK_FILL = 12` ensure carousels are populated up to 10 items. If Gemini returns 5–7 items, balanced subcategory top-up seamlessly completes slots 8–10 from the candidate pool.

### 3. Testing with a Sample
```sh
python generate_accessories.py --sample 20 --seed 42
```
Samples 20 active products, calls Gemini, and writes output to `product_accessories.sample.csv` with a dedicated sample checkpoint.

### 4. Full Catalog Generation
```sh
python generate_accessories.py
```
Generates all 8,477 products into `product_accessories.csv` using checkpointing (`product_accessories.v5.checkpoint.csv`). Automatically resumes if interrupted.

---

## Carousel 2 Workflow (`generate_similar_products.py`)

To generate alternative and substitute products across all catalog items:

```sh
python generate_similar_products.py \
  --input enriched_products.csv \
  --output similar_products.csv
```

* Evaluates each product within its taxonomy node.
* Enforces breaker series compatibility (Eaton BR with BR, Homeline with Homeline).
* Keeps tool sub-types aligned (screwdrivers with screwdrivers, strippers with strippers).
* Separates heavy-duty wiring devices (dryer/range 30A/50A) from standard 15A duplex receptacles.
* Aligns wire construction (Romex with Romex, THHN with THHN, Aluminum with Aluminum).
* Ranks results by 6-month sales order volume.

---

## Rule Diagnostics & Quality Tools

* `check_pools.py`: Audits candidate pools for gang, color, trade size, and saturation conflicts.
* `job_kits.csv`: Defines multi-trade assembly scenarios (EV charger, Service upgrade, Hot tub, Rough-in).
* `tests/`: Unit test suite covering prefix normalization, trade size extraction, and conflict detection. Run via:
  ```sh
  python -m unittest discover tests
  ```

---

## Future Updates: Adding New Products or Dropping Discontinued SKUs

When receiving a new `products.csv` or updated `BLAZER.RANK.csv`, run this 4-step sequence:

```sh
# 1. Enrich only the new SKUs (existing products load from checkpoint in seconds)
python3 enrich_catalog.py

# 2. Automatically map website categories (runs locally in ~2 seconds, 0 API cost)
python3 map_taxonomy.py

# 3. Regenerate Similar Products (Carousel 2, runs locally in ~45s, 0 API cost)
python3 generate_similar_products.py

# 4. Generate Accessories (Carousel 1, processes only new SKUs using checkpoint)
python3 generate_accessories.py --checkpoint product_accessories.csv
```

