"""Generate complementary electrical accessories for an enriched product catalog.

Usage:
    python generate_accessories.py [--input enriched_products.csv]
        [--output product_accessories.csv]
        [--checkpoint product_accessories.v3.checkpoint.csv]

Requires GEMINI_API_KEY in the environment or a .env file. Successful rows are
checkpointed every 100 products so an interrupted run can be resumed.
"""

import argparse
import asyncio
import csv
from collections import Counter, defaultdict
import logging
import os
import re
import zlib
import sys
from typing import Iterable, NamedTuple

import pandas as pd
from dotenv import load_dotenv
from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel, Field
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_random_exponential,
)
from tqdm.asyncio import tqdm

MODEL = "gemini-3.1-flash-lite"
CONCURRENCY = 12
CHECKPOINT_EVERY = 100
MAX_ATTEMPTS = 6
ID_COL = "BLAZER ID"
OUTPUT_ID_COL = "PROD_ID"
RELATION_COLUMNS = [f"PROD_ID_REL{i}" for i in range(1, 11)]
REQUIRED_COLUMNS = {
    ID_COL,
    "PRODUCT",
    "clean_title",
    "normalized_specs",
    "short_description",
    "primary_category",
    "sub_category",
}
MAX_CANDIDATES = 150
CHECKPOINT_VERSION = "v3"
SIZE_MATCHED_GROUPS = frozenset({"conduit", "conduit_fittings", "weatherheads"})
# Candidates without a parseable size are dropped in these groups.
STRICT_SIZE_GROUPS = frozenset({"conduit_fittings"})

TAXONOMY_PATH = "product_taxonomy.csv"
TAX_COL = "tax_path"
COLORS = (
    "white", "ivory", "almond", "black", "brown", "gray", "grey", "red",
    "light almond", "stainless", "blue", "green", "orange", "yellow",
)

RULES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "accessory_rules.csv")


class TaxonomyRule(NamedTuple):
    target_path: str
    accessory_path: str
    same_brand: bool
    same_color: bool
    size_mode: str
    match: str
    target_keywords: re.Pattern | None
    accessory_keywords: re.Pattern | None


MATCH_MODES = frozenset({"none", "series", "gauge", "platform", "amps", "thread", "box"})


def _keywords(value: str) -> re.Pattern | None:
    value = (value or "").strip()
    return re.compile(value, re.I) if value else None


def load_taxonomy_rules(path: str = RULES_PATH) -> dict[str, TaxonomyRule]:
    """One rule per CSV row. Optional columns: match, target_keywords, accessory_keywords."""
    if not os.path.exists(path):
        return {}
    rules = {}
    with open(path, newline="", encoding="utf-8") as handle:
        for index, row in enumerate(csv.DictReader(handle), start=1):
            size_mode = row["size_match"].strip().lower() or "none"
            if size_mode not in {"none", "loose", "strict"}:
                raise ValueError(f"{path} row {index}: bad size_match {size_mode!r}")
            match = (row.get("match") or "none").strip().lower() or "none"
            if match not in MATCH_MODES:
                raise ValueError(f"{path} row {index}: bad match {match!r}")
            rules[f"rule{index}"] = TaxonomyRule(
                row["target_path"].strip(),
                row["accessory_path"].strip(),
                row["same_brand"].strip().lower() == "yes",
                row["same_color"].strip().lower() == "yes",
                size_mode,
                match,
                _keywords(row.get("target_keywords", "")),
                _keywords(row.get("accessory_keywords", "")),
            )
    return rules


TAXONOMY_RULES = load_taxonomy_rules()

KITS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "job_kits.csv")
KIT_DEFAULT_PER_SLOT = 3
KIT_MAX_TOTAL = 15
KIT_POOL_PER_SLOT = 8
KIT_ONLY_BELOW = 12


def _stable_int(value: str) -> int:
    digits = re.sub(r"\D", "", str(value))
    return int(digits) if digits else sum(map(ord, str(value)))


class KitSlot(NamedTuple):
    job: str
    slot: str
    path: str
    pattern: re.Pattern[str]
    per_slot: int


def load_job_kits(path: str = KITS_PATH) -> dict[str, list[KitSlot]]:
    """Job scenarios (EV charger, service upgrade, ...) as lists of product slots.

    Columns: job, slot, path (taxonomy prefix, optional), keywords (regex on the
    product text), max (candidates taken per slot, optional).
    """
    kits: dict[str, list[KitSlot]] = defaultdict(list)
    if not os.path.exists(path):
        return kits
    with open(path, newline="", encoding="utf-8") as handle:
        for index, row in enumerate(csv.DictReader(handle), start=1):
            try:
                pattern = re.compile(row["keywords"], re.IGNORECASE)
                per_slot = int(row.get("max") or KIT_DEFAULT_PER_SLOT)
            except (re.error, ValueError) as exc:
                raise ValueError(f"{path} row {index}: {exc}") from exc
            kits[row["job"].strip()].append(
                KitSlot(row["job"].strip(), row["slot"].strip(), (row.get("path") or "").strip(), pattern, per_slot)
            )
    return kits


def _slot_matches(slot: KitSlot, row: dict[str, str]) -> bool:
    if slot.path and not row.get(TAX_COL, "").startswith(slot.path):
        return False
    return bool(slot.pattern.search(_text(row)))


JOB_KITS = load_job_kits()


def kit_jobs(target: dict[str, str]) -> list[str]:
    return [
        job
        for job, slots in JOB_KITS.items()
        if any(_slot_matches(slot, target) for slot in slots)
    ]
_SIZE_PATTERN = re.compile(
    r"(\d+-\d+/\d+|\d+/\d+|\d+\.\d+|\d+)\s*(?:inch(?:es)?\b|in\b|\")", re.I
)

logger = logging.getLogger("generate_accessories")


class AccessorySelection(BaseModel):
    product_ids: list[str] = Field(
        default_factory=list,
        description="Up to 10 candidate BLAZER IDs that are complementary accessories.",
    )


