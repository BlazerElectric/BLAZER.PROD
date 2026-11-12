# BLAZER.PROD

Recommendation and catalog enrichment engine for electrical wholesale e-commerce.

## Setup

Install the Python dependencies with:
```sh
python3 -m pip install -r requirements.txt
```

`enrich_catalog.py` and Gemini-backed accessory generation require `GEMINI_API_KEY` in the environment or a local `.env` file. Keep API keys out of Git. Taxonomy mapping, similarity generation, auditing, and JSON export are deterministic and do not require the key.

For optional per-script command-line arguments, run the script with `--help`.

### Checkpoint and Export Safety

The canonical `enriched_products.checkpoint.csv`, `product_accessories.csv`, and `similar_products.csv` are tracked in Git as recovery data. Commit and push updated copies after a catalog refresh. Other checkpoints and sample files remain Git-ignored, including the default `product_accessories.v6.checkpoint.csv`; back up any other checkpoint you rely on separately.

If a checkpoint is missing, Gemini-backed commands stop before making API calls. Restore the matching checkpoint or pass `--allow-new-checkpoint` to deliberately start a new one. Starting over may regenerate many products and incur Gemini costs; for `--sample` and `--top-selling`, the separate sample checkpoint also requires this option on its first run.

The JSON exporter requires both carousel CSVs by default. Restore or regenerate missing files before exporting. Use `--allow-partial` only for an intentional partial export: missing carousel files will produce empty recommendation arrays and a warning. The audit's automatic export uses the strict default.

## Dual-Carousel Architecture

The storefront recommendation engine uses two distinct, complementary carousels:

1. **Carousel 1: Accessories & Installation Hardware (`product_accessories.csv`)**
   - **Purpose:** Surfaces what an electrician physically needs to install and complete the assembly on the job. Includes mating connectors, rough-in boxes, covers, mounting hardware, fasteners, grounding electrodes, wire, and installation consumables.
   - **Engine:** `generate_accessories.py` powered by `accessory_rules.csv`, `job_kits.csv`, and Google Gemini (`gemini-3.1-flash-lite`).
   - **Format:** `PROD_ID`, `PROD_ID_REL1` through `PROD_ID_REL10`.
   - **Ordering Principle:** Hybrid popularity-weighted installation workflow:
     * **Slots 1–3 (`REL1`–`REL3`):** High-demand core installation essentials (e.g. top-selling matching connectors, couplings, matching wallplates, primary mounting rough-in boxes/enclosures, or mating plugs). Proven high-volume workhorses take priority over obscure low-demand mating parts.
     * **Slots 4–7 (`REL4`–`REL7`):** High-utility installation complements and routing/securing hardware (e.g. conduit straps, hangers, fittings, wire, cable, mud rings, grounding pigtails, clamps).
     * **Slots 8–10 (`REL8`–`REL10`):** Finishing items, trade consumables, and staples (e.g. locknuts, electrical tape, wire nuts, pulling lube, covers, labels, duct seal).

2. **Carousel 2: Similar & Alternative Products (`similar_products.csv`)**
   - **Purpose:** Surfaces direct substitutes, sibling ratings, alternative sizes, and color variants (e.g. 15A vs 20A breaker, 12/2 vs 12/3 MC cable, White vs Gray receptacle, 3/4" vs 1" conduit body).
   - **Compatibility:** Similarity is not a guarantee of interchangeability. In particular, breaker alternatives can differ in amperage, pole count, protection, or mounting; verify the panel listing and application before use.
   - **Engine:** `generate_similar_products.py` (100% deterministic, 0 API tokens).
   - **Format:** `PROD_ID`, `SIMILAR_ID_1` through `SIMILAR_ID_10`.
   - **Ordering Principle:** Scored by compatibility dimensions (exact series match, tool sub-type, fixture sub-type, brand, color/finish, wire construction), then sorted descending by:
     Compatibility dimensions include series, ratings, phase/pole, trade size, tool or fixture subtype, color/finish, and cable or conduit construction. Ties are broken by 6-month sales volume and product ID.
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
* `MIN` is Minerallac, not Rack-A-Tiers or Dottie; `CAR` is Carlon/Kraloy Generic, not Vertical Cable.
* Verified model corrections in `manufacturer_normalization.py` repair cached specifications as well as fresh enrichment. They include 38AST cable type/knockout, 664-WG four-way toggle function, H-164-OS channel dimensions/gauge, 902C raised depth, BST box support, and VC9982 PVC solvent cement.
* Unambiguous raw-catalog dimensions also correct RACO 2104's one-inch trade size and Cully 52416J's one-inch screw length; their old enrichment incorrectly said one-half inch.

