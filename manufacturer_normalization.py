"""Apply deterministic manufacturer-prefix corrections to enriched catalog rows."""

import argparse
import csv
import json
import os
import stat
import re
import tempfile

import pandas as pd

ID_COL = "BLAZER ID"
PRODUCT_COL = "PRODUCT"
PREFIX_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "MFR Prefix.csv")
PREFIXES_TO_NORMALIZE = frozenset(
    {
        "DBL",
        "COP",
        "ALW",
        "DOT",
        "RAT",
        "RAC",
        "NIP",
        "ELB",
        "CON",
        "PVC",
        "LQT",
        "MC",
        "CUL",
        "HDN",
        "POR",
        "GLA",
        "VC",
        "PNS",
        "DIO",
    }
)
AUTHORITATIVE_VENDOR_PREFIXES = frozenset({"CUL", "HDN", "POR", "GLA", "VC", "PNS", "DIO", "RAC"})
DBL_EXCEPTION = re.compile(r"^\s*DBL\s+23\s+CD\b", re.IGNORECASE)
TEXT_FIELDS = (
    "manufacturer_part_number",
    "clean_title",
    "normalized_specs",
    "short_description",
)
LIST_FIELDS = ("search_keywords", "common_misspellings")


def _load_prefix_manufacturers(path: str) -> dict[str, str]:
    with open(path, newline="", encoding="utf-8-sig") as handle:
        return {
            row["PREFIX"].strip().upper(): row["Manufacturer"].strip()
            for row in csv.DictReader(handle)
            if row.get("PREFIX", "").strip() and row.get("Manufacturer", "").strip()
        }


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" ,;-")


def _replace_brand(value: str, brand: str, replacement: str | None) -> str:
    if not value or not brand:
        return value
    updated = re.sub(re.escape(brand), replacement or "", value, flags=re.IGNORECASE)
    return _clean_text(updated)


def _brand_is_explicit(product: str, brand: str) -> bool:
    tokens = _brand_tokens(brand)
    return any(
        re.search(rf"\b{re.escape(token)}\b", product, re.IGNORECASE)
        for token in tokens
    )


def _brand_tokens(brand: str) -> list[str]:
    ignored = {
        "cable",
        "company",
        "conduit",
        "elbow",
        "generic",
        "inc",
        "industries",
        "liquidtight",
        "llc",
        "nipple",
        "pvc",
        "products",
        "tube",
        "unknown",
        "various",
        "wire",
    }
    return [
        token
        for token in re.findall(r"[A-Za-z0-9]+", brand)
        if token.casefold() not in ignored
    ]


def _strip_leading_brand(value: str, brand: str) -> str:
    tokens = re.findall(r"[A-Za-z0-9]+", brand)
    for length in range(len(tokens), 0, -1):
        pattern = r"[\s/-]+".join(re.escape(token) for token in tokens[:length])
        updated = re.sub(
            rf"^\s*{pattern}\b[\s:,-]*",
            "",
            value,
            flags=re.IGNORECASE,
        )
        if updated != value:
            return _clean_text(updated)
    return value