GROUP_RULES: dict[
    str, tuple[tuple[str, ...], frozenset[str], frozenset[str]]
] = {
    "conduit_fittings": (
        (),
        frozenset(
            {
                "Cable Connectors",
                "Compression Connectors",
                "Conduit Hubs",
                "Conduit Nipples",
                "EMT Fittings",
                "Flexible Conduit Connectors",
                "Liquidtight Fittings",
                "PVC Conduit Fittings",
                "Rigid Conduit Fittings",
            }
        ),
        frozenset(
            {
                "Bushings & Locknuts",
                "Conduit Hangers & Clamps",
                "Conduit Hangers & Straps",
                "Conduit Straps & Clamps",
            }
        ),
    ),
    "load_centers_and_panels": (
        (),
        frozenset(
            {
                "Load Centers",
                "Main Breaker Load Centers",
                "Main Lug Load Centers",
                "Panelboard Enclosures",
                "Panelboard Interiors",
                "Panelboards",
                "Panelboards & Interiors",
                "Pump Panels",
                "Spa Panels",
                "Spa Panels & Disconnects",
                "Temporary Power Panels",
            }
        ),
        frozenset(
            {
                "Circuit Breaker Accessories",
                "Load Center Accessories",
                "Panel Accessories",
                "Panelboard Accessories",
                "Panelboard Trims & Accessories",
                "Panelboard Trims & Covers",
                "Grounding & Bonding",
                "Grounding & Bonding Accessories",
                "Grounding Accessories",
                "Grounding Kits",
                "Surge Protection",
            }
        ),
    ),
    "weatherheads": (
        ("weatherhead", "service entrance head"),
        frozenset(),
        frozenset(
            {
                "Conduit Hubs",
                "Conduit Seals & Hubs",
                "Duct Seal & Sealants",
                "Duct Seal & Putty",
                "Grounding Clamps",
                "Service Entrance Clamps",
                "Service Entrance Connectors",
                "Service Entrance Fittings",
            }
        ),
    ),
    "conduit": (
        (),
        frozenset(
            {
                "EMT Conduit",
                "ENT Conduit",
                "Flexible Conduit",
                "Flexible Metal Conduit",
                "Innerduct & Communication Conduit",
                "Intermediate Metal Conduit (IMC)",
                "Liquidtight Conduit",
                "Non-Metallic Conduit",
                "PVC Conduit",
                "Rigid & Intermediate Metal Conduit",
                "Rigid Conduit",
            }
        ),
        frozenset(
            {
                "Conduit Adapters",
                "Conduit Bodies",
                "Conduit Body Accessories",
                "Conduit Body Covers",
                "Conduit Body Covers & Gaskets",
                "Conduit Body Gaskets",
                "Conduit Clamps & Straps",
                "Conduit Connectors",
                "Conduit Couplings",
                "Conduit Fittings",
                "Conduit Fittings & Accessories",
                "Conduit Gaskets & Seals",
                "Conduit Hangers & Supports",
                "Conduit Hubs",
                "Conduit Nipples",
                "Conduit Nipples & Bushings",
                "Conduit Nipples & Extensions",
                "Conduit Nipples & Offsets",
                "Conduit Seals & Hubs",
                "Conduit Straps & Clamps",
                "Conduit Supports & Hangers",
                "Conduit Washers & Reducers",
                "Duct Seal & Putty",
                "Duct Seal & Sealants",
                "EMT Fittings",
                "Flexible Conduit Connectors",
                "Flexible Conduit Fittings",
                "Liquidtight Fittings",
                "PVC Conduit Fittings",
                "Rigid Conduit Fittings",
            }
        ),
    ),
    "boxes_and_enclosures": (
        (),
        frozenset(
            {
                "Adjustable Electrical Boxes",
                "Ceiling Fan & Fixture Boxes",
                "Electrical Boxes",
                "Fan & Fixture Boxes",
                "Junction & Pull Boxes",
                "Junction Boxes",
                "Junction Boxes & Enclosures",
                "Pull Boxes",
                "Raceway Junction Boxes",
                "Recessed Electrical Boxes",
                "Steel Electrical Boxes",
            }
        ),
        frozenset(
            {
                "Box Covers",
                "Box Covers & Mud Rings",
                "Box Covers & Plaster Rings",
                "Box Covers & Plates",
                "Box Mounting Brackets",
                "Box Mounting Brackets & Rings",
                "Box Supports & Mounting Brackets",
                "Box Supports & Mounting Hardware",
                "Concrete Box Covers",
                "Device Box Covers",
                "Electrical Box Accessories",
                "Enclosure Accessories",
                "Enclosure Mounting Hardware",
                "FS/FD Box Covers",
                "Junction Box Accessories",
                "Weatherproof Box Covers",
            }
        ),
    ),
}

SYSTEM_PROMPT = """You are an electrical wholesale cross-sell specialist.
Choose products from the supplied candidate list that an electrician would
purchase along with the target product to complete its installation.

Return only candidate IDs, in useful purchase order, with no explanation.
Return no more than 10 IDs and never return the target ID.
Only select complementary, mating, required, mounting, grounding, sealing,
termination, or installation consumables. Do not select direct substitutes,
another color/brand/size version of the target, unrelated products, or items
that merely belong to the same broad category. Use only IDs in the candidate
list. It is valid to return an empty list when no candidate is clearly
compatible. If the target lists install_jobs, favor candidates an electrician
would need for the same job. Consider the target's size, material, voltage, wiring method, and
other specifications before selecting an accessory.
"""


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, genai_errors.APIError):
        code = getattr(exc, "code", None)
        return code in (408, 429) or (isinstance(code, int) and code >= 500)
    return isinstance(
        exc, (asyncio.TimeoutError, ConnectionError, TimeoutError, ValueError)
    )


def _parse_size(token: str) -> float:
    whole, _, fraction = token.rpartition("-")
    if "/" in fraction:
        numerator, denominator = fraction.split("/")
        return float(whole or 0) + int(numerator) / int(denominator)
    return float(token)


_CODE_SIZE = re.compile(
    r"(?<![\w/.-])(\d+-\d+/\d+|\d+/\d+|\d+)(?:\s*(?:in\b|\"))?[\s-]*"
    r"(?:emt|rgd|rmc|grc|imc|pvc|ts\b|flex|lt\b|lqt|ent\b)|\bnip\s+(\d+-\d+/\d+|\d+/\d+|\d+)x",
    re.I,
)


def trade_sizes(row: dict[str, str]) -> list[float]:
    """Inch sizes in the order they appear; the first is the trade size."""
    coded = []
    for a, b in _CODE_SIZE.findall(str(row.get("PRODUCT", ""))):
        try:
            coded.append(_parse_size(a or b))
        except (ValueError, ZeroDivisionError):
            continue
    if coded:
        return coded
    text = f"{row.get('normalized_specs', '')} {row.get('clean_title', '')}"
    return [_parse_size(token) for token in _SIZE_PATTERN.findall(text)]


def _sizes_compatible(
    target: dict[str, str], candidate: dict[str, str], strict: bool = False
) -> bool:
    target_sizes = trade_sizes(target)
    candidate_sizes = trade_sizes(candidate)
    if not target_sizes or not candidate_sizes:
        return not strict
    return any(abs(target_sizes[0] - size) < 1e-6 for size in candidate_sizes)