To apply normalization to an enriched catalog:
```sh
python manufacturer_normalization.py \
  --input enriched_products.csv \
  --output enriched_products.csv
```

Apply the same command to `enriched_products.checkpoint.csv` as both input and output when refreshing verified metadata, so the tracked recovery data stays current. Neither normalization command calls Gemini. Keep raw `products.csv` descriptions unchanged; unverified material, dimension, and vendor-fit claims require source review rather than guesses.

To run unit tests verifying normalization:
```sh
python -m unittest discover tests
```

---

## Website Taxonomy Mapping (`map_taxonomy.py`)

```sh
python3 map_taxonomy.py
```

Maps enriched product descriptions to paths in `CATEGORY.csv` using IDF-weighted token overlap, then applies explicit product-pattern overrides from `generate_accessories.py`. Writes the best taxonomy path, confidence information, and runner-up to `product_taxonomy.csv` without API calls. The web JSON currently takes its `category` and `subcategory` fields from `enriched_products.csv`; it does not merge the deeper paths in `product_taxonomy.csv`.

---

## Carousel 1 Workflow (`generate_accessories.py`)

### 1. Preview Candidate Pools (Zero API Cost)
```sh
python generate_accessories.py --dry-run
```
Reports candidate pool health, identifies thin or zero pools, and displays top candidate frequencies.

