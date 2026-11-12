"""Export compiled web catalog JSON for Blazer Electric e-commerce storefront.

Merges products.csv, enriched_products.csv, BLAZER.RANK.csv,
product_accessories.csv, and similar_products.csv into a single, high-performance,
minified master JSON file (blazer_web_catalog.json).
"""

import argparse
import csv
from datetime import datetime, timezone
import json
import logging
import os
import sys
import time
from typing import Any, Dict, List, Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("export_web_catalog")

DEFAULT_PRODUCTS_PATH = "products.csv"
DEFAULT_ENRICHED_PATH = "enriched_products.csv"
DEFAULT_RANK_PATH = "BLAZER.RANK.csv"
DEFAULT_ACCESSORIES_PATH = "product_accessories.csv"
DEFAULT_SIMILAR_PATH = "similar_products.csv"
DEFAULT_OUTPUT_PATH = "blazer_web_catalog.json"
DEFAULT_UNRANKED_FALLBACK = 999999


def _parse_json_list(raw_value: Optional[str], *, sku: str, column: str) -> List[str]:
    """Parse a JSON-encoded list of strings, allowing blank cells."""
    if not raw_value:
        return []
    raw_str = raw_value.strip()
    if not raw_str:
        return []
    try:
        parsed = json.loads(raw_str)
    except json.JSONDecodeError as error:
        raise ValueError(f"Invalid JSON list in {column} for product {sku}") from error
    if not isinstance(parsed, list):
        raise ValueError(f"Expected JSON list in {column} for product {sku}")
    return [str(item) for item in parsed if item is not None]