_TOOL_NODES = (
    "Tools & Meters > Cordless Tools",
    "Tools & Meters > Corded Tools",
    "Tools & Meters > Hand Tools",
)
# The mapper sometimes files blades, bits and punches under tool nodes.
TAXONOMY_OVERRIDES = (
    (
        r"\b(?:jack|keystone|patch panel)\b",
        ("Lighting Fixtures, Control > Bulbs - LED",),
        "Data & Telecom > Workstation",
    ),
    (
        r"\b(?:urd|uf-b|uf-\d|underground feeder)\b|\buf-",
        ("Connectors & Crimps > Wire Connectors",),
        "Wire, Cable & Cord > Direct Burial",
    ),
    (
        r"photocontrol|photo control|photocell",
        ("Caddy > Ceiling/Acoustical",),
        "Lighting Fixtures, Control > Photocells",
    ),
    (
        r"\bpan\b|pancake",
        ("Boxes & Enclosures > Boxes - Weatherproof",),
        "Boxes & Enclosures > Boxes - Metallic",
    ),
    (
        r"\bhid\b|\bt[58]\b|\bpar\d+|\bmr16|\ba19\b",
        ("Control & Automation > Pilot Devices",),
        "Lamps, Bulbs & Ballast > Bulbs - LED",
    ),
    (r"outlet plate", ("Boxes & Enclosures > Boxes - Non-Metallic",), "Wiring Devices > Wallplates"),
    (r"rj45|rj-45|cat ?[56]e?\b", ("Connectors & Crimps > Wire Connectors",), "Data & Telecom > Low Voltage Cable"),
    (r"lkg conn|locking conn|twist.?lock", ("Connectors & Crimps > Wire Connectors",), "Wiring Devices > Pin & Sleeve"),
    (r"underground enclosure|tier \d", ("Connectors & Crimps > Wire Connectors",), "Boxes & Enclosures > Underground Pull Boxes"),
    (
        r"metal clad|\bmc cable\b|armored",
        ("Fittings > Clips, Clamps, Hangers", "Wire, Cable & Cord > Building Wire"),
        "Wire, Cable & Cord > Armored Cable",
    ),
    (
        r"conduit strap|emt strap|one-hole strap|two-hole strap|pipe strap",
        ("Conduit, Strut & Raceways > Strut",),
        "Fittings > Straps",
    ),
    (
        r"\b(?:saw blades?|blades?|bits?|hole saws?|holesaws?|hole cutters?|punch(?:es)?|"
        r"insert bits?|drill and tap|nut ?setters?|nut ?drive?rs?|nutdrvr|drive guide|"
        r"(?:hex|quick).{0,12}adapter|socket adptr)\b",
        _TOOL_NODES,
        "Tools & Meters > Bits, Blades, Punches, Holesaws",
    ),
    (
        r"diagonal cutter|side cutter|cutting plier|high leverage",
        ("Tools & Meters > Cordless Tools", "Tools & Meters > Corded Tools"),
        "Tools & Meters > Hand Tools",
    ),
    (
        r"^(?!.*(?:emt|conduit|raintight|rain tight|liquid|flex)).*(?:butt splice|h-tap|compression (?:connector|lug|splice)|crimp splice)",
        ("Fittings > EMT Fittings", "Wire, Cable & Cord > Service Wire"),
        "Connectors & Crimps > Lugs & Split Bolts",
    ),
    (
        r"sheathed cable|\bnm-b\b|\bmc cable\b",
        ("Data & Telecom > Non-Metallic Raceway",),
        "Wire, Cable & Cord > Building Wire",
    ),
    (
        r"ethernet|patch panel|fiber",
        ("Wiring Devices > Switches",),
        "Data & Telecom > Distribution Devices",
    ),
    (
        r"telephone jack|data jack|keystone",
        ("Fasteners & Hardware > Fixture Hardware",),
        "Data & Telecom > Workstation",
    ),
    (
        r"\bbattery pack\b",
        ("Lamps, Bulbs & Ballast > Ballast - Fluorescent",),
        "Power Distribution > Power Quality/Protection",
    ),
    (
        r"\blamp\b",
        ("Control & Automation > Pilot Devices",),
        "Lamps, Bulbs & Ballast > Bulbs - HID",
    ),
    (
        r"wallmount enclosure|\benclosure\b",
        ("Boxes & Enclosures > Floor Boxes",),
        "Boxes & Enclosures > Enclosures - Commercial Type",
    ),
    (
        r"liquid ?tight|flex",
        ("Fittings > ",),
        "Fittings > MC, NMB, Liquidtight, Flex Fittings",
    ),
    (
        r"seal(?:ing)? fitting|explosion",
        ("Fittings > ",),
        "Fittings > Explosionproof Fittings",
    ),
    (
        r"\belectrical box\b",
        ("Wiring Devices > Wallplates",),
        "Boxes & Enclosures > Boxes - Non-Metallic",
    ),
    (
        r"locking|twist-?lock|turnlok|\bl\d{1,2}-\d{2}",
        ("Wiring Devices > Straight Blade 10-20 Amp", "Wiring Devices > Receptacles"),
        "Wiring Devices > Twistlock",
    ),
)
RANK_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "BLAZER.RANK.csv")
ORDERS_COL = "orders_6m"
MIN_CANDIDATE_ORDERS = 2
MAX_PER_RULE = 12
MAX_PER_LOW_VALUE = 3
# Consumables and tools that fit almost anything; a few per pool is enough.
LOW_VALUE_NODES = (
    "Connectors & Crimps > Wire Connectors",
    "Tools & Meters > Hand Tools",
    "Tools & Meters > Paint, Chemicals",
    "Tools & Meters > Storage",
    "Tools & Meters > Safety Equipment",
    "Tools & Meters > Meters - Test",
    "Fasteners & Hardware > Tapes",
)
# Small categories whose only sensible complements are other low-value items.
SMALL_TOOL_NODES = (
    "Tools & Meters > Apparel",
    "Tools & Meters > Storage",
    "Tools & Meters > Flashlights",
    "Tools & Meters > Paint, Chemicals",
    "Tools & Meters > Marking",
    "Tools & Meters > Batteries",
)
TOP_ANCHORS = 1  # best sellers always offered; the rest rotate
SPREAD_FACTOR = 10
MIN_SUGGESTIONS = 5
FALLBACK_FILL = 8


def attach_orders(source: pd.DataFrame, path: str = RANK_PATH) -> pd.DataFrame:
    if not os.path.exists(path):
        logger.warning("%s not found; candidates are not filtered by popularity.", path)
        return source.assign(**{ORDERS_COL: MIN_CANDIDATE_ORDERS})
    rank = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    counts = pd.to_numeric(rank.iloc[:, 2], errors="coerce").fillna(0).astype(int)
    orders = pd.Series(counts.values, index=rank.iloc[:, 0].str.strip()).groupby(level=0).max()
    return source.assign(**{ORDERS_COL: source[ID_COL].map(orders).fillna(0).astype(int)})


def attach_taxonomy(source: pd.DataFrame, path: str = TAXONOMY_PATH) -> pd.DataFrame:
    source = attach_orders(source)
    source = source.assign(**{TAX_COL: _taxonomy_paths(source, path)})
    return source


def _taxonomy_paths(source: pd.DataFrame, path: str) -> pd.Series:
    paths = _raw_taxonomy_paths(source, path)
    text = (source["PRODUCT"] + " " + source["clean_title"]).str.lower()
    for pattern, from_nodes, to_node in TAXONOMY_OVERRIDES:
        mask = paths.isin(from_nodes) & text.str.contains(pattern, regex=True)
        paths = paths.mask(mask, to_node)
    return paths


def _raw_taxonomy_paths(source: pd.DataFrame, path: str) -> pd.Series:
    if not os.path.exists(path):
        logger.warning("%s not found; taxonomy-based rules are disabled.", path)
        return pd.Series("", index=source.index)
    taxonomy = pd.read_csv(path, dtype=str, keep_default_na=False)
    paths = taxonomy.set_index(ID_COL)
    paths = paths["Main Category"] + " > " + paths["Subcategory 1"]
    return source[ID_COL].map(paths).fillna("")


