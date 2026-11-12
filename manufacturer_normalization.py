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
        "MIN",
        "CAR",
    }
)
AUTHORITATIVE_VENDOR_PREFIXES = frozenset({"CUL", "HDN", "POR", "GLA", "VC", "PNS", "DIO", "RAC", "MIN"})
DBL_EXCEPTION = re.compile(r"^\s*DBL\s+23\s+CD\b", re.IGNORECASE)
TEXT_FIELDS = (
    "manufacturer_part_number",
    "clean_title",
    "normalized_specs",
    "short_description",
)
LIST_FIELDS = ("search_keywords", "common_misspellings")

# Verified model corrections also apply when cached enrichment is reused.
VERIFIED_METADATA = (
    (r"^RAC\s+2104\b", {
        "clean_title": "RACO 2104 1-Inch Squeeze Connector for Flexible Metal Conduit",
        "normalized_specs": "1 Inch Trade Size, Squeeze Connector, Flexible Metal Conduit",
        "short_description": "One-inch trade-size squeeze connector for flexible metal conduit; the raw catalog specifies 1 TS.",
        "search_keywords": '["RACO 2104", "1 inch flex connector", "squeeze connector", "flexible metal conduit"]',
    }),
    (r"^CUL\s+52416J\b", {
        "clean_title": "Cully 52416J 8-32 x 1-Inch Round Head Machine Screw, Slotted/Phillips Zinc",
        "normalized_specs": "8-32 Thread, 1 Inch Length, Round Head, Slotted/Phillips, Zinc Plated",
        "short_description": "Eight-32 machine screw with a one-inch length, as specified in the raw catalog description.",
        "search_keywords": '["Cully 52416J", "8-32 machine screw", "1 inch", "round head", "slotted Phillips"]',
    }),
    (r"^ARL\s+38AST\b", {
        "clean_title": "Arlington 38AST Snap-In AC/MC/FMC Connector, 3/8 Inch Trade Size, 1/2 Inch Knockout",
        "normalized_specs": "3/8 Inch Trade Size, 1/2 Inch Knockout, Die Cast Zinc, Snap-In, AC/MC/FMC",
        "short_description": "Snap-in connector for listed AC/MC cable and flexible metal conduit, using a 1/2-inch knockout.",
        "primary_category": "Fittings", "sub_category": "MC, NMB, Liquidtight, Flex Fittings",
        "search_keywords": '["Arlington 38AST", "snap-in connector", "AC/MC connector", "FMC connector", "1/2 inch knockout"]',
    }),
    (r"^PNS\s+664[- ]?WG\b", {
        "clean_title": "Pass & Seymour 664-WG White Four-Way Toggle Switch, 15 Amp 120 Volt",
        "normalized_specs": "15 Amp, 120 Volt, Four-Way, Toggle, White, Grounding Terminals",
        "short_description": "Four-way toggle switch for multi-location lighting circuits, with grounding terminals.",
        "search_keywords": '["664-WG", "four-way toggle switch", "4-way switch", "white switch", "15 amp"]',
    }),
    (r"^HDN\s+H-164-OSx10PG\b", {
        "clean_title": "Haydon H-164-OS 13/16 Inch x 1-5/8 Inch Slotted Strut Channel, 10 Foot Pre-Galvanized",
        "normalized_specs": "13/16 Inch Depth, 1-5/8 Inch Width, 10 Foot Length, 14 Gauge, Slotted, Pre-Galvanized",
        "short_description": "Shallow 14-gauge H-164-OS channel; select channel nuts listed for H-164.",
        "search_keywords": '["Haydon H-164-OS", "shallow strut", "13/16 inch channel", "14 gauge", "slotted strut"]',
    }),
    (r"^RAC\s+902C\b", {
        "clean_title": "RACO 902C 4-Inch Square Duplex Receptacle Cover, 1/2-Inch Raised",
        "normalized_specs": "4 Inch Square, 1/2 Inch Raised Depth, Duplex Receptacle Opening, Galvanized Steel",
        "short_description": "Raised 4-inch square cover with a duplex receptacle opening; 1/2 inch describes the raised depth.",
        "search_keywords": '["RACO 902C", "4 inch square cover", "duplex receptacle cover", "1/2 inch raised"]',
    }),
    (r"^MIN\s+BST\b", {
        "clean_title": "Minerallac BST Box Mount Support",
        "normalized_specs": "Box Mount Support",
        "short_description": "Minerallac BST electrical box mounting support.",
        "search_keywords": '["Minerallac BST", "box mount support", "electrical box support"]',
    }),
    (r"^CAR\s+VC9982\b", {
        "clean_title": "Carlon/Kraloy PVC Quick-Set Solvent Cement, 1 Quart",
        "normalized_specs": "1 Quart, Quick-Set, PVC Solvent Cement",
        "short_description": "Quick-set PVC solvent cement for compatible PVC conduit and fittings.",
        "primary_category": "Tools & Meters", "sub_category": "Paint, Chemicals, Cleaning Supplies",
        "search_keywords": '["PVC solvent cement", "quick-set cement", "Carlon VC9982", "Kraloy CWC20", "quart"]',
    }),
    (r"^BPT\s+38ACDS\b", {
        "primary_category": "Fittings", "sub_category": "MC, NMB, Liquidtight, Flex Fittings",
    }),
    (r"^MIN\s+121\b", {
        "primary_category": "Fittings", "sub_category": "Clips, Clamps, Hangers",
    }),
)


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
        brand_pattern = r"(?:L\.H\.\s*)?Dottie" if old_brand.casefold() == "l.h. dottie" else re.escape(old_brand)

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
                            brand_pattern,
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
                        brand_pattern,
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

    for pattern, fields in VERIFIED_METADATA:
        mask = normalized[PRODUCT_COL].str.contains(pattern, regex=True, case=False, na=False)
        if mask.any():
            for field, value in fields.items():
                normalized.loc[mask, field] = value
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