def normalize_enriched_products(
    products: pd.DataFrame, prefix_path: str = PREFIX_PATH
) -> pd.DataFrame:
    """Correct known raw manufacturer codes while preserving other enrichment."""
    prefix_manufacturers = _load_prefix_manufacturers(prefix_path)
    corrections = {
        code: prefix_manufacturers[code]
        for code in PREFIXES_TO_NORMALIZE
        if code in prefix_manufacturers
    }
    if corrections.keys() != PREFIXES_TO_NORMALIZE:
        missing = sorted(PREFIXES_TO_NORMALIZE - corrections.keys())
        raise ValueError(f"{prefix_path} is missing manufacturer prefixes: {missing}")

    normalized = products.copy()
    for index, row in normalized.iterrows():
        raw_product = str(row.get(PRODUCT_COL, ""))
        match = re.match(r"^\s*([A-Z]{2,3})(?=\s|$)", raw_product, re.IGNORECASE)
        if not match:
            continue

        code = match.group(1).upper()
        if code not in corrections or (code == "DBL" and DBL_EXCEPTION.search(raw_product)):
            continue

        manufacturer = corrections[code]
        old_brand = str(row.get("brand_manufacturer", "")).strip()

        if manufacturer.casefold().endswith("generic"):
            generic_labels = {
                manufacturer.casefold(),
                "generic",
                "generic/unbranded",
                "generic/unknown",
                "generic/various",
                "generic/standard",
                "unknown",
            }
            explicit_brand = (
                old_brand
                and old_brand.casefold() not in generic_labels
                and _brand_is_explicit(raw_product, old_brand)
            )
            normalized.at[index, "brand_manufacturer"] = (
                old_brand if explicit_brand else manufacturer
            )
            if (
                old_brand
                and old_brand.casefold() != manufacturer.casefold()
                and not explicit_brand
            ):
                for field in TEXT_FIELDS:
                    value = str(row.get(field, ""))
                    if field == "clean_title":
                        value = _strip_leading_brand(value, old_brand)
                        if old_brand.casefold() in generic_labels:
                            value = _replace_brand(value, old_brand, None)
                    elif field == "short_description":
                        value = _strip_leading_brand(value, old_brand)
                        if old_brand.casefold() in generic_labels:
                            value = _replace_brand(value, old_brand, None)
                    else:
                        value = _replace_brand(value, old_brand, None)
                    normalized.at[index, field] = value

                for field in LIST_FIELDS:
                    value = str(row.get(field, ""))
                    if not value:
                        continue
                    items = json.loads(value)
                    cleaned = [
                        item
                        for item in (
                            (
                                _replace_brand(str(item), old_brand, None)
                                if old_brand.casefold() in generic_labels
                                else _strip_leading_brand(str(item), old_brand)
                            )
                            for item in items
                        )
                        if item
                    ]
                    normalized.at[index, field] = json.dumps(cleaned, ensure_ascii=False)
            continue

        normalized.at[index, "brand_manufacturer"] = manufacturer
        if old_brand and old_brand.casefold() != manufacturer.casefold():
            for field in (*TEXT_FIELDS, *LIST_FIELDS):
                value = str(row.get(field, ""))
                if not value:
                    continue
                if field in LIST_FIELDS:
                    items = json.loads(value)
                    changed = [
                        re.sub(
                            re.escape(old_brand),
                            manufacturer,
                            str(item),
                            flags=re.IGNORECASE,
                        )
                        if code in AUTHORITATIVE_VENDOR_PREFIXES
                        or old_brand.casefold() not in raw_product.casefold()
                        else str(item)
                        for item in items
                    ]
                    normalized.at[index, field] = json.dumps(changed, ensure_ascii=False)
                elif code in AUTHORITATIVE_VENDOR_PREFIXES or old_brand.casefold() not in raw_product.casefold():
                    normalized.at[index, field] = re.sub(
                        re.escape(old_brand),
                        manufacturer,
                        value,
                        flags=re.IGNORECASE,
                    )

        title = str(normalized.at[index, "clean_title"])
        if title and not title.casefold().startswith(f"{manufacturer.casefold()} "):
            if old_brand and (
                code in AUTHORITATIVE_VENDOR_PREFIXES
                or old_brand.casefold() not in raw_product.casefold()
            ):
                title = re.sub(
                    rf"^\s*{re.escape(old_brand)}\b[\s:,-]*",
                    "",
                    title,
                    flags=re.IGNORECASE,
                )
            normalized.at[index, "clean_title"] = f"{manufacturer} {_clean_text(title)}".strip()

    return normalized


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Apply known manufacturer-prefix corrections without calling Gemini."
    )
    parser.add_argument("--input", default="enriched_products.csv")
    parser.add_argument("--output", default="enriched_products.corrected.csv")
    parser.add_argument("--prefixes", default=PREFIX_PATH)
    args = parser.parse_args()

    products = pd.read_csv(args.input, dtype=str, keep_default_na=False)
    corrected = normalize_enriched_products(products, args.prefixes)
    directory = os.path.dirname(os.path.abspath(args.output))
    mode_source = args.output if os.path.exists(args.output) else args.input
    output_mode = stat.S_IMODE(os.stat(mode_source).st_mode)
    fd, temp_path = tempfile.mkstemp(prefix=".enriched.", suffix=".tmp", dir=directory)
    os.close(fd)
    try:
        os.chmod(temp_path, output_mode)
        corrected.to_csv(temp_path, index=False, encoding="utf-8")
        os.replace(temp_path, args.output)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)

    print(f"Wrote {args.output} ({len(corrected)} rows)")


if __name__ == "__main__":
    main()