def load_products(path: str = DEFAULT_PRODUCTS_PATH) -> Dict[str, str]:
    """Load products.csv mapping BLAZER ID to exact PRODUCT description string."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"Products file not found: {path}")

    products: Dict[str, str] = {}
    with open(path, mode="r", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            sku = (row.get("BLAZER ID") or "").strip()
            title = row.get("PRODUCT", "")
            if sku:
                products[sku] = title
    return products


def load_enriched_products(path: str = DEFAULT_ENRICHED_PATH) -> Dict[str, Dict[str, Any]]:
    """Load enriched_products.csv mapping BLAZER ID to enriched attributes."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"Enriched products file not found: {path}")

    enriched: Dict[str, Dict[str, Any]] = {}
    with open(path, mode="r", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            sku = (row.get("BLAZER ID") or "").strip()
            if not sku:
                continue
            enriched[sku] = {
                "brand": (row.get("brand_manufacturer") or "").strip(),
                "mpn": (row.get("manufacturer_part_number") or "").strip(),
                "category": (row.get("primary_category") or "").strip(),
                "subcategory": (row.get("sub_category") or "").strip(),
                "specs": (row.get("normalized_specs") or "").strip(),
                "search_keywords": _parse_json_list(
                    row.get("search_keywords"), sku=sku, column="search_keywords"
                ),
                "common_misspellings": _parse_json_list(
                    row.get("common_misspellings"), sku=sku, column="common_misspellings"
                ),
            }
    return enriched


def load_ranks(
    path: str = DEFAULT_RANK_PATH,
    fallback: int = DEFAULT_UNRANKED_FALLBACK,
) -> Dict[str, int]:
    """Load popularity rankings from BLAZER.RANK.csv.

    Lower rank numbers indicate higher sales volume (e.g., Rank 1 is #1 best seller).
    If an explicit 'RANK' column exists, it is used; otherwise rows are sorted
    descending by 6-month order volume and assigned 1-based ranks (1, 2, ...).
    """
    if not os.path.exists(path):
        logger.warning("Rank file %s not found; all items will use fallback rank %d.", path, fallback)
        return {}

    with open(path, mode="r", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)

    if not rows:
        return {}

    # Check if an explicit RANK column is present
    first_row = rows[0]
    rank_col = next((col for col in first_row.keys() if col and col.strip().upper() == "RANK"), None)

    ranks: Dict[str, int] = {}
    if rank_col:
        for row in rows:
            pid = (row.get("PROD_ID") or row.get("BLAZER ID") or "").strip()
            val = row.get(rank_col, "").strip()
            if pid and val:
                try:
                    ranks[pid] = int(val)
                except ValueError:
                    pass
        return ranks

    # Otherwise derive rank from 'TIMES ORDERED PAST 6 MONTHS' (descending order)
    def parse_orders(r: Dict[str, str]) -> int:
        val = r.get("TIMES ORDERED PAST 6 MONTHS", "")
        try:
            return int(float(val.strip()))
        except (ValueError, AttributeError):
            return 0

    sorted_rows = sorted(rows, key=parse_orders, reverse=True)
    for index, row in enumerate(sorted_rows, start=1):
        pid = (row.get("PROD_ID") or row.get("BLAZER ID") or "").strip()
        if pid and pid not in ranks:
            ranks[pid] = index

    return ranks


def load_accessories(
    path: str = DEFAULT_ACCESSORIES_PATH, allow_partial: bool = False
) -> Dict[str, List[str]]:
    """Load 1-to-10 accessory tiers from product_accessories.csv."""
    if not os.path.exists(path):
        if not allow_partial:
            raise FileNotFoundError(
                f"Accessories file {path} not found. Restore or generate it, or use "
                "--allow-partial for an intentional partial export."
            )
        logger.warning("Accessories file %s not found; accessories will be empty.", path)
        return {}

    accessories: Dict[str, List[str]] = {}
    rel_cols = [f"PROD_ID_REL{i}" for i in range(1, 11)]

    with open(path, mode="r", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            pid = (row.get("PROD_ID") or row.get("BLAZER ID") or "").strip()
            if not pid:
                continue
            acc_list = [
                row[col].strip()
                for col in rel_cols
                if col in row and row[col] and row[col].strip()
            ]
            accessories[pid] = acc_list

    return accessories


def load_similar(
    path: str = DEFAULT_SIMILAR_PATH, allow_partial: bool = False
) -> Dict[str, List[str]]:
    """Load top alternative/similar products from similar_products.csv."""
    if not os.path.exists(path):
        if not allow_partial:
            raise FileNotFoundError(
                f"Similar products file {path} not found. Restore or generate it, or use "
                "--allow-partial for an intentional partial export."
            )
        logger.warning("Similar products file %s not found; similar will be empty.", path)
        return {}

    similar: Dict[str, List[str]] = {}
    sim_cols = [f"SIMILAR_ID_{i}" for i in range(1, 11)]

    with open(path, mode="r", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            pid = (row.get("PROD_ID") or row.get("BLAZER ID") or "").strip()
            if not pid:
                continue
            sim_list = [
                row[col].strip()
                for col in sim_cols
                if col in row and row[col] and row[col].strip()
            ]
            similar[pid] = sim_list

    return similar


def build_catalog_dict(
    products_path: str = DEFAULT_PRODUCTS_PATH,
    enriched_path: str = DEFAULT_ENRICHED_PATH,
    rank_path: str = DEFAULT_RANK_PATH,
    accessories_path: str = DEFAULT_ACCESSORIES_PATH,
    similar_path: str = DEFAULT_SIMILAR_PATH,
    unranked_fallback: int = DEFAULT_UNRANKED_FALLBACK,
    allow_partial: bool = False,
) -> Dict[str, Any]:
    """Merge all catalog data sources into the master JSON schema dictionary."""
    logger.info("Loading catalog sources...")
    products = load_products(products_path)
    enriched = load_enriched_products(enriched_path)
    ranks = load_ranks(rank_path, fallback=unranked_fallback)
    accessories = load_accessories(accessories_path, allow_partial=allow_partial)
    similar = load_similar(similar_path, allow_partial=allow_partial)

    compiled_products: Dict[str, Dict[str, Any]] = {}
    unranked_count = 0

    for sku, title in products.items():
        enr = enriched.get(sku, {})
        rank = ranks.get(sku, unranked_fallback)
        if rank == unranked_fallback:
            unranked_count += 1

        compiled_products[sku] = {
            "sku": sku,
            "title": title,
            "brand": enr.get("brand", ""),
            "mpn": enr.get("mpn", ""),
            "category": enr.get("category", ""),
            "subcategory": enr.get("subcategory", ""),
            "specs": enr.get("specs", ""),
            "rank": rank,
            "search_keywords": enr.get("search_keywords", []),
            "common_misspellings": enr.get("common_misspellings", []),
            "accessories": accessories.get(sku, []),
            "similar": similar.get(sku, []),
        }

    now_iso = datetime.now(timezone.utc).isoformat()
    catalog_data = {
        "last_updated": now_iso,
        "total_products": len(compiled_products),
        "products": compiled_products,
    }

    logger.info(
        "Compiled %d products (%d ranked, %d unranked with fallback %d).",
        len(compiled_products),
        len(compiled_products) - unranked_count,
        unranked_count,
        unranked_fallback,
    )
    return catalog_data


def save_atomic_json(data: Dict[str, Any], output_path: str, minify: bool = True) -> None:
    """Atomically write JSON data to file."""
    tmp_path = f"{output_path}.tmp"
    with open(tmp_path, mode="w", encoding="utf-8") as handle:
        if minify:
            json.dump(data, handle, separators=(",", ":"), ensure_ascii=False)
        else:
            json.dump(data, handle, indent=2, ensure_ascii=False)
    os.replace(tmp_path, output_path)


def export_catalog(
    products_path: str = DEFAULT_PRODUCTS_PATH,
    enriched_path: str = DEFAULT_ENRICHED_PATH,
    rank_path: str = DEFAULT_RANK_PATH,
    accessories_path: str = DEFAULT_ACCESSORIES_PATH,
    similar_path: str = DEFAULT_SIMILAR_PATH,
    output_path: str = DEFAULT_OUTPUT_PATH,
    minify: bool = True,
    unranked_fallback: int = DEFAULT_UNRANKED_FALLBACK,
    allow_partial: bool = False,
) -> str:
    """Run full catalog compilation and atomic export to JSON.

    Returns the path of the generated JSON file.
    """
    start_time = time.time()
    logger.info("Starting web catalog JSON export -> %s", output_path)

    data = build_catalog_dict(
        products_path=products_path,
        enriched_path=enriched_path,
        rank_path=rank_path,
        accessories_path=accessories_path,
        similar_path=similar_path,
        unranked_fallback=unranked_fallback,
        allow_partial=allow_partial,
    )

    save_atomic_json(data, output_path, minify=minify)

    elapsed = time.time() - start_time
    file_size_mb = os.path.getsize(output_path) / (1024 * 1024)
    logger.info(
        "Successfully exported %s (%.2f MB, %d products) in %.2f seconds.",
        output_path,
        file_size_mb,
        data["total_products"],
        elapsed,
    )
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compile and export blazer_web_catalog.json for the e-commerce ordering app."
    )
    parser.add_argument("--products", default=DEFAULT_PRODUCTS_PATH, help="Path to products.csv")
    parser.add_argument("--enriched", default=DEFAULT_ENRICHED_PATH, help="Path to enriched_products.csv")
    parser.add_argument("--rank", default=DEFAULT_RANK_PATH, help="Path to BLAZER.RANK.csv")
    parser.add_argument("--accessories", default=DEFAULT_ACCESSORIES_PATH, help="Path to product_accessories.csv")
    parser.add_argument("--similar", default=DEFAULT_SIMILAR_PATH, help="Path to similar_products.csv")
    parser.add_argument("--output", default=DEFAULT_OUTPUT_PATH, help="Output path for blazer_web_catalog.json")
    parser.add_argument("--pretty", action="store_true", help="Format JSON with indentation instead of minifying")
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="Allow missing carousel CSVs and export empty arrays for those recommendations.",
    )
    parser.add_argument(
        "--unranked-fallback",
        type=int,
        default=DEFAULT_UNRANKED_FALLBACK,
        help="Fallback rank for unranked products (default: 999999)",
    )

    args = parser.parse_args()
    export_catalog(
        products_path=args.products,
        enriched_path=args.enriched,
        rank_path=args.rank,
        accessories_path=args.accessories,
        similar_path=args.similar,
        output_path=args.output,
        minify=not args.pretty,
        unranked_fallback=args.unranked_fallback,
        allow_partial=args.allow_partial,
    )


if __name__ == "__main__":
    main()