### 2. Taxonomy Rules & Subcategory Interleaving
* Rules live in `accessory_rules.csv`. Each row maps a target `Main Category > Subcategory 1` to an accessory node.
* `notes=verified_fit` bypasses the sales-volume minimum only for explicitly verified model-to-model accessory rules. Use restrictive model patterns and manufacturer documentation, not generic category matches.
  - FLBC8500 cover family: [Arlington specification sheet](https://www.aimedia.co/media/spec-sheets/FLBC8500.pdf). FLBC8510 is single-gang; FLBC8520 requires a two-box assembly.
  - L11-TL3: [Sprecher + Schuh lug hardware guidance](https://www.sprecherschuh.com/api/library/techdocs/posts/2015/06/L11-TL-lug-hardware.html).
  - Halco 87200-87205: [Type B installation guide](https://www.halcolighting.com/media/resource/Halco-t8-ho-he%20install%20guide.pdf). Do not infer socket shunting or driver compatibility from generic LED descriptions.
* **Bidirectional Relationships:** Key trade relationships are wired bidirectionally (Boxes $\leftrightarrow$ Devices, Disconnects $\leftrightarrow$ Emergency Warning Labels, Conduit $\leftrightarrow$ Fittings, Strut $\leftrightarrow$ Threaded Rod, Solar Wire $\leftrightarrow$ MC4 Connectors, Fuses $\leftrightarrow$ Disconnects).
* **Subcategory Interleaving:** Candidate pools are interleaved across distinct subcategories, with subcategories ordered by their strongest sales volume, so one subcategory cannot dominate the early candidate slots.
* **Capacity:** `MIN_SUGGESTIONS = 10` and `FALLBACK_FILL = 12` target up to 10 items when compatible candidates exist. Short or empty rows are preferable to unverified accessories; do not bypass compatibility guards to fill slots.
* **Similar products are not certified substitutes:** sibling coil voltages, lug conductor ranges, GFCI amperages/protection features, and LED tube wiring types must be checked for the installation. CA7-TD855 currently has no verified 24V companion contactor in this catalog, so its accessory list is intentionally empty.

### 3. Testing with a Sample
```sh
python generate_accessories.py --sample 20 --seed 42 --allow-new-checkpoint
```
Samples 20 active products, calls Gemini, and writes output to `product_accessories.sample.csv` with a dedicated sample checkpoint.

Other targeted options:
```sh
python generate_accessories.py --dry-run
python generate_accessories.py --top-selling 100 --top-offset 50 --allow-new-checkpoint
python generate_accessories.py --rebuild-rules
```
`--dry-run` previews candidate eligibility without API calls or output writes. `--top-selling` processes the top N products by order volume; `--top-offset` selects a later slice. `--rebuild-rules` reprocesses products affected by rules 560–879.

### 4. Full Catalog Generation
```sh
python generate_accessories.py
```
Generates all 8,477 products into `product_accessories.csv` using checkpointing (`product_accessories.v6.checkpoint.csv`). Automatically resumes if interrupted.

---

## Carousel 2 Workflow (`generate_similar_products.py`)

To generate alternative and substitute products across all catalog items:

```sh
python generate_similar_products.py \
  --input enriched_products.csv \
  --output similar_products.csv
```

* Evaluates each product within its taxonomy node.
* Rejects mismatched functional classes rather than filling ten slots with unrelated products: complete boxes versus extensions/supports, complete plates versus inserts, cover openings/gangs, grounding versus insulating/reducing bushings, PVC fitting geometry, conduit versus MC supports, PVC cement versus other chemicals, and complete conduit bodies versus replacement covers.
* Keeps ordinary MC separate from HCF-AC/MC and power/control constructions. THHN alternatives preserve grounding versus neutral versus ungrounded identification; gauge/conductor-count changes remain application-dependent siblings.
* Breakers retain series, standard/tandem form, protection type, and neutral connection type; WR GFCI alternatives retain weather resistance. These restrictions still do not certify panel or installation compatibility.
* Exact trade size or wire construction ranks ahead of brand/popularity; matching device color follows exact fit. Short lists are intentional when qualified alternatives are unavailable.
* Enforces breaker series compatibility (Eaton BR with BR, Homeline with Homeline).
* Keeps tool sub-types aligned (screwdrivers with screwdrivers, strippers with strippers).
* Separates non-metallic splice kits from heat cable, ground-rod clamps from other grounding products, and intersystem bonding bridges from unrelated grounding items.
* Separates heavy-duty wiring devices (dryer/range 30A/50A) from standard 15A duplex receptacles.
* Checks compatibility across product ratings and construction details, including wire type, voltage, phase/pole count, trade size, and conduit schedule.
* Ranks results by 6-month sales order volume.
* Runtime varies by environment; the current 8,477-product catalog takes about 2 minutes.

---

## Rule Diagnostics & Quality Tools

* `audit_and_fix_accessories.py`: Deterministic catalog-wide quality audit and fixer (0 API cost; runtime varies by environment):
  - Preserves all 57+ locked hero packages in `curated_accessories.csv`.
  - Stops before writing if a locked package for an active product references a missing catalog ID; repair the curated row before retrying. Accessory generation performs the same check before calling Gemini.
  - Fills up to 10 slots where compatible candidates exist and reports remaining short rows.
  - Enforces trade sanity guardrails:
    * Cutout style compatibility: Duplex vs Toggle vs Decora vs Single Round vs 2.15" Range/Dryer vs Blank.
    * Multi-gang limits (blocks multi-gang 3G/4G boxes on 1G devices).
    * Eliminates tool mismatches (no heavy strippers, crimpers, meters, or power tools on small commodity wiring devices, fasteners, or small hardware).
    * Box redundancy cap (maximum 2 rough-in boxes per wiring device).
    * Wire nut deduplication (maximum 1 standard twist-on nut per carousel).
    * Model-specific verified-fit guards for FLBC8500 covers, L11-TL3 disconnects, CA7-TD855 coils, and Halco Type B tubes; matching-thread nuts and washers for machine screws.
    * Oscillating blades are not paired with reciprocating saws. Skylark S2-LF controls use designer/Decora wallplate openings.
    * Optical fiber mode barriers (Single-Mode 9/125 vs Multimode 50/125 & 62.5/125).
    * Connector entry size is checked against explicit box knockouts, not box width/depth. 38AST uses a 1/2-inch knockout despite its 3/8-inch trade size.
    * Blank covers reject devices even when the device cutout cannot be identified; recognized plates reject unverified device openings. Square-box cover machine screws must be 8-32.
    * Ground rods require listed rod-clamp diameter matches; threaded LB bodies cannot directly mate to EMT pipe. H-164 shallow strut rejects N-820-series regular-spring nuts.
    * Auditing promotes compatible core essentials ahead of retained filler: MC cable/metal boxes for 38AST, 8-32 cover screws, bonding wire within a grounding bushing's lug range, and cement/matching locknuts for PVC male adapters. This is deterministic and leaves locked rows unchanged.
    * Network switch vs wall switch disambiguation.
    * Surveillance hard drive vs tool storage disambiguation.
  - Automatically triggers `export_web_catalog.py` as its final step to keep the storefront JSON synchronized.
* `export_web_catalog.py`: Exports `products.csv`, `enriched_products.csv`, `BLAZER.RANK.csv`, `product_accessories.csv`, and `similar_products.csv` to the storefront master JSON (0 API cost). The JSON's category and subcategory fields come from `enriched_products.csv`.
* `check_pools.py`: Audits candidate pools for gang, color, trade size, and saturation conflicts.
* `job_kits.csv`: Defines multi-trade assembly scenarios (EV charger, Service upgrade, Hot tub, Rough-in, Network/Data install).
* `tests/`: Unit tests covering manufacturer normalization, accessory rules and audit behavior, similar-product scoring, and web catalog export. Run via:
  ```sh
  python -m unittest discover tests
  ```

Manufacturer references for these checks:
* [RACO square box knockout and 8-32 cover-mount details](https://www.hubbell.com/raco/en/products/4-in-square-box-drawn-2-18-in-deep-cat-232d/p/19515859).
* [Arlington 38AST AC/MC snap-in connector](https://www.onewerner.com/product/Arlington-38AST).
* [Legrand 664-WG four-way toggle](https://www.legrand.us/wiring-devices/light-switches-and-dimmers/residential-switches/trademaster-grounding-toggle-switch-white/p/664wg).
* [Haydon H-164-OS channel](https://haydoncorp.com/catalog/product/h-164-os), [regular-spring nuts](https://haydoncorp.com/catalog/product/n-820-series), and [short-spring nuts](https://haydoncorp.com/catalog/product/N-810-Series?id=248&category=55).

Locked curated packages bypass automatic cleanup. With explicit approval, BR230 SKU 2999's ET310 tester (90-120V only) was removed without a guessed replacement; the remaining nine items retain their relative order. Panel-specific accessories still require fit review; do not treat the package as certified for every 240V installation. Rank-only SKU 40855 remains outside the catalog pending a decision on its administrative role.

---

## Web Catalog JSON Export (`export_web_catalog.py`)

`export_web_catalog.py` compiles the entire product catalog into a high-performance, minified master JSON file named `blazer_web_catalog.json`.

### Single Source of Truth Data Bridge
This file serves as the unified data bridge between the data processing pipeline in this repository and our **Vercel-hosted frontend ordering app**. The frontend consumes `blazer_web_catalog.json` directly to drive instant search, product detail pages, and dual recommendation carousels without database latency or runtime external API dependencies.

### Merged Data Sources & Mapping Logic
The export pipeline stitches together five distinct datasets across all 8,477 catalog products:
* **`products.csv`**: Source of truth for product descriptions. Extracts `BLAZER ID` (SKU) and maps the exact `PRODUCT` description string to the primary `title` property so electricians see the exact catalog descriptions they know.
* **`enriched_products.csv`**: Extracts `brand_manufacturer` (`brand`), `manufacturer_part_number` (`mpn`), `primary_category` (`category`), `sub_category` (`subcategory`), `normalized_specs` (`specs`), and JSON-parsed string arrays for `search_keywords` and `common_misspellings`.
* **`BLAZER.RANK.csv`**: Popularity rankings derived from 6-month sales order volume. Products are ranked so lower numbers equal higher sales rank (#1 is top seller). Unranked products fall back to `999999`.
* **`product_accessories.csv`**: Preserves the strict 1-to-10 complementary installation accessory tiers (`PROD_ID_REL1` through `PROD_ID_REL10`) into an `accessories` array per product.
* **`similar_products.csv`**: Extracts the top alternative and direct substitute products (`SIMILAR_ID_1` through `SIMILAR_ID_10`) into a `similar` array per product.

### Manual Execution
To manually regenerate `blazer_web_catalog.json` at any time:
```sh
python export_web_catalog.py
```

Options:
* `--products`: Custom path to `products.csv` (default: `products.csv`)
* `--enriched`: Custom path to `enriched_products.csv` (default: `enriched_products.csv`)
* `--rank`: Custom path to `BLAZER.RANK.csv` (default: `BLAZER.RANK.csv`)
* `--accessories`: Custom path to `product_accessories.csv` (default: `product_accessories.csv`)
* `--similar`: Custom path to `similar_products.csv` (default: `similar_products.csv`)
* `--output`: Output destination (default: `blazer_web_catalog.json`)
* `--pretty`: Output human-readable indented JSON instead of minified JSON
* `--allow-partial`: Allow missing carousel CSVs, with a warning and empty arrays for missing recommendations.

Execution completes in under 1 second with 0 external API calls.

---

## Catalog Refresh Workflow

When `products.csv` or `BLAZER.RANK.csv` changes, run this sequence:

```sh
# 1. Enrich new SKUs (successful rows are reused from the checkpoint)
python3 enrich_catalog.py

# 2. Map website taxonomy, including deeper paths used by similar-product scoring
python3 map_taxonomy.py

# 3. Regenerate Similar Products (Carousel 2, deterministic and 0 API cost)
# The current 8,477-product catalog takes about 2 minutes; runtime varies by environment.
python3 generate_similar_products.py

# 4. Generate Accessories (Carousel 1, processes only new SKUs using checkpoint)
python3 generate_accessories.py --checkpoint product_accessories.csv

# 5. Deterministic Quality Audit & Fill (0 API cost, compatible slots only; preserves curated items)
# Automatically triggers export_web_catalog.py upon completion
python3 audit_and_fix_accessories.py

# 6. (Optional) Standalone Web Catalog Export
# Step 5 runs this automatically, but it can be rerun manually at any time:
python3 export_web_catalog.py
```