def _color(row: dict[str, str]) -> str:
    text = " ".join(
        str(row.get(column, "")).lower() for column in ("PRODUCT", "clean_title")
    )
    for color in COLORS:
        if re.search(rf"\b{color}\b", text):
            return "gray" if color == "grey" else color
    return ""


MFR_PREFIX_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "MFR Prefix.csv"
)


def load_prefix_brands(path: str = MFR_PREFIX_PATH) -> dict[str, str]:
    """Map PRODUCT prefix -> manufacturer; ambiguous or generic prefixes are skipped."""
    if not os.path.exists(path):
        return {}
    names: dict[str, set[str]] = defaultdict(set)
    with open(path, newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            prefix = row["PREFIX"].strip().upper()
            name = row["Manufacturer"].strip()
            if prefix and name and "generic" not in name.lower():
                names[prefix].add(name.lower())
    return {p: next(iter(n)) for p, n in names.items() if len(n) == 1}


PREFIX_BRANDS = load_prefix_brands()


def _brand(row: dict[str, str]) -> str:
    tokens = str(row.get("PRODUCT", "")).split()
    if tokens and tokens[0].upper() in PREFIX_BRANDS:
        return PREFIX_BRANDS[tokens[0].upper()]
    return str(row.get("brand_manufacturer", "")).strip().lower()


_BREAKER_SERIES = (
    ("HOM", re.compile(r"\bHOM[A-Z]*\d*\b|Homeline", re.I)),
    ("QO", re.compile(r"\bQO[A-Z]{0,3}\d*\b")),
    ("THQL", re.compile(r"\bTHQL[A-Z]*\d*\b")),
    ("THQB", re.compile(r"\bTH?H?QB[A-Z]*\d*\b|\bTHQC\w*", re.I)),
    ("QP", re.compile(r"\bQP\w*|\bQ\d{2,3}\w*|Siemens Q\b")),
    ("BAB", re.compile(r"\bBAB\w*|\bBQD\w*|\bQPF\w*|\bBQ\w*")),
    ("BR", re.compile(r"\bBR[A-Z]{0,3}\d*\b|\bBQ[A-Z]{0,2}\d*\b")),
    ("CH", re.compile(r"\bCH[A-Z]{0,3}\d*\b|\bCL\d*\b")),
)
_PLATFORM = re.compile(r"\bM(12|18)\b|\b(12|20|60)\s*-?\s*V(?:olt)?\b", re.I)
_GAUGE = re.compile(
    r"(\d+(?:/0)?)\s*(AWG|kcmil|MCM)?\s*(?:-|to)\s*(\d+(?:/0)?)\s*(AWG|kcmil|MCM)"
    r"|(\d+(?:/0)?)\s*(AWG|kcmil|MCM)",
    re.I,
)
_CONNECTOR_WORDS = re.compile(
    r"wire connector|wire nut|splic|lever|twist|push-in|wirenut", re.I
)


_AMPS = re.compile(r"\b(\d{1,4})\s*(?:amps?|a)\b", re.I)


def amp_ratings(row: dict[str, str]) -> set[int]:
    return {int(value) for value in _AMPS.findall(_text(row))}


def _text(row: dict[str, str]) -> str:
    return " ".join(str(row.get(c, "")) for c in ("PRODUCT", "clean_title"))


def breaker_series(row: dict[str, str]) -> str:
    text = _text(row)
    for name, pattern in _BREAKER_SERIES:
        if pattern.search(text):
            return name
    return ""


_THREAD = re.compile(r"#\s?(\d{1,2})\b|\b(\d{1,2}/\d{1,2})\s*(?:inch|in\b|-\s?\d|\")", re.I)


def box_kind(row: dict[str, str]) -> str:
    text = _text(row).lower()
    if re.search(r"4-11/16|4\.6875|4 11/16", text):
        return "4-11/16"
    if re.search(r"\bsquare\b.*\b4\b|\b4\b.*\bsquare\b|\b4(?:-| )?(?:inch|in)\b.*square|\b4s\b|\b4 ?sq", text):
        return "4sq"
    if re.search(r"\b(?:2|two)[- ]gang", text):
        return "2g"
    if re.search(r"single[- ]gang|\b1[- ]gang|handy|switch box|device", text):
        return "1g"
    return ""


def fastener_diameter(row: dict[str, str]) -> str:
    match = _THREAD.search(f"{row.get('clean_title', '')} {row.get('PRODUCT', '')}")
    if not match:
        return ""
    return "#" + match.group(1) if match.group(1) else match.group(2)


def platform(row: dict[str, str]) -> str:
    match = _PLATFORM.search(_text(row))
    if not match:
        return ""
    return ("M" + match.group(1)) if match.group(1) else match.group(2) + "V"


def _gauge_area(number: str, unit: str) -> float | None:
    """Conductor size as kcmil so AWG and kcmil values are comparable."""
    unit = unit.lower()
    if "/" in number:
        aught = int(number.split("/")[0])
        awg = -(aught - 1)
    else:
        value = int(number)
        if unit in {"kcmil", "mcm"} or value > 40:
            return float(value)
        awg = value
    return 25 * 92 ** ((36 - awg) / 19.5) / 1000


_NM_GAUGE = re.compile(r"\b(\d{1,2})/[2-5]\b")
_CABLE_WORDS = re.compile(r"sheathed|nm-b|romex|metal clad|\bmc\b|\buf\b|underground feeder", re.I)


def gauge_range(row: dict[str, str]) -> tuple[float, float] | None:
    values: list[float] = []
    for match in _GAUGE.finditer(_text(row)):
        if match.group(1):
            pairs = [
                (match.group(1), match.group(2) or ""),
                (match.group(3), match.group(4)),
            ]
        else:
            pairs = [(match.group(5), match.group(6))]
        for number, unit in pairs:
            area = _gauge_area(number, unit)
            if area is not None:
                values.append(area)
    if not values:
        text = _text(row)
        nm = _NM_GAUGE.search(text)
        if nm and _CABLE_WORDS.search(text):
            area = _gauge_area(nm.group(1), "")
            if area is not None:
                values.append(area)
    if not values:
        return None
    return min(values), max(values)


def _extra_match_ok(
    rule: TaxonomyRule, target: dict[str, str], candidate: dict[str, str]
) -> bool:
    if rule.match == "series":
        a, b = breaker_series(target), breaker_series(candidate)
        return bool(a) and a == b
    if rule.match == "platform":
        a, b = platform(target), platform(candidate)
        return bool(a) and a == b
    if rule.match == "box":
        a, b = box_kind(target), box_kind(candidate)
        return bool(a) and a == b
    if rule.match == "thread":
        a, b = fastener_diameter(target), fastener_diameter(candidate)
        return bool(a) and a == b
    if rule.match == "amps":
        return bool(amp_ratings(target) & amp_ratings(candidate))
    if rule.match == "gauge":
        wanted = gauge_range(target)
        if wanted is None:
            return False
        have = gauge_range(candidate)
        if have is None:
            return bool(_CONNECTOR_WORDS.search(_text(candidate)))
        return have[0] * 0.999 <= wanted[1] and wanted[0] <= have[1] * 1.001
    return True


def _taxonomy_compatible(
    group: str, target: dict[str, str], candidate: dict[str, str]
) -> bool:
    rule = TAXONOMY_RULES[group]
    if rule.accessory_keywords and not rule.accessory_keywords.search(_text(candidate)):
        return False
    if rule.same_brand and (not _brand(target) or _brand(target) != _brand(candidate)):
        return False
    if rule.same_color:
        target_color, candidate_color = _color(target), _color(candidate)
        if not target_color or target_color != candidate_color:
            return False
    if rule.size_mode != "none" and not _sizes_compatible(
        target, candidate, strict=rule.size_mode == "strict"
    ):
        return False
    return _extra_match_ok(rule, target, candidate)


def _matching_groups(row: dict[str, str]) -> list[str]:
    text = " ".join(
        str(row.get(column, "")).lower()
        for column in ("PRODUCT", "clean_title")
    )
    subcategory = row["sub_category"].strip()
    groups = [
        name
        for name, (target_terms, target_subcategories, _) in GROUP_RULES.items()
        if subcategory in target_subcategories
        or any(term in text for term in target_terms)
    ]
    tax_path = row.get(TAX_COL, "")
    groups.extend(
        name
        for name, rule in TAXONOMY_RULES.items()
        if tax_path == rule.target_path
        and (not rule.target_keywords or rule.target_keywords.search(_text(row)))
    )
    return groups


def _spread(
    target: dict[str, str], ranked: list[dict[str, str]]
) -> list[dict[str, str]]:
    """Top sellers first, but rotate among the leading ones per target so the
    same few products are not suggested for every target of a large rule."""
    limit, anchor_count = MAX_PER_RULE, TOP_ANCHORS
    if (
        ranked
        and _node_key(ranked[0])[5:].startswith(LOW_VALUE_NODES)
        and not _node_key(target)[5:].startswith(SMALL_TOOL_NODES)
    ):
        limit, anchor_count = MAX_PER_LOW_VALUE, 0
    if len(ranked) <= limit:
        return ranked
    anchors = ranked[:anchor_count]
    pool = ranked[anchor_count : limit * SPREAD_FACTOR]
    pool.sort(
        key=lambda c: zlib.crc32(f"{target[ID_COL]}|{c[ID_COL]}".encode())
    )
    return sorted(
        anchors + pool[: limit - anchor_count], key=lambda c: (-c.get(ORDERS_COL, 0), c[ID_COL])
    )


def _node_key(row: dict[str, str]) -> str:
    return "node:" + (row.get(TAX_COL, "").strip(" >") or row["sub_category"])


# Same-node products here are substitutes, not complements.
NO_NODE_FALLBACK = (
    "Power Distribution > Safety Switches / Disconnects",
    "Power Distribution > Circuit Breakers",
    "Power Distribution > Load Centers / Panelboards",
    "Boxes & Enclosures > Boxes - ",
    "Control & Automation > Drives",
    "Fuses & Accessories > ",
    "Lighting Fixtures, Control > Photocells",
    "Lighting Fixtures, Control > Occupancy Sensors",
)
NO_SIBLING_FALLBACK = (
    "Fittings > Bushing / K.O. Seal / Locknuts",
    "Lamps, Bulbs & Ballast > Drivers - LED",
    "Lamps, Bulbs & Ballast > Bulbs - ",
    "Boxes & Enclosures > Boxes - ",
)
ONLY_WITH = {
    "Lighting Fixtures, Control > Outdoor Lighting": (
        "Lighting Fixtures, Control > Outdoor Lighting",
        "Lighting Fixtures, Control > Photocells",
        "Lighting Fixtures, Control > Timer Controls",
        "Lighting Fixtures, Control > Occupancy Sensors",
        "Lamps, Bulbs & Ballast > Bulbs - LED",
        "Connectors & Crimps > Wire Connectors",
        "Wire, Cable & Cord > Armored Cable",
        "Boxes & Enclosures > Boxes - Weatherproof",
    ),
    "Lighting Fixtures, Control > Emergency Lighting & Signs": (
        "Lighting Fixtures, Control > Emergency Lighting & Signs",
        "Lamps, Bulbs & Ballast > Drivers - LED",
        "Lamps, Bulbs & Ballast > Bulbs - LED",
        "Connectors & Crimps > Wire Connectors",
        "Boxes & Enclosures > Boxes - Metallic",
        "Boxes & Enclosures > Boxes - Non-Metallic",
    ),
    "Boxes & Enclosures > Boxes - Explosionproof": (
        "Fittings > Explosionproof Fittings",
        "Boxes & Enclosures > Boxes - Explosionproof",
        "Fittings > Conduit Hubs",
        "Fittings > Conduit Bodies",
    ),
}


def _fallback_candidates(
    target: dict[str, str],
    by_group: dict[str, list[dict[str, str]]],
    have: set[str],
    needed: int,
) -> list[dict[str, str]]:
    """Popular products from the target's own taxonomy node, size/diameter matches first."""
    if _node_key(target)[5:].startswith(NO_NODE_FALLBACK):
        return []
    target_title = _text(target)
    target_dia = fastener_diameter(target)
    pool = [
        c
        for c in by_group[_node_key(target)]
        if c[ID_COL] not in have
        and c[ID_COL] != target[ID_COL]
        and c.get(ORDERS_COL, 0) >= MIN_CANDIDATE_ORDERS
        and "obsolete" not in c["PRODUCT"].lower()
        and _text(c) != target_title
    ]
    pool.sort(
        key=lambda c: (
            not (target_dia and fastener_diameter(c) == target_dia),
            not _sizes_compatible(target, c),
            not (_brand(target) and _brand(c) == _brand(target)),
            -c.get(ORDERS_COL, 0),
            c[ID_COL],
        )
    )
    return pool[:needed]


def _sibling_fallback_candidates(
    target: dict[str, str],
    by_group: dict[str, list[dict[str, str]]],
    have: set[str],
    needed: int,
) -> list[dict[str, str]]:
    """Last resort: popular products from other nodes of the same main category.

    Same-brand products come first so thin products still get related items;
    the model decides whether any of them are actually complementary.
    """
    own = _node_key(target)
    if own[5:].startswith(NO_SIBLING_FALLBACK):
        return []
    main = target.get(TAX_COL, "").split(">")[0].strip()
    if not main or needed <= 0:
        return []
    prefix = f"node:{main} >"
    pool = [
        c
        for key, members in by_group.items()
        if key.startswith(prefix) and key != own and not key.endswith("> Tools")
        for c in members
        if c[ID_COL] not in have
        and c[ID_COL] != target[ID_COL]
        and c.get(ORDERS_COL, 0) >= MIN_CANDIDATE_ORDERS
        and "obsolete" not in c["PRODUCT"].lower()
    ]
    target_words = _significant_words(target)
    scored = []
    for c in pool:
        same_brand = bool(_brand(target)) and _brand(c) == _brand(target)
        overlap = len(target_words & _significant_words(c))
        if same_brand or overlap:
            scored.append((not same_brand, -overlap, -c.get(ORDERS_COL, 0), c[ID_COL], c))
    scored.sort(key=lambda item: item[:4])
    return [item[4] for item in scored[:needed]]


def _significant_words(row: dict[str, str]) -> set[str]:
    return {
        w
        for w in re.findall(r"[a-z]{4,}", row["PRODUCT"].lower())
        if w not in _GENERIC_WORDS
    }


_GENERIC_WORDS = {
    "with", "that", "this", "from", "type", "pack", "kit", "box", "case",
    "black", "white", "gray", "grey", "gang", "plate", "steel", "mount",
}


def _candidate_indexes(
    source: pd.DataFrame,
) -> dict[str, list[dict[str, str]]]:
    by_group: dict[str, list[dict[str, str]]] = defaultdict(list)
    rows = source.to_dict("records")
    for candidate in rows:
        candidate_subcategory = candidate["sub_category"].strip()
        for group, (_, _, allowed_subcategories) in GROUP_RULES.items():
            if candidate_subcategory in allowed_subcategories:
                by_group[group].append(candidate)
        for group, rule in TAXONOMY_RULES.items():
            if candidate.get(TAX_COL, "") == rule.accessory_path:
                by_group[group].append(candidate)
        by_group[_node_key(candidate)].append(candidate)
    ranked = sorted(rows, key=lambda c: (-c.get(ORDERS_COL, 0), c[ID_COL]))
    for job, slots in JOB_KITS.items():
        for slot in slots:
            by_group[f"kit:{job}:{slot.slot}"] = [
                c
                for c in ranked
                if c.get(ORDERS_COL, MIN_CANDIDATE_ORDERS) >= MIN_CANDIDATE_ORDERS
                and "obsolete" not in c["PRODUCT"].lower()
                and _slot_matches(slot, c)
            ]
    return by_group


GANG_MAINS = frozenset({"Wiring Devices", "Boxes & Enclosures"})
_COLOR_SKIP_NODES = ("Straight Blade", "Twistlock", "Locking")
_GANG_WORDS = (("single", 1), ("two", 2), ("three", 3), ("four", 4))


def gang_count(row: dict[str, str]) -> int:
    text = _text(row).lower()
    match = re.search(r"\b([1-9])[- ]?(?:g\b|gang)", text)
    if match:
        return int(match.group(1))
    for word, number in _GANG_WORDS:
        if re.search(rf"\b{word}[- ]gang", text):
            return number
    return 0


def _path_parts(row: dict[str, str]) -> list[str]:
    return [part.strip() for part in str(row.get(TAX_COL, "")).split(">")]


_CONDUIT_MAINS = frozenset({"Conduit, Strut & Raceways", "Fittings"})
_MATERIALS = (
    ("pvc", re.compile(r"\bpvc\b|sch(?:edule)? ?(?:40|80)|kraloy", re.I)),
    ("flex", re.compile(r"liquid|lqt|sealtight|\blt\b|\bnmlt|\bflex|\bfmc\b|greenfield", re.I)),
    ("emt", re.compile(r"\bemt\b", re.I)),
    ("rigid", re.compile(r"\brgd\b|rigid|\bgrc\b|\brmc\b|\bimc\b|\brgs\d", re.I)),
)
_MATERIAL_NODES = (
    "Conduit - ",
    "EMT Fittings",
    "Rigid Fittings",
    "PVC Fittings",
    "Conduit Bodies",
    "Conduit Hubs",
    "MC, NMB, Liquidtight, Flex Fittings",
    "Bushing / K.O. Seal / Locknuts",
    "Straps",
    "Clips, Clamps, Hangers",
)


_PLASTIC = re.compile(r"plastic|pvc", re.I)
_DEVICE_HUB = re.compile(r"plate type hub|\bDG, DH", re.I)
_REDUCER = re.compile(r"reducing|offset nipple", re.I)
_BODY_PART = re.compile(r"\b(?:cover|cvr|gasket|gask\w*|gk\w*)\b", re.I)


def conduit_materials(row: dict[str, str]) -> set[str]:
    text = _text(row)
    found = {name for name, pattern in _MATERIALS if pattern.search(text)}
    if not found:
        node = _path_parts(row)[1:2]
        if node and node[0] == "EMT Fittings":
            found = {"emt"}
        elif node and node[0] == "Rigid Fittings":
            found = {"rigid"}
    return found


_GAUGE_MAINS = frozenset({"Wire, Cable & Cord", "Connectors & Crimps"})


def hard_conflict(target: dict[str, str], cand: dict[str, str]) -> str:
    """Return 'gang', 'color', 'series', 'box', 'gauge', 'material', 'size' or 'part' when a pair can never go together, else ''."""
    tparts, cparts = _path_parts(target), _path_parts(cand)
    allowed = ONLY_WITH.get(" > ".join(tparts[:2]))
    if allowed and " > ".join(cparts[:2]) not in allowed:
        return "unrelated"
    if tparts[0] == cparts[0] == "Power Distribution":
        tseries, cseries = breaker_series(target), breaker_series(cand)
        if tseries and cseries and tseries != cseries:
            return "series"
    if tparts[0] == cparts[0] == "Boxes & Enclosures":
        tkind, ckind = box_kind(target), box_kind(cand)
        if tkind and ckind and tkind != ckind:
            return "box"
    if (
        tparts[0] in _CONDUIT_MAINS
        and cparts[0] in _CONDUIT_MAINS
        and tparts[1:2][0].startswith(_MATERIAL_NODES)
        and cparts[1:2][0].startswith(_MATERIAL_NODES)
    ):
        if tparts[1].startswith("Conduit - ") and cparts[1] == "Conduit Bodies" and _BODY_PART.search(
            str(cand.get("PRODUCT", ""))
        ):
            return "part"
        if _DEVICE_HUB.search(str(cand.get("PRODUCT", ""))):
            return "part"
        if tparts[1].startswith("Conduit - ") and _REDUCER.search(str(cand.get("PRODUCT", ""))):
            return "part"
        tmat, cmat = conduit_materials(target), conduit_materials(cand)
        if len(tmat) == 1 and cmat and not tmat & cmat:
            return "material"
        if (
            tmat == {"pvc"}
            and cparts[1] == "Bushing / K.O. Seal / Locknuts"
            and "pvc" not in cmat
            and not _PLASTIC.search(str(cand.get("PRODUCT", "")))
        ):
            return "material"
        tsizes, csizes = trade_sizes(target), trade_sizes(cand)
        if tsizes and csizes and not any(abs(tsizes[0] - x) < 1e-6 for x in csizes):
            return "size"
    if (
        tparts[0] in _GAUGE_MAINS
        and cparts[0] in _GAUGE_MAINS
        and "Instrumentation Cable" not in (tparts[1:2] + cparts[1:2])
    ):
        trange, crange = gauge_range(target), gauge_range(cand)
        if trange and crange and not (crange[0] * 0.999 <= trange[1] and trange[0] <= crange[1] * 1.001):
            return "gauge"
    if tparts[0] not in GANG_MAINS or cparts[0] not in GANG_MAINS:
        return ""
    tgang, cgang = gang_count(target), gang_count(cand)
    if tgang and cgang and tgang != cgang:
        return "gang"
    if tparts[0] == cparts[0] == "Wiring Devices" and not any(
        node.startswith(_COLOR_SKIP_NODES)
        for node in tparts[1:2] + cparts[1:2]
    ):
        tcolor, ccolor = _color(target), _color(cand)
        if tcolor and ccolor and tcolor != ccolor:
            return "color"
    return ""


def _drop_conflicts(
    target: dict[str, str], candidates: list[dict[str, str]]
) -> list[dict[str, str]]:
    return [c for c in candidates if not hard_conflict(target, c)]


def build_candidates(
    target: dict[str, str],
    by_group: dict[str, list[dict[str, str]]],
) -> list[dict[str, str]]:
    candidates = _drop_conflicts(target, _eligible_candidates(target, by_group))
    if len(candidates) < MIN_SUGGESTIONS:
        have = {c[ID_COL] for c in candidates}
        candidates = candidates + _drop_conflicts(
            target,
            _fallback_candidates(
                target, by_group, have, FALLBACK_FILL - len(candidates)
            ),
        )
        if len(candidates) < MIN_SUGGESTIONS:
            have = {c[ID_COL] for c in candidates}
            candidates = candidates + _drop_conflicts(
                target,
                _sibling_fallback_candidates(
                    target, by_group, have, FALLBACK_FILL - len(candidates)
                ),
            )
    if len(candidates) <= MAX_CANDIDATES:
        return candidates

    by_subcategory: dict[str, list[dict[str, str]]] = defaultdict(list)
    for candidate in candidates:
        by_subcategory[candidate["sub_category"]].append(candidate)
    for subcategory_candidates in by_subcategory.values():
        subcategory_candidates.sort(key=lambda candidate: (-candidate.get(ORDERS_COL, 0), candidate[ID_COL]))

    selected: list[dict[str, str]] = []
    subcategories = sorted(by_subcategory)
    round_index = 0
    while len(selected) < MAX_CANDIDATES:
        added = False
        for subcategory in subcategories:
            products = by_subcategory[subcategory]
            if round_index < len(products):
                selected.append(products[round_index])
                added = True
                if len(selected) == MAX_CANDIDATES:
                    break
        if not added:
            break
        round_index += 1
    return selected


def _eligible_candidates(
    target: dict[str, str],
    by_group: dict[str, list[dict[str, str]]],
) -> list[dict[str, str]]:
    target_id = target[ID_COL]
    groups = _matching_groups(target)
    candidates: list[dict[str, str]] = []
    seen: set[str] = {target_id}
    for group in groups:
        group_candidates: list[dict[str, str]] = []
        for candidate in by_group[group]:
            if (
                candidate.get(ORDERS_COL, MIN_CANDIDATE_ORDERS) < MIN_CANDIDATE_ORDERS
                or "obsolete" in candidate["PRODUCT"].lower()
            ):
                continue
            if group in TAXONOMY_RULES and not _taxonomy_compatible(
                group, target, candidate
            ):
                continue
            if group in SIZE_MATCHED_GROUPS and not _sizes_compatible(
                target, candidate, strict=group in STRICT_SIZE_GROUPS
            ):
                continue
            group_candidates.append(candidate)
        if group in TAXONOMY_RULES:
            group_candidates.sort(
                key=lambda c: (-c.get(ORDERS_COL, 0), c[ID_COL])
            )
            group_candidates = _spread(target, group_candidates)
        for candidate in group_candidates:
            if candidate[ID_COL] not in seen:
                seen.add(candidate[ID_COL])
                candidates.append(candidate)
    if len(candidates) >= KIT_ONLY_BELOW:
        return candidates
    return candidates + _kit_candidates(target, by_group, seen)


def _kit_candidates(
    target: dict[str, str],
    by_group: dict[str, list[dict[str, str]]],
    seen: set[str],
) -> list[dict[str, str]]:
    """Products that belong to the same job as the target but a different slot."""
    added: list[dict[str, str]] = []
    for job, slots in JOB_KITS.items():
        own = {slot.slot for slot in slots if _slot_matches(slot, target)}
        if not own:
            continue
        # A broad main-category match (e.g. any Wiring Device) only says the
        # target belongs to the job's trade, so stay in that category.
        broad = {
            slot.path for slot in slots
            if slot.slot in own and slot.path and ">" not in slot.path
        } or ({""} if any(slot.slot in own and not slot.path for slot in slots) else set())
        for slot in slots:
            if slot.slot in own:
                continue
            taken = 0
            ranked = by_group[f"kit:{job}:{slot.slot}"][:KIT_POOL_PER_SLOT]
            if ranked:
                offset = _stable_int(target[ID_COL]) % len(ranked)
                ranked = ranked[offset:] + ranked[:offset]
            for candidate in ranked:
                if taken >= slot.per_slot:
                    break
                if candidate[ID_COL] in seen or candidate[ID_COL] == target[ID_COL]:
                    continue
                if candidate.get(TAX_COL, "").endswith("Wallplates"):
                    continue
                if broad and len(seen) >= 8 and not any(
                    candidate.get(TAX_COL, "").startswith(b) for b in broad if b
                ):
                    continue
                seen.add(candidate[ID_COL])
                added.append(candidate)
                taken += 1
        if len(added) >= KIT_MAX_TOTAL:
            break
    return added[:KIT_MAX_TOTAL]


def preview_candidates(source: pd.DataFrame) -> None:
    by_group = _candidate_indexes(source)
    candidates_by_target: Counter[str] = Counter()
    counts: list[int] = []
    rule_counts: list[int] = []
    for target in source.to_dict("records"):
        eligible = _eligible_candidates(target, by_group)
        candidates = build_candidates(target, by_group)
        counts.append(len(candidates))
        rule_counts.append(len(eligible))
        candidates_by_target.update(candidate[ID_COL] for candidate in candidates)

    zero = sum(count == 0 for count in counts)
    truncated = sum(count > MAX_CANDIDATES for count in counts)
    logger.info(
        "Rule-based candidates: %d products have none, %d have fewer than %d; "
        "after node fallback: %d have none, %d have fewer than %d",
        sum(c == 0 for c in rule_counts),
        sum(c < MIN_SUGGESTIONS for c in rule_counts),
        MIN_SUGGESTIONS,
        zero,
        sum(c < MIN_SUGGESTIONS for c in counts),
        MIN_SUGGESTIONS,
    )
    logger.info(
        "Dry run: %d products; %d have eligible candidates; %d have none; "
        "average pool %.1f (max %d); %d pools balanced and capped at %d candidates",
        len(counts),
        len(counts) - zero,
        zero,
        sum(counts) / len(counts) if counts else 0,
        max(counts, default=0),
        truncated,
        MAX_CANDIDATES,
    )
    products = source.set_index(ID_COL)
    logger.info("Most frequently included in candidate lists (not model selections):")
    for product_id, target_count in candidates_by_target.most_common(20):
        row = products.loc[product_id]
        logger.info("  %s (%s): eligible for %d targets", product_id, row["PRODUCT"], target_count)


def _candidate_payload(candidates: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    return [
        {
            "id": candidate[ID_COL],
            "title": candidate["clean_title"] or candidate["PRODUCT"],
            "specs": candidate["normalized_specs"],
            "category": candidate["primary_category"],
            "sub_category": candidate["sub_category"],
        }
        for candidate in candidates
    ]


@retry(
    retry=retry_if_exception(_is_retryable),
    wait=wait_random_exponential(multiplier=2, max=60),
    stop=stop_after_attempt(MAX_ATTEMPTS),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)
async def select_accessories(
    client: genai.Client,
    target: dict[str, str],
    candidates: list[dict[str, str]],
    model: str,
) -> list[str]:
    candidate_payload = _candidate_payload(candidates)
    if not candidate_payload:
        return []
    target_payload = {
        "id": target[ID_COL],
        "title": target["clean_title"] or target["PRODUCT"],
        "specs": target["normalized_specs"],
        "description": target["short_description"],
        "category": target["primary_category"],
        "sub_category": target["sub_category"],
    }
    if jobs := kit_jobs(target):
        target_payload["install_jobs"] = jobs
    response = await client.aio.models.generate_content(
        model=model,
        contents=(
            f"Target product:\n{target_payload}\n\n"
            f"Candidate accessories:\n{candidate_payload}"
        ),
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_schema=AccessorySelection,
            temperature=0.1,
        ),
    )
    parsed = response.parsed
    if isinstance(parsed, AccessorySelection):
        selection = parsed.product_ids
    elif response.text:
        selection = AccessorySelection.model_validate_json(response.text).product_ids
    else:
        raise ValueError("Empty or unparsable model response")
    allowed = {candidate[ID_COL] for candidate in candidates}
    return list(dict.fromkeys(item for item in selection if item in allowed))[:10]


def top_up_selection(
    target: dict[str, str],
    by_group: dict[str, list[dict[str, str]]],
    selected: list[str],
) -> list[str]:
    """Fill a short model selection from rule-matched candidates only.

    Fallback candidates are excluded: they are same-category filler, and the
    model's rejection of them is usually correct.
    """
    if len(selected) >= MIN_SUGGESTIONS:
        return selected
    rule_based = _drop_conflicts(target, _eligible_candidates(target, by_group))
    result = list(selected)
    for candidate in rule_based:
        if len(result) >= MIN_SUGGESTIONS:
            break
        if candidate[ID_COL] not in result and candidate[ID_COL] != target[ID_COL]:
            result.append(candidate[ID_COL])
    return result


def load_checkpoint(path: str) -> pd.DataFrame:
    if not os.path.exists(path):
        return pd.DataFrame(columns=[OUTPUT_ID_COL, *RELATION_COLUMNS])
    checkpoint = pd.read_csv(path, dtype=str, keep_default_na=False)
    expected = {OUTPUT_ID_COL, *RELATION_COLUMNS}
    missing = expected - set(checkpoint.columns)
    if missing:
        raise ValueError(f"{path} is missing columns: {sorted(missing)}")
    return checkpoint[[OUTPUT_ID_COL, *RELATION_COLUMNS]].drop_duplicates(
        subset=OUTPUT_ID_COL, keep="last"
    )


def save_atomic(df: pd.DataFrame, path: str) -> None:
    temporary_path = f"{path}.tmp"
    df.to_csv(temporary_path, index=False, encoding="utf-8")
    os.replace(temporary_path, path)


async def run(
    input_path: str,
    output_path: str,
    checkpoint_path: str,
    model: str,
    sample: int = 0,
) -> int:
    load_dotenv()
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set (add it to the environment or .env).")

    source = pd.read_csv(input_path, dtype=str, keep_default_na=False)
    missing = REQUIRED_COLUMNS - set(source.columns)
    if missing:
        raise ValueError(f"{input_path} is missing columns: {sorted(missing)}")
    source = attach_taxonomy(source.drop_duplicates(subset=ID_COL, keep="first"))
    by_group = _candidate_indexes(source)
    if sample:
        has_pool = source[
            [bool(build_candidates(t, by_group)) for t in source.to_dict("records")]
        ]
        source = has_pool.sample(n=min(sample, len(has_pool)), random_state=42)
    done = load_checkpoint(checkpoint_path)
    pending = source[~source[ID_COL].isin(set(done[OUTPUT_ID_COL]))]
    logger.info("%d rows total, %d done, %d pending", len(source), len(done), len(pending))

    client = genai.Client(api_key=api_key)
    semaphore = asyncio.Semaphore(CONCURRENCY)
    failures = 0

    async def worker(target: dict[str, str]) -> dict[str, str] | None:
        nonlocal failures
        candidates = build_candidates(target, by_group)
        async with semaphore:
            try:
                selected = await select_accessories(client, target, candidates, model)
            except Exception as exc:
                failures += 1
                logger.error("Failed %s (%r): %s", target[ID_COL], target["PRODUCT"], exc)
                return None
        selected = top_up_selection(target, by_group, selected)
        return {
            OUTPUT_ID_COL: target[ID_COL],
            **dict(zip(RELATION_COLUMNS, selected + [""] * (10 - len(selected)))),
        }

    records = pending.to_dict("records")
    with tqdm(total=len(records), desc="Generating accessories") as progress:
        for start in range(0, len(records), CHECKPOINT_EVERY):
            batch = records[start : start + CHECKPOINT_EVERY]
            tasks = [asyncio.ensure_future(worker(target)) for target in batch]
            new_rows = []
            for task in asyncio.as_completed(tasks):
                row = await task
                progress.update(1)
                if row:
                    new_rows.append(row)
            if new_rows:
                done = pd.concat([done, pd.DataFrame(new_rows)], ignore_index=True)
                save_atomic(done, checkpoint_path)

    final = source[[ID_COL]].rename(columns={ID_COL: OUTPUT_ID_COL}).merge(
        done, on=OUTPUT_ID_COL, how="left"
    )
    final = final[[OUTPUT_ID_COL, *RELATION_COLUMNS]].fillna("")
    save_atomic(final, output_path)
    logger.info("Wrote %s (%d failures; re-run to retry them)", output_path, failures)
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default="enriched_products.csv")
    parser.add_argument("--output", default="product_accessories.csv")
    parser.add_argument(
        "--checkpoint", default=f"product_accessories.{CHECKPOINT_VERSION}.checkpoint.csv"
    )
    parser.add_argument("--model", default=MODEL, choices=("gemini-3.1-flash-lite", "gemini-2.5-flash"))
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview candidate eligibility without calling Gemini or writing output.",
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=0,
        help="Run Gemini on N random products only, using separate sample output/checkpoint files.",
    )
    args = parser.parse_args()
    if args.sample:
        if args.output == "product_accessories.csv":
            args.output = "product_accessories.sample.csv"
        if args.checkpoint == f"product_accessories.{CHECKPOINT_VERSION}.checkpoint.csv":
            args.checkpoint = f"product_accessories.{CHECKPOINT_VERSION}.sample.checkpoint.csv"
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.dry_run:
        try:
            source = pd.read_csv(args.input, dtype=str, keep_default_na=False)
            missing = REQUIRED_COLUMNS - set(source.columns)
            if missing:
                parser.error(f"{args.input} is missing columns: {sorted(missing)}")
            source = attach_taxonomy(
                source.drop_duplicates(subset=ID_COL, keep="first")
            )
            preview_candidates(source)
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
        return
    try:
        failures = asyncio.run(run(args.input, args.output, args.checkpoint, args.model, args.sample))
    except (OSError, RuntimeError, ValueError) as exc:
        parser.error(str(exc))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
