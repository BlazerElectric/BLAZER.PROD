"""Generate complementary electrical accessories for an enriched product catalog.

Usage:
    python generate_accessories.py [--input enriched_products.csv]
        [--output product_accessories.csv]
        [--checkpoint product_accessories.v6.checkpoint.csv]

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

from checkpoint_safety import require_checkpoint

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
CHECKPOINT_VERSION = "v6"
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
CURATED_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "curated_accessories.csv")


def load_curated_accessories(path: str = CURATED_PATH) -> pd.DataFrame:
    if os.path.exists(path):
        df = pd.read_csv(path, dtype=str, keep_default_na=False)
        return df.drop_duplicates(subset=OUTPUT_ID_COL, keep="last")
    return pd.DataFrame(columns=[OUTPUT_ID_COL, *RELATION_COLUMNS])


def validate_curated_accessories(curated: pd.DataFrame, product_ids: set[str]) -> None:
    invalid = []
    for row in curated.to_dict("records"):
        target = row[OUTPUT_ID_COL]
        if target not in product_ids:
            continue
        for column in RELATION_COLUMNS:
            accessory = row[column].strip()
            if accessory and accessory not in product_ids:
                invalid.append(f"{target} {column}: {accessory}")
    if invalid:
        raise ValueError(
            "Curated accessories reference products missing from the catalog: "
            + "; ".join(invalid)
        )


class TaxonomyRule(NamedTuple):
    target_path: str
    accessory_path: str
    same_brand: bool
    same_color: bool
    size_mode: str
    match: str
    target_keywords: re.Pattern | None
    accessory_keywords: re.Pattern | None
    verified_fit: bool


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
                (row.get("notes") or "").strip().lower() == "verified_fit",
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
Aim to select up to 10 complementary accessories (ideally 8 to 10) whenever
compatible candidates exist in the list, and never return the target ID.
Only select complementary, mating, required, mounting, grounding, sealing,
termination, or installation consumables. Do not select direct substitutes,
another color/brand/size version of the target, unrelated products, or items
that merely belong to the same broad category. Use only IDs in the candidate
list. Select diverse accessories (e.g. choose at most one size/variant of a
ground bar, enclosure, or connector, and at most one tester or meter, rather than multiple models of the same part)
so the customer sees a well-rounded installation package. It is valid to return an empty list when no candidate is clearly
compatible. If the target lists install_jobs, favor candidates an electrician
would need for the same job. Consider the target's size, material, voltage, wiring method, and
other specifications before selecting an accessory.

Each candidate includes its recent order volume ("popularity"). Strongly prefer candidates
with proven high sales volume over low-volume or obscure SKUs. Never pick a rarely bought SKU
when a high-volume trade staple serves the job.

Order selected candidate IDs (Slots 1 to 10) balancing installation necessity with popularity:
- Slots 1-3: The most critical, high-demand core installation essentials that electricians routinely buy to install this item (e.g. top-selling connectors, couplings, matching wallplates, primary mounting rough-in boxes/enclosures, or mating plugs). For fuses, prioritize the core equipment they are installed into (matching fusible safety switches, fused disconnect switches, fuse blocks, or fuse holders). Do NOT force low-volume or niche items into slots 1-3 just because they are technically a mating part; favor proven, high-order workhorses.
- Slots 4-7: High-utility installation complements and routing/securing hardware (e.g. conduit straps, hangers, fittings, wire, cable, mud rings, grounding pigtails, clamps, fuse pullers, lockout/tagout devices).
- Slots 8-10: Practical finishing items, trade consumables, and staples (e.g. locknuts, electrical tape, wire nuts, pulling lube, covers, labels, duct seal, continuity/voltage testers).
Within each group, prioritize higher popularity candidates.
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
    sizes = [_parse_size(token) for token in _SIZE_PATTERN.findall(text)]
    return sizes or [_parse_size(token) for token in _SIZE_PATTERN.findall(str(row.get("PRODUCT", "")))]


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
        r"^rac\s+752\b",
        (),
        "Boxes & Enclosures > Boxes - Metallic > 4 Square Boxes > 4IN Square Covers",
    ),
    (
        r"^rac\s+902c\b",
        (),
        "Boxes & Enclosures > Boxes - Metallic > 4 Square Boxes > Industrial Cover",
    ),
    (
        r"^bpt\s+lb-42cg\b",
        (),
        "Fittings > Conduit Bodies > Conduit Bodies Aluminum > Conduit Bodies Type LB",
    ),
    (
        r"\btp262-w\b",
        (),
        "Wiring Devices > Wallplates > Decora Plates > 2  Gang",
    ),
    (
        r"\b38ast\b|\bmc connector\b",
        ("Fittings > NM Cable Fittings",),
        "Fittings > MC, NMB, Liquidtight, Flex Fittings",
    ),
    (
        r"\btp26[- ]\w+\b.*\bplate\b",
        ("Wiring Devices > Combination Devices",),
        "Wiring Devices > Wallplates",
    ),
    (
        r"\bunitap\b|\buni-tap\b|\bsplice reducer\b",
        ("Connectors & Crimps > Wire Connectors",),
        "Connectors & Crimps > Lugs & Split Bolts",
    ),
    (
        r"\bl11-tl\d+\b",
        ("Control & Automation > Terminal Blocks", "Boxes & Enclosures > Boxes - Non-Metallic"),
        "Connectors & Crimps > Lugs & Split Bolts",
    ),
    (
        r"\bled\b.*\bt[58]\b|\bt[58]\b.*\bled\b",
        ("Lamps, Bulbs & Ballast > Bulbs - Halogen",),
        "Lamps, Bulbs & Ballast > Bulbs - LED > LED - Tube Type",
    ),
    (
        r"\b(?:tp[1-4]|p[1-4])\b.*plate|wall plate|switch plate",
        ("Lighting Fixtures, Control > Occupancy Sensors",),
        "Wiring Devices > Wallplates",
    ),
    (
        r"wall pack|open face.*housing|traditional.*wall|wpled",
        ("Lighting Fixtures, Control > Occupancy Sensors",),
        "Lighting Fixtures, Control > Outdoor Lighting",
    ),
    (
        r"\brsc\d+|screw cvr wall mnt|n3r.*enclosure|nema 3r.*enclosure",
        ("Data & Telecom > Audio Video Equipment",),
        "Boxes & Enclosures > Enclosures - Commercial Type",
    ),
    (
        r"k4221|swivel mount|photocontrol|photo control",
        ("Data & Telecom > Cable Tray",),
        "Lighting Fixtures, Control > Photocells",
    ),
    (
        r"unitap|splice reducer|tap connector",
        ("Fittings > PVC Fittings",),
        "Connectors & Crimps > Lugs & Split Bolts",
    ),
    (
        r"thermoplastic.*(?:sw|switch|outlet|device) box",
        ("Boxes & Enclosures > Boxes - Metallic",),
        "Boxes & Enclosures > Boxes - Non-Metallic",
    ),
    (
        r"weatherhead|service entrance cap",
        ("Wire, Cable & Cord > Service Wire",),
        "Fittings > Flashings, Weatherhead",
    ),
    (
        r"surge arrest|surge protect",
        ("Control & Automation > Starters & Contactors",),
        "Power Distribution > Circuit Breakers",
    ),
    (
        r"mobile home feeder",
        ("Caddy > Cable/Conduit",),
        "Wire, Cable & Cord > Building Wire",
    ),
    (
        r"^(?!.*(?:lamp|bulb|ex39|mogul|g24|t8|t5)).*\b(?:jack|keystone|patch panel)\b",
        ("Lighting Fixtures, Control > Bulbs - LED", "Lamps, Bulbs & Ballast > Bulbs - LED"),
        "Data & Telecom > Workstation",
    ),
    (
        r"soladeck|grounding block|ground block",
        ("Boxes & Enclosures > Boxes - Metallic",),
        "Data & Telecom > Grounding and Bonding",
    ),
    (
        r"screw cover gutter|wireway|gutter",
        ("Power Distribution > Load Centers / Panelboards", "Boxes & Enclosures > Boxes - Non-Metallic"),
        "Conduit, Strut & Raceways > Wire Duct, Wire Trough, Wireway",
    ),
    (
        r"\b(?:doorbell|chime)\b",
        ("Power Distribution > Transformers", "Fasteners & Hardware > Anchors"),
        "Fire Alarm, Safety, Security > Doorbell Camera",
    ),
    (
        r"^(?!.*con\s+(?:emt|imc|rigid|grc)\s+.*conduit).*(?:\b(?:40ast|ast)\b|\bconduit connector\b|flexible metal conduit connector|connector for flexible metal|squeeze connector)",
        ("Conduit, Strut & Raceways > Conduit - Metallic", "Conduit, Strut & Raceways > Conduit - Flexible"),
        "Fittings > MC, NMB, Liquidtight, Flex Fittings",
    ),
    (
        r"allied moulded.*device box|device box.*(?:nail|bracket|ears)|thermoplastic.*device box",
        ("Conduit, Strut & Raceways > Raceway Boxes - Metallic",),
        "Boxes & Enclosures > Boxes - Non-Metallic",
    ),
    (
        r"liquid ?tight.*connector|push.?(?:in|on).*connector.*liquid",
        (
            "Connectors & Crimps > Connectors, Crimps, Disconnects",
            "Connectors & Crimps > Wire Connectors",
            "Fittings > nan",
            "Fittings > ",
        ),
        "Fittings > MC, NMB, Liquidtight, Flex Fittings",
    ),
    (
        r"loadcenter|load center",
        ("Lamps, Bulbs & Ballast > Bulbs Incandescent",),
        "Power Distribution > Load Centers / Panelboards",
    ),
    (
        r"socket adapter|square.*hex",
        ("Fuses & Accessories > Plug Fuses & Accessories",),
        "Tools & Meters > Bits, Blades, Punches, Holesaws",
    ),
    (
        r"screw cover.*(?:enclosure|box|pull box)|nema 1.*screw cover|nema 3r.*screw cover|n1.*screw cover|rsc\d+",
        (
            "Boxes & Enclosures > FS & FD Devices",
            "Boxes & Enclosures > Boxes - Non-Metallic",
            "Data & Telecom > Audio Video Equipment",
            "Boxes & Enclosures > Underground Pull Boxes",
            "Fasteners & Hardware > Screws",
        ),
        "Boxes & Enclosures > Enclosures - Commercial Type",
    ),
    (
        r"clamp meter|true[ -]rms",
        ("Fasteners & Hardware > Guy Wire Hardware",),
        "Tools & Meters > Meters - Test Equipment - Calculators",
    ),
    (
        r"mast guying kit|mast kit",
        ("Fasteners & Hardware > Guy Wire Hardware",),
        "Fittings > Flashings, Weatherhead",
    ),
    (
        r"f-connector|\bhdmi\b|\bkeystone\b",
        ("Connectors & Crimps > Connectors, Crimps, Disconnects",),
        "Data & Telecom > Workstation",
    ),
    (
        r"air conditioning disconnect|\bac disconnect\b",
        ("Connectors & Crimps > Connectors, Crimps, Disconnects",),
        "Power Distribution > Safety Switches / Disconnects",
    ),
    (
        r"push button.*station",
        ("Connectors & Crimps > Connectors, Crimps, Disconnects",),
        "Control & Automation > Pilot Devices",
    ),
    (
        r"unitap|splice reducer|in-line splice|splice connector",
        ("Connectors & Crimps > Connectors, Crimps, Disconnects",),
        "Connectors & Crimps > Lugs & Split Bolts",
    ),
    (
        r"crimp sleeve|splice cap",
        ("Connectors & Crimps > Connectors, Crimps, Disconnects",),
        "Connectors & Crimps > Wire Connectors",
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
    (
        r"underground enclosure|tier \d|quaz|polymer box|handhole|\bpe\d{2}\b|\bpg\d{4}\b|\bpc\d{4}\b",
        (
            "Connectors & Crimps > Wire Connectors",
            "Boxes & Enclosures > Boxes - Weatherproof",
            "Boxes & Enclosures > Boxes - Metallic",
            "Boxes & Enclosures > Boxes - Non-Metallic",
            "Boxes & Enclosures > ",
        ),
        "Boxes & Enclosures > Underground Pull Boxes",
    ),
    (
        r"^(?!.*(?:clip|clamp|connector|hanger|support)).*(?:metal clad|\bmc cable\b|armored)",
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
        r"telephone jack|data jack|keystone|tracjack|trackjack",
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
        r"locking|twist-?lock|turnlok|\bl\d{1,2}-\d{2}|cs\d{4}",
        ("Wiring Devices > Straight Blade 10-20 Amp", "Wiring Devices > Receptacles", "Lighting Fixtures, Control > Photocells", "Wiring Devices > Combination Devices"),
        "Wiring Devices > Twistlock",
    ),
    (
        r"\b(?:c[- ]tap|h[- ]tap)\b",
        ("Fittings > EMT Fittings", "Wire, Cable & Cord > Service Wire", "Conduit, Strut & Raceways > Conduit - Metallic"),
        "Connectors & Crimps > Lugs & Split Bolts",
    ),
    (
        r"\b(?:jiffy clip|stud clip|cable clip)\b",
        ("Wire, Cable & Cord > Armored Cable", "Wire, Cable & Cord > Building Wire"),
        "Fittings > Clips, Clamps, Hangers",
    ),
    (
        r"offset nipple|chase nipple",
        ("Fittings > EMT Fittings", "Fittings > Rigid Fittings"),
        "Fittings > Bushing / K.O. Seal / Locknuts",
    ),
    (
        r"thermoplastic.*(?:sw|switch|outlet|box)\b",
        ("Conduit, Strut & Raceways > Raceway Boxes - Metallic", "Boxes & Enclosures > Boxes - Metallic"),
        "Boxes & Enclosures > Boxes - Non-Metallic",
    ),
    (
        r"bluetooth.*speaker|jobsite speaker|wearable.*speaker|speaker.*wearable",
        ("Data & Telecom > Residential", "Data & Telecom > Audio Video Equipment"),
        "Tools & Meters > Hand Tools",
    ),
    (
        r"ugskit|underground splice kit",
        ("Connectors & Crimps > Grounding Products", "Fans, Heaters > Heat Cable"),
        "Connectors & Crimps > Splicing & Terminating Kits",
    ),
    (
        r"wall ?plate|plate.*(?:blank|box mount|toggle|duplex)",
        ("Data & Telecom > Audio Video Equipment",),
        "Wiring Devices > Wallplates",
    ),
    (
        r"non[- ]?metallic junction box|jbx\d+|pvc.*junction box",
        ("Boxes & Enclosures > Boxes - Explosionproof",),
        "Boxes & Enclosures > Boxes - Non-Metallic",
    ),
    (
        r"512h|box hanger|hanger.*box",
        ("Caddy > Cable/Conduit", "Caddy > Ceiling/Acoustical"),
        "Fittings > Clips, Clamps, Hangers",
    ),
    (
        r"11-in-1|multibit|multi-bit|screwdriver",
        ("Tools & Meters > Bits, Blades, Punches, Holesaws",),
        "Tools & Meters > Hand Tools",
    ),
    (
        r"jiffy clip|pipe strap|conduit strap|1-hole strap|one-hole strap|2-hole strap|th-18\d{2}|th-19\d{2}|\b18\d{2}\b.*strap|\b19\d{2}\b.*strap",
        ("Conduit, Strut & Raceways > Strut", "Wire, Cable & Cord > Armored Cable"),
        "Fittings > Straps",
    ),
    (
        r"unitap|uni-tap|splice reducer",
        (
            "Connectors & Crimps > Distribution & Terminal Blocks",
            "Fasteners & Hardware > Tapes",
            "Fittings > PVC Fittings",
            "Connectors & Crimps > Connectors, Crimps, Disconnects",
        ),
        "Connectors & Crimps > Lugs & Split Bolts",
    ),
    (
        r"insulation piercing|bipc\d+",
        ("Connectors & Crimps > Splicing & Terminating Kits",),
        "Connectors & Crimps > Lugs & Split Bolts",
    ),
    (
        r"dvstv|dvtv|ntstv|rh4fbl3|mrf2s-8sd010|0-10v.*dimmer|0-10.*dim",
        (
            "Wiring Devices > Switches",
            "Wiring Devices",
        ),
        "Lighting Fixtures, Control > Dimmers/Dimming Controls",
    ),
    (
        r"time clock|time switch|timeswitch|\bt10[1-4]\b|\be101b\b|tripper.*t100|156t|\bet\d{4}c\b|\b1109a\b",
        (
            "Lighting Fixtures, Control > Occupancy Sensors",
            "Wiring Devices > Switches",
            "Control & Automation > Disconnect Switches",
        ),
        "Lighting Fixtures, Control > Timer Controls",
    ),
    (
        r"power monitor|phase monitor|voltage monitor|phase loss",
        (
            "Control & Automation > Measuring, Monitoring & Logic Devices",
            "Power Distribution > Transformers",
            "Control & Automation > ",
        ),
        "Control & Automation > Relays",
    ),
    (
        r"distribution block|\bpdb\b",
        ("Power Distribution > ", "Power Distribution > Load Centers / Panelboards"),
        "Connectors & Crimps > Lugs & Split Bolts",
    ),
    (
        r"\b[1-4]fs[cd]?\b|\b[1-4]fd[cd]?\b|cast (?:iron )?device box",
        ("Conduit, Strut & Raceways > Raceway Boxes - Metallic",),
        "Boxes & Enclosures > Boxes - Metallic",
    ),
    (
        r"tombstone|fl skt|t8 retro fit kit|t8 retrofit",
        (
            "Tools & Meters > Hand Tools",
            "Lighting Fixtures, Control > Indoor Lighting",
            "Lamps, Bulbs & Ballast > Bulbs - LED",
        ),
        "Lighting Fixtures, Control > Lampholders, Adapters & Extensions",
    ),
    (
        r"v20\d{2}|2010|2011|2048|2001 stl",
        (
            "Conduit, Strut & Raceways > Raceway - Non-Metallic",
            "Conduit, Strut & Raceways > Raceway Boxes - Metallic",
            "Fittings > Rigid Fittings",
            "Data & Telecom > Non-Metallic Raceway",
        ),
        "Conduit, Strut & Raceways > Plugmold",
    ),
    (
        r"lattice guard|guard cage|stringlight guard",
        ("Lighting Fixtures, Control > Indoor Lighting",),
        "Lighting Fixtures, Control > Temporary Lighting",
    ),
    (
        r"dou125|dos125|dou200|dou275|dos5s|dou5s|dou9s|nitrus omt|open-lok|multi-tool blade|oscillating blade",
        (
            "Fittings > Bushing / K.O. Seal / Locknuts",
            "Tools & Meters > Cordless Tools",
            "Tools & Meters > Bits, Blades, Punches, Holesaws",
        ),
        "Tools & Meters > Bits, Blades, Punches, Holesaws",
    ),
    (
        r"sawzall|recip.*blade|the torch|the ax|\bax\b.*blade|torch.*nitrus|nitrus.*carbide|demo demon.*blade|steel demon.*recip",
        (
            "Tools & Meters > Cordless Tools",
            "Tools & Meters > Bits, Blades, Punches, Holesaws",
        ),
        "Tools & Meters > Bits, Blades, Punches, Holesaws",
    ),
    (
        r"flush mount|flush with alabaster|vanity strip|vanity mount|bath bar|semi-flush",
        (
            "Lighting Fixtures, Control > Occupancy Sensors",
            "Lighting Fixtures, Control > Temporary Lighting",
            "Lighting Fixtures, Control > Emergency Lighting & Signs",
        ),
        "Lighting Fixtures, Control > Indoor Lighting",
    ),
    (
        r"swivel flood|weatherproof.*flood|floodlight",
        (
            "Lighting Fixtures, Control > Indoor Lighting",
        ),
        "Lighting Fixtures, Control > Outdoor Lighting",
    ),
    (
        r"\bwas\d{3}\b|\bseal,\s*ot\b",
        (
            "Fittings > Conduit Hubs",
        ),
        "Fittings > Bushing / K.O. Seal / Locknuts",
    ),
    (
        r"pipe choke|grounding hub|hub\d+l\b",
        (
            "Fittings > Conduit Hubs",
        ),
        "Connectors & Crimps > Grounding Products",
    ),
    (
        r"filterfan|filter fan|exhaust filter|pfa\d+|pf\s*\d{4,5}|flz-\d|finder.*filterfan",
        (
            "Boxes & Enclosures > Enclosures - Commercial Type",
            "Fans, Heaters > Fans & Fan Controls",
            "Fans, Heaters",
        ),
        "Boxes & Enclosures > Enclosure Accessories",
    ),
    (
        r"ø?22\.5mm|22\.5\s*mm|pb0[1-6]c|pb19c|pb20c|bs3dl|10250t|mp3-10|mp1-10|mpd4-11",
        (
            "Wiring Devices > Specialty Switches",
            "Wiring Devices",
            "Fire Alarm, Safety, Security > Pushbuttons & Switches",
            "Lighting Fixtures, Control > Outdoor Lighting",
        ),
        "Control & Automation > Pilot Devices",
    ),
    (
        r"downlight retrofit|retrofit.*downlight",
        (
            "Lighting Fixtures, Control > Indoor Lighting",
        ),
        "Lighting Fixtures, Control > Indoor Lighting - Recessed",
    ),
    (
        r"nm-b|romex|uf-b",
        (
            "Data & Telecom > Non-Metallic Raceway",
        ),
        "Wire, Cable & Cord > Building Wire",
    ),
    (
        r"^mc\s+mc-|\bmc-al\b|\bhcf-ac\b|\bmc-pcs\b|\bmc-stl\b|\bmcap\b|\bmetal clad\b",
        (
            "Fittings > Clips, Clamps, Hangers",
            "Data & Telecom > Non-Metallic Raceway",
            "Wire, Cable & Cord",
            "Wire, Cable & Cord > ",
        ),
        "Wire, Cable & Cord > Armored Cable",
    ),
    (
        r"^con emt\s+",
        (
            "Fittings > EMT Fittings",
        ),
        "Conduit, Strut & Raceways > Conduit - Metallic",
    ),
    (
        r"\b(?:3br\d{4}|chp32b|chp32l|brp\d{2}[bl]|brc\d{2}[bl]|loadcenter|load center)\b",
        (
            "Power Distribution > Circuit Breakers",
            "Boxes & Enclosures > Enclosures - Commercial Type",
            "Boxes & Enclosures > Boxes - Non-Metallic",
            "Lamps, Bulbs & Ballast > Bulbs Incandescent",
        ),
        "Power Distribution > Load Centers / Panelboards",
    ),
    (
        r"chpx\d|loadcenter cover|neutral flush cover",
        (
            "Boxes & Enclosures > Boxes - Non-Metallic",
            "Boxes & Enclosures > Boxes - Metallic",
        ),
        "Power Distribution > Load Centers / Panelboards",
    ),
    (
        r"brpsurge|brnsurge|clnsurge|chsurge",
        (
            "Control & Automation > Starters & Contactors",
        ),
        "Power Distribution > Circuit Breakers",
    ),
    (
        r"ezb20\d{2}|ez trim|ezt20\d{2}|gwpbq\d{4}|lugkit\d{3}",
        (
            "Boxes & Enclosures",
            "Boxes & Enclosures > ",
            "Boxes & Enclosures > Enclosures - Commercial Type",
            "Tools & Meters > Storage",
            "Power Distribution > Metering & Temporary Power",
        ),
        "Power Distribution > Load Centers / Panelboards",
    ),
    (
        r"carflex.*(?:100|50|coil)|liquidtight.*(?:100|50)\s*(?:coil|ft|')",
        (
            "Conduit, Strut & Raceways > Conduit - Non-Metallic",
            "Fittings > MC, NMB, Liquidtight, Flex Fittings",
        ),
        "Conduit, Strut & Raceways > Conduit - Flexible",
    ),
    (
        r"coupling|end bell|female adp|male adpt|junction box adapter|conduit clamp|elbow|elb\b",
        (
            "Conduit, Strut & Raceways > Conduit - Non-Metallic",
        ),
        "Fittings > PVC Fittings",
    ),
    (
        r"led wallpack|wall pack|crosstour|wpled|wlp\d{2}",
        (
            "Lighting Fixtures, Control > Occupancy Sensors",
            "Lighting Fixtures, Control > Temporary Lighting",
        ),
        "Lighting Fixtures, Control > Outdoor Lighting",
    ),
    (
        r"led disk light|disk light|wafer down|hlc6fs|slim downlight|commercial slim downlight|proled select slim downlight",
        (
            "Power Distribution > Metering & Temporary Power",
            "Lighting Fixtures, Control > Occupancy Sensors",
            "Lighting Fixtures, Control > Indoor Lighting - Recessed",
        ),
        "Lighting Fixtures, Control > Indoor Lighting",
    ),
    (
        r"lock out tag out|lockout.*tagout|\b44-97[01]\b",
        (
            "Fasteners & Hardware > Anchors",
        ),
        "Tools & Meters > Safety Equipment",
    ),
    (
        r"ct can|trans encl|ct cabinet|current transformer cabinet|csuct|u4468",
        (
            "Power Distribution > Transformers",
        ),
        "Boxes & Enclosures > Enclosures - Commercial Type",
    ),
    (
        r"rk5|rk1|class j|ajt\d|trs\d|tr\d+r|frsr|frnr|flsr|flnr|a4bq|a6d",
        (
            "Fuses & Accessories > General Purpose Fuses",
        ),
        "Fuses & Accessories > Power Fuses",
    ),
    (
        r"led t8|led t5|t8 led|t5 led|t8 hybrid|t8 linear tube|24t8|36t8|48t8|96t8",
        (
            "Lighting Fixtures, Control > Indoor Lighting",
        ),
        "Lamps, Bulbs & Ballast > Bulbs - LED",
    ),
    (
        r"c tap|c-tap|hytap|butt splice|compression splice|ygfc|yghc|yc[0-9]c|ys[0-9]",
        (
            "Fittings > EMT Fittings",
        ),
        "Connectors & Crimps > Lugs & Split Bolts",
    ),
    (
        r"ground screw|grounding screw|grn ground|grounding clips?",
        (
            "Fasteners & Hardware > Screws",
        ),
        "Connectors & Crimps > Grounding Products",
    ),
    (
        r"box extender|\bbe[1-4]r?\b|spacer shims|receptacle shim",
        (
            "Boxes & Enclosures > Boxes - Metallic",
            "Wiring Devices > Wallplates",
            "Wiring Devices > Combination Devices",
        ),
        "Boxes & Enclosures > Boxes - Non-Metallic",
    ),
    (
        r"modular crimp|vdv226|punchdown|punch down|scout.*tester|vdv\d{3}",
        (
            "Tools & Meters > Hand Tools",
            "Tools & Meters > Datacomm Tools",
        ),
        "Data & Telecom > Tools",
    ),
    (
        r"low voltage.*(?:bracket|mounting plate|mounting frame|device)|\blv[1-4]\b|\blvn[1-4]\b",
        (
            "Data & Telecom > Low Voltage Cable",
            "Boxes & Enclosures > Boxes - Non-Metallic",
        ),
        "Caddy > Datacom/Low Voltage",
    ),
    (
        r"access control.*cable|composite.*cable",
        ("Data & Telecom > Workstation",),
        "Data & Telecom > Low Voltage Cable",
    ),
    (
        r"\b(?:24t8|36t8|48t8|96t8)\b|\bt8.*(?:hybrid|bypass|duoconnect|linear tube|led lamp)\b",
        (
            "Lamps, Bulbs & Ballast > Bulbs - LED > LED - Traditional > Lamps - A-type",
            "Lamps, Bulbs & Ballast > Bulbs - LED",
            "Lighting Fixtures, Control > Indoor Lighting > LED",
            "Lighting Fixtures, Control > Indoor Lighting",
        ),
        "Lamps, Bulbs & Ballast > Bulbs - LED > LED - Tube Type",
    ),
    (
        r"s384-c|range/dryer plate|flush range dryer plate|tp703|ss703|p703w",
        (
            "Boxes & Enclosures > Boxes - Non-Metallic",
        ),
        "Wiring Devices > Wallplates",
    ),
    (
        r"lever[- ]nut|wago.*221|\b221[- ](?:41[235]|61[235]|2401)",
        (
            "Control & Automation > Terminal Blocks",
            "Wiring Devices > Terminal Blocks & Connectors",
        ),
        "Connectors & Crimps > Wire Connectors",
    ),
    (
        r"weatherhead|service entrance cap|service cap",
        ("Wire, Cable & Cord > Service Wire", "Fittings > PVC Fittings"),
        "Fittings > Flashings, Weatherhead",
    ),
    (
        r"glass.*fuse|fuse.*glass|\bagc[- ]?\d|\bagx\b|\bagc\b|\bmdl\b",
        ("Tools & Meters > Paint, Chemicals, Cleaning Supplies",),
        "Fuses & Accessories > General Purpose Fuses",
    ),
    (
        r"blank.*(?:wall\s*plate|wallplate|box mount|switch plate)|(?:wall\s*plate|wallplate).*blank",
        ("Data & Telecom > Audio Video Equipment", "Lighting Fixtures, Control > Occupancy Sensors"),
        "Wiring Devices > Wallplates",
    ),
    (
        r"photocontrol|photo control|photocell",
        (
            "Caddy > Ceiling/Acoustical",
            "Data & Telecom > Cable Tray",
            "Data & Telecom > Audio Video Equipment",
            "Boxes & Enclosures > Enclosures - Commercial Type",
            "Lamps, Bulbs & Ballast > Bulbs - Fluorescent",
        ),
        "Lighting Fixtures, Control > Photocells",
    ),
    (
        r"squeeze connector|\bsqz conn\b|flexible.*connector|connector.*flexible",
        ("Conduit, Strut & Raceways > Conduit - Flexible",),
        "Fittings > MC, NMB, Liquidtight, Flex Fittings",
    ),
    (
        r"warning sign|danger.*sign|pressure sensitive.*sign|voltage.*sign|marking sign",
        ("Lighting Fixtures, Control > Emergency Lighting & Signs",),
        "Tools & Meters > Marking & Labeling",
    ),
    (
        r"\b(?:lamp|bulb|t8|t5|t5ho|led tube|led lamp|omni-directional|g24d?)\b",
        ("Data & Telecom > Workstation", "Data & Telecom > Low Voltage Cable"),
        "Lamps, Bulbs & Ballast > Bulbs - LED",
    ),
    (
        r"gigabit.*switch|poe.*switch|network.*switch|greennet.*switch|managed.*switch|unmanaged.*switch",
        ("Wiring Devices > Switches",),
        "Data & Telecom > Distribution Devices",
    ),
    (
        r"hard drive|purple.*drive|\bhdd\b|sata.*drive",
        ("Tools & Meters > Storage",),
        "Data & Telecom > Video & Surveillance Equipment",
    ),
    (
        r"\bgbk\d{1,2}\b|ground bar kit|grounding bar kit",
        ("Fasteners & Hardware > Anchors", "Fasteners & Hardware > Hardware"),
        "Power Distribution > Load Centers / Panelboards",
    ),
    (
        r"brnsurge|brpsurge|clnsurge|chsurge",
        ("Power Distribution > Power Quality/Protection",),
        "Power Distribution > Circuit Breakers",
    ),
    (
        r"minuteman|stand-by ups|online ups|line interactive ups",
        ("Power Distribution > Power Quality/Protection",),
        "Data & Telecom > Cabinets, Racks & Wire Management",
    ),
    (
        r"rac\s*72[24]|rac\s*73[17]|oct(?:agon)?\s*(?:box\s*)?cover|4o\s*(?:flat\s*)?cover",
        ("Boxes & Enclosures > Boxes - Weatherproof",),
        "Boxes & Enclosures > Mud Rings / Covers",
    ),
    (
        r"\b(?:nec|ugly'?s)\b.*(?:code\s*book|tabs|handbook|reference)|code\s*book",
        (
            "Connectors & Crimps > Welded Connections",
            "Data & Telecom > Cabinets, Racks & Wire Management",
            "Data & Telecom > Low Voltage Cable",
            "Fasteners & Hardware > ",
        ),
        "Tools & Meters > Books",
    ),
    (
        r"hammer drill|impact driver|drill/driver|cordless drill|combo kit|m18 fuel.*kit",
        ("Data & Telecom > Tools", "Data & Telecom > Workstation"),
        "Tools & Meters > Cordless Tools",
    ),
    (
        r"packout.*(?:chest|box|tray|organizer)|tool tray|tool chest",
        ("Data & Telecom > Tools", "Data & Telecom > Workstation"),
        "Tools & Meters > Storage",
    ),
    (
        r"driller'?s dust bowl",
        ("Data & Telecom > Cabinets, Racks & Wire Management",),
        "Tools & Meters > Bits, Blades, Punches, Holesaws",
    ),
    (
        r"angler.*fish tape|pulling fish tape",
        ("Data & Telecom > Tools",),
        "Tools & Meters > Cable Pulling & Hoist",
    ),
    (
        r"transducer|pressure sensor",
        ("Lamps, Bulbs & Ballast > Ballast -HID", "Lamps, Bulbs & Ballast > Ballast - Fluorescent"),
        "Control & Automation > Drives",
    ),
    (
        r"\bvfd\b|variable frequency drive",
        ("Power Distribution > Load Centers / Panelboards",),
        "Control & Automation > Drives",
    ),
    (
        r"socket adapter|hex socket adapter|drive guide|bit tip holder",
        ("Wiring Devices > Switches", "Wiring Devices"),
        "Tools & Meters > Bits, Blades, Punches, Holesaws",
    ),
    (
        r"\byghp\w*|hyground|c-tap|grounding c-tap",
        ("Fittings > EMT Fittings", "Fittings"),
        "Connectors & Crimps > Grounding Products",
    ),
    (
        r"\b(?:uny|unf|eyk|eys|eaf|gun|unl)\d*|conduit union\b",
        ("Fittings > NM Cable Fittings", "Fittings > EMT Fittings"),
        "Fittings > Explosionproof Fittings",
    ),
    (
        r"wp34\d{2}\b|keystone.*wall\s*plate|\b(?:1|2|3|4|6)\s*port\s*(?:wall\s*plate|faceplate|plate)\b",
        ("Wiring Devices > Wallplates",),
        "Data & Telecom > Workstation",
    ),
    (
        r"fishing pole|tuff-rod|glow rod|fish rod|wire fishing",
        ("Caddy > Cable/Conduit", "Caddy > Datacom/Low Voltage"),
        "Tools & Meters > Cable Pulling & Hoist",
    ),
    (
        r"\bwie\b.*(?:cont hinge|screw cvr|rsc\d|sc\d|b\d{6})\b|continuous hinge.*(?:enclosure|box)",
        ("Data & Telecom > Audio Video Equipment", "Data & Telecom > Cabinets, Racks & Wire Management"),
        "Boxes & Enclosures > Enclosures - Commercial Type",
    ),
    (
        r"\bezt\d{4}\w*|\bezb\d{4}\w*|pow-r-(?:stock|xpress).*(?:trim|door|box)",
        ("Boxes & Enclosures > Enclosure Accessories", "Boxes & Enclosures > Enclosures - Commercial Type"),
        "Power Distribution > Load Centers / Panelboards",
    ),
    (
        r"quick set cement|pvc cement|cwc\d{2}|solvent cement",
        ("Caddy > Cable/Conduit", "Caddy"),
        "Tools & Meters > Paint, Chemicals, Cleaning Supplies",
    ),
    (
        r"rac\s*902c|rac\s*808c|rac\s*810c|rac\s*811c|cc cover 4sq",
        ("Boxes & Enclosures > Boxes - Weatherproof",),
        "Boxes & Enclosures > Mud Rings / Covers",
    ),
    (
        r"\bcar\s+b\d{3}[a-z]*\b|carlon.*box|\ball\s+p-\d{3}[a-z]*\b|allied moulded|non[- ]metallic.*box",
        ("Boxes & Enclosures > Boxes - Metallic",),
        "Boxes & Enclosures > Boxes - Non-Metallic",
    ),
    (
        r"strip.*(?:wired for directdrive|directdrive|type b)|kt-ddsledt8",
        ("Lamps, Bulbs & Ballast > Bulbs - LED", "Lamps, Bulbs & Ballast > Bulbs - LED > LED - Tube Type"),
        "Lighting Fixtures, Control > Indoor Lighting",
    ),
    (
        r"^arl\s+fr420f\b",
        ("Control & Automation > Terminal Blocks",),
        "Boxes & Enclosures > Boxes - Weatherproof",
    ),
    (
        r"\b66 block\b",
        ("Control & Automation > Terminal Blocks",),
        "Data & Telecom > Cross Connectors",
    ),
    (
        r"\bvinyl spade\b|\bspade terminal\b",
        ("Control & Automation > Terminal Blocks",),
        "Connectors & Crimps > Small Terminals, Forks, Rings",
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
TOP_ANCHORS = 4  # best sellers always offered; the rest rotate
SPREAD_FACTOR = 10
MIN_SUGGESTIONS = 10
FALLBACK_FILL = 12


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
    if os.path.exists(path):
        tax_df = pd.read_csv(path, dtype=str, keep_default_na=False)
        if ID_COL in tax_df.columns:
            tax_indexed = tax_df.drop_duplicates(subset=ID_COL, keep="first").set_index(ID_COL)
            if "Subcategory 2" in tax_indexed.columns:
                source = source.assign(tax_sub2=source[ID_COL].map(tax_indexed["Subcategory 2"]).fillna(""))
            if "Subcategory 3" in tax_indexed.columns:
                source = source.assign(tax_sub3=source[ID_COL].map(tax_indexed["Subcategory 3"]).fillna(""))
    return source


def _taxonomy_paths(source: pd.DataFrame, path: str) -> pd.Series:
    paths = _raw_taxonomy_paths(source, path)
    text = (source["PRODUCT"] + " " + source["clean_title"]).str.lower()
    for pattern, from_nodes, to_node in TAXONOMY_OVERRIDES:
        eligible = paths.isin(from_nodes) if from_nodes else pd.Series(True, index=paths.index)
        mask = eligible & text.str.contains(pattern, regex=True)
        paths = paths.mask(mask, " > ".join(part.strip() for part in to_node.split(">")[:2]))
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
    if re.search(r"\b(?:red|rd)\b", text):
        return "red"
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
    ("DIN", re.compile(r"\bL9-\d|\bMINICB\b|\bST20\d\b|DIN.?RAIL", re.I)),
    ("HOM", re.compile(r"\bHOM[A-Z]*\d*\b|Homeline", re.I)),
    ("QO", re.compile(r"\bQO[A-Z]{0,3}\d*\b")),
    ("THQL", re.compile(r"\bTHQL[A-Z]*\d*\b")),
    ("THQB", re.compile(r"\bTH?H?QB[A-Z]*\d*\b|\bTHQC\w*", re.I)),
    ("QP", re.compile(r"\bQP\w*|\bQ\d{2,3}\w*|Siemens Q\b")),
    ("BAB", re.compile(r"\bBAB\w*|\bBQD\w*|\bQPF\w*|\bBQ\w*|PRL1|PRL2", re.I)),
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


_THREAD = re.compile(
    r"#\s?(\d{1,2})\b|\b(\d{1,2}/\d{1,2})\b|\b(\d{1,2})-(?:32|24|20|16|13|11|10)\b",
    re.I,
)


def box_kind(row: dict[str, str]) -> str:
    text = _text(row).lower()
    if re.search(r"fan (?:and )?fixture|fan box|ceiling fan box|saddle box|fbrs|fbs4|fb4\d\d", text):
        return "fan_box"
    if re.search(r"4-11/16|4\.6875|4 11/16", text):
        return "4-11/16"
    if re.search(r"\bsquare\b.*\b4\b|\b4\b.*\bsquare\b|\b4(?:-| )?(?:inch|in)\b.*square|\b4s\b|\b4 ?sq\b", text):
        return "4sq"
    if re.search(r"\b(?:round|rnd)\b|ceiling box|pancake|fixture (?:mounting )?box|\b3-1/2\b.*\bbox\b|\b4-inch round\b", text) and not re.search(r"floor box", text):
        return "round_box"
    if re.search(r"\boct|\b[34]o(?:box|ext)?\b", text):
        return "oct3" if re.search(r"3-1/2|\b3o|\b30\b", text) else "oct4"
    if re.search(r"\b(?:4|four)[- ]gang|\b4g\b", text):
        return "4g"
    if re.search(r"\b(?:3|three)[- ]gang|\b3g\b", text):
        return "3g"
    if re.search(r"\b(?:2|two)[- ]gang|\b2g\b", text):
        return "2g"
    if re.search(r"single[- ]gang|\b1[- ]gang|\b1g\b|handy|switch box", text):
        return "1g"
    return ""


def fastener_diameter(row: dict[str, str]) -> str:
    match = _THREAD.search(f"{row.get('clean_title', '')} {row.get('PRODUCT', '')}")
    if not match:
        return ""
    if match.group(1):
        return "#" + match.group(1)
    if match.group(2):
        return match.group(2)
    return "#" + match.group(3)


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


_WIRE_TYPE_GAUGE = re.compile(
    r"\b(?:thhn|thwn(?:-2)?|xhhw(?:-2)?|use-2|rhw(?:-2)?|coilpak)\s*[- ]\s*(\d{1,2}(?:/0)?)\b", re.I
)
_NM_GAUGE = re.compile(r"\b(\d{1,2})/[2-5]\b")
_CABLE_WORDS = re.compile(r"sheathed|nm-b|romex|metal clad|\bmc\b|\buf\b|underground feeder", re.I)


def gauge_range(row: dict[str, str]) -> tuple[float, float] | None:
    values: list[float] = []
    for match in _GAUGE.finditer(_text(row) + " " + str(row.get("normalized_specs", ""))):
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
        bare = _WIRE_TYPE_GAUGE.search(_text(row))
        if bare:
            area = _gauge_area(bare.group(1), "")
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


def fuse_frame_amperage(amp: int) -> int:
    if amp <= 30:
        return 30
    elif amp <= 60:
        return 60
    elif amp <= 100:
        return 100
    elif amp <= 200:
        return 200
    elif amp <= 400:
        return 400
    elif amp <= 600:
        return 600
    return amp


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
        t_amps = amp_ratings(target)
        c_amps = amp_ratings(candidate)
        if not t_amps or not c_amps:
            return False
        if t_amps & c_amps:
            return True
        t_text = _text(target).lower()
        c_text = _text(candidate).lower()
        t_is_fuse = "fuse" in t_text and not any(k in t_text for k in ("block", "holder", "reducer", "puller", "clip", "switch", "disconnect"))
        c_is_fuse = "fuse" in c_text and not any(k in c_text for k in ("block", "holder", "reducer", "puller", "clip", "switch", "disconnect"))
        if t_is_fuse and c_amps:
            frames = {fuse_frame_amperage(a) for a in t_amps}
            if frames & c_amps:
                return True
        if c_is_fuse and t_amps:
            frames = {fuse_frame_amperage(a) for a in c_amps}
            if frames & t_amps:
                return True
        return False
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
        if (tax_path == rule.target_path or tax_path.startswith(rule.target_path + " >"))
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
    "Fuses & Accessories > ",
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
            c_tax = candidate.get(TAX_COL, "")
            if c_tax == rule.accessory_path or c_tax.startswith(rule.accessory_path + " >"):
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
    match = re.search(r"\b([1-9])[- ]?(?:g\b|gang|dvc\b|device\b|dev\b)", text)
    if match:
        return int(match.group(1))
    for word, number in _GANG_WORDS:
        if re.search(rf"\b{word}[- ](?:gang|device|dvc)", text):
            return number
    # Handy boxes and standard 3x2 switch boxes are 1-gang
    if re.search(r"\b(?:handy box|switch box 3\s*x\s*2|3\s*x\s*2\s*switch box)\b", text):
        return 1
    # Standard wiring devices (receptacles, switches, dimmers, GFCIs) are 1-gang unless specified
    tax = str(row.get(TAX_COL, ""))
    if "Wiring Devices" in tax:
        if any(w in text for w in ["duplex", "receptacle", "recp", "toggle", "switch", "decora", "decorator", "dimmer", "outlet", "gfci"]):
            return 1
    return 0


def device_cutout(row: dict[str, str]) -> str:
    text = (str(row.get("clean_title", "")) + " " + str(row.get("PRODUCT", ""))).lower()
    tax = str(row.get(TAX_COL, ""))
    is_wallplate = "Wallplates" in tax or "wallplate" in text or "wall plate" in text or box_component(row).endswith("cover")
    is_device = any(k in tax for k in ["Receptacles", "Switches", "GFCI", "Combination Devices", "Specialty Switches", "Straight Blade 10-20 Amp"]) or any(k in text for k in ["receptacle", "switch", "outlet", "dimmer"]) or bool(re.search(r"\bs2-lf(?:sq)?\b", text))

    if not (is_wallplate or is_device):
        return ""

    if is_wallplate and len(plate_openings(row)) > 1:
        return "combo"
    if re.search(r"\b(?:tp703|ss703|ss701|s384|2\.15|range/dryer plate|dryer plate|power outlet plate)\b", text):
        return "dryer_round"
    if re.search(r"\b(?:tp126|tp826|tp226|combo|combination)\b", text):
        return "combo"
    if re.search(r"\b(?:blank|tp13|p13|tp23|ss23|p23|tp33|p33|tp43)\b", text):
        return "blank"
    if re.search(r"\b(?:decora|decorator|rocker|gfci|gfi|paddle|slide dimmer|dvcl|dvstv|rhl153p\w*|s2-lf(?:sq)?|tp26|p26|2604)\b", text):
        return "decora"
    if not is_wallplate and re.search(r"\b(?:pns|pass\s*(?:&|and)\s*seymour)\s+66[014][- ]?wg\b", text):
        return "toggle"
    if re.search(r"\b(?:toggle|tp[1-6]\b|p[1-6]\b|ss[1-6]\b|1201|1221|cs120|csb120|cs115)\b", text):
        return "toggle"
    if re.search(r"\b(?:single receptacle|single recp|tp7\b|p7\b|1\.406|1\.59\d*)\b", text) and not re.search(r"\bduplex\b", text):
        return "single_round"
    if re.search(r"\b(?:duplex|dplx|tp8\b|p8\b|tp82\b|p82\b|3232|5362|5262|cr20|cr15|br20|br15|8300)\b", text):
        return "duplex"
    return ""


def _path_parts(row: dict[str, str]) -> list[str]:
    return [part.strip() for part in str(row.get(TAX_COL, "")).split(">")]


def plate_openings(row: dict[str, str]) -> tuple[tuple[str, int], ...]:
    text = _text(row).lower()
    openings = []
    for kind, pattern in (
        ("toggle", r"(?:toggle|tog)"),
        ("duplex", r"(?:duplex|dplx)"),
        ("decora", r"(?:decora|decorator|decor|gfci|gfi)"),
        ("blank", r"blank"),
        ("single_round", r"(?:single receptacle|single recp)"),
    ):
        if re.search(rf"\b{pattern}\b", text):
            counts = re.findall(rf"(?<![\w/.])([1-6])\s*(?:g\s*)?{pattern}\b", text)
            openings.append((kind, max(map(int, counts)) if counts else 1))
    return tuple(openings)


def conductor_role(row: dict[str, str]) -> str:
    color = _color(row)
    if color in {"green", "white", "gray"}:
        return "ground" if color == "green" else "neutral"
    return "ungrounded" if color else ""


def box_component(row: dict[str, str]) -> str:
    text = _text(row).lower()
    if re.search(r"\b(?:extension|extender|extns|ext|spacer|shim)\b|\bbe[1-4]r?\b", text):
        return "extension"
    if re.search(r"box mount support|box support|box bracket|stud bracket|far[- ]side support", text):
        return "support"
    if re.search(r"\b(?:cover|cvr|wallplate|wall plate|lid|mud ring|plaster ring)\b", text) and not re.search(
        r"(?:box|enclosure|body).*(?:with|w/|screw).*(?:cover|cvr)|screw cover.*(?:box|enclosure)", text
    ):
        if re.search(r"\bblank\b", text):
            return "blank_cover"
        if re.search(r"in[- ]?use|while.in.use", text):
            return "in_use_cover"
        return "cover"
    return "box"


_KO_NUMBER = r"(?:\d+-\d+/\d+|\d+/\d+|\d+(?:\.\d+)?)"
_KO_SIZE = rf"{_KO_NUMBER}\s*(?:inch(?:es)?|in\b|\")?(?:\s*\([^)]*\))?"
_KNOCKOUTS = re.compile(
    rf"(?<![\w/.-])({_KO_SIZE}(?:\s*(?:&|and|,)\s*{_KO_SIZE})*)\s*(?:knockouts?|\bt?ko\b|k\.o\.)", re.I
)


def knockout_sizes(row: dict[str, str]) -> list[float]:
    text = " ".join(str(row.get(k, "")) for k in ("normalized_specs", "clean_title", "PRODUCT"))
    sizes = []
    for match in _KNOCKOUTS.finditer(text):
        sizes.extend(_parse_size(token) for token in re.findall(_KO_NUMBER, match.group(1)))
    return list(dict.fromkeys(sizes))


def cable_family(row: dict[str, str]) -> str:
    text = _text(row).lower()
    for family, pattern in (
        ("nm", r"\bnm(?:-b)?\b|\bromex\b|non[- ]metallic (?:sheathed|cable)"),
        ("hcf_ac", r"hcf[- ]?ac|health.?care.*\bac\b"),
        ("hcf_mc", r"hcf[- ]?mc|health.?care.*\bmc\b"),
        ("mc_pcs", r"mc[- ]?pcs|power.*control.*cable"),
        ("lfnc", r"\blfnc\b|\bnmlt\d|carflex|non[- ]metallic.*liquidtight|liquidtight.*non[- ]metallic"),
        ("lfmc", r"\blfmc\b|liquidtight.*metallic|metallic.*liquidtight"),
        ("ac_mc", r"\b38ast\b|\bac/mc\b"),
        ("mc", r"\bmc\b|metal[- ]?clad"),
        ("ac", r"\bac[- ]cable\b|armored cable|\bbx\b"),
        ("fmc", r"\bfmc\b|greenfield|sqz.*flex|squeeze.*flex|flexible metal conduit"),
        ("thhn", r"\bthhn\b|\bthwn(?:-2)?\b"),
    ):
        if re.search(pattern, text, re.I):
            return family
    return ""


def bushing_kind(row: dict[str, str]) -> str:
    text = _text(row).lower()
    raw = str(row.get("PRODUCT", "")).lower()
    if re.search(r"\bhub\b", raw):
        return "hub"
    if raw.startswith("wmd "):
        return "raceway"
    for kind, pattern in (
        ("locknut", r"lock ?nut"),
        ("grounding", r"ground.*bushing|\bgrnd\b.*bushing|bushing.*(?:ground|lug)|lay[- ]?in lug"),
        ("reducing", r"reduc.*bushing"),
        ("knockout", r"(?:knock[- ]?out|\bko\b|\bk\.o\.)\s*bushing"),
        ("emt", r"\bemt\d*\b.*bushing|bushing.*\bemt\b"),
        ("split", r"split.*bushing"),
        ("threaded", r"bushing"),
        ("seal", r"knockout seal|\bko seal\b"),
    ):
        if re.search(pattern, text):
            return kind
    return ""


def is_ground_rod_clamp(row: dict[str, str]) -> bool:
    return bool(re.search(r"\b(?:ground|grd)\s+rod\b.*\bclamp\b", _text(row), re.I))


def is_ground_rod(row: dict[str, str]) -> bool:
    text = _text(row)
    return bool(re.search(r"\b(?:ground|grd|grounding)\s+rod\b", text, re.I)) and not bool(
        re.search(r"\b(?:clamp|driver|coupler|mold)\b", text, re.I)
    )


def rod_sizes_compatible(target: dict[str, str], candidate: dict[str, str]) -> bool:
    def diameters(row: dict[str, str]) -> list[float]:
        return trade_sizes(row) or [
            _parse_size(token) for token in re.findall(r"(?<![\w/.-])\d+/\d+(?![\w/.-])", _text(row))
        ]

    t_sizes, c_sizes = diameters(target), diameters(candidate)
    return bool(t_sizes and c_sizes and t_sizes[0] in c_sizes)


def is_hvac_disconnect(row: dict[str, str]) -> bool:
    text = _text(row)
    if re.search(r"\bhvac\b|air condition|\bu(?:065|38)\w*\b", text, re.I):
        return True
    return bool(re.search(r"\bac disconnect\b", text, re.I)) and not bool(
        re.search(r"\bmotor\b|\b3[- ]?(?:pole|phase|ph)\b|\b(?:480|600)\s*v", text, re.I)
    )


_CONDUIT_MAINS = frozenset({"Conduit, Strut & Raceways", "Fittings"})
_MATERIALS = (
    ("pvc", re.compile(r"\bpvc\b|sch(?:edule)? ?(?:40|80)|kraloy", re.I)),
    ("liquidtight", re.compile(r"liquid|lqt|sealtight|\blt\b|\bnmlt|lfmc", re.I)),
    ("dry_flex", re.compile(r"\b(?:fmc|greenfield|reduced wall flex|steel flex|aluminum flex|sqz flex|screw in flex)\b|\bflx\b", re.I)),
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
    text = _text(row).lower()
    found = set()
    if re.search(r"\bpvc\b|sch(?:edule)? ?(?:40|80)|kraloy", text):
        found.add("pvc")
    if re.search(r"liquid|lqt|sealtight|\blt\b|\bnmlt|lfmc", text):
        found.add("liquidtight")
    if re.search(r"\b(?:fmc|greenfield|reduced wall flex|steel flex|aluminum flex|sqz.*flex|screw in.*flex|flx)\b", text) and not re.search(r"liquid|lqt|sealtight|\bnmlt\b", text):
        found.add("dry_flex")
    if re.search(r"\bemt\b", text):
        found.add("emt")
    if re.search(r"\brgd\b|rigid|\bgrc\b|\brmc\b|\bimc\b|\brgs\d", text):
        found.add("rigid")
    if not found:
        node = _path_parts(row)[1:2]
        if node and node[0] == "EMT Fittings":
            found = {"emt"}
        elif node and node[0] == "Rigid Fittings":
            found = {"rigid"}
        elif node and node[0] == "PVC Fittings":
            found = {"pvc"}
    return found


def fuse_class(row: dict[str, str]) -> str:
    text = _text(row).lower()
    if re.search(r"\bclass[- ]?j\b|\bajt\b|\bjdl\b|\bjks\b|\blpj\b", text):
        return "class_j"
    if re.search(r"\bclass[- ]?cc\b|\batdr\b|\batqr\b|\bccmr\b|\bfnq-r\b|\bktk-r\b|\blp-cc\b", text):
        return "class_cc"
    if re.search(r"\bclass[- ]?r(?:k[15])?\b|\brk[15]\b|\btrs\b|\btr\d+r\b|\bfrsr\b|\bfrnr\b|\bflsr\b|\bflnr\b|\blpn-rk\b|\blps-rk\b", text):
        return "class_r"
    if re.search(r"\bclass[- ]?[hk]\b|\bnos\b|\bnon\b|\brkn\b", text):
        return "class_hk"
    if re.search(r"\bmidget\b|\b10x38\b|13/32|\bktk\b|\bfnq\b|\bbln\b|\bblf\b|\bflq\b|\bflm\b|\bklk\b|\bheb\b", text):
        return "midget"
    if re.search(r"plug fuse|box cover unit|type s\b|type t\b|type w\b|type sl\b|type tl\b|\bsou\b|\bsof\b|\bsox\b|\bsog\b", text):
        return "plug"
    return ""


_GAUGE_MAINS = frozenset({"Wire, Cable & Cord", "Connectors & Crimps"})


def hard_conflict(target: dict[str, str], cand: dict[str, str]) -> str:
    """Return 'gang', 'color', 'series', 'box', 'gauge', 'material', 'size' or 'part' when a pair can never go together, else ''."""
    tparts, cparts = _path_parts(target), _path_parts(cand)
    target_text, candidate_text = _text(target), _text(cand)
    if (
        (tparts[0] == "Boxes & Enclosures" or "Wallplates" in str(target.get(TAX_COL, "")))
        and (cparts[0] == "Boxes & Enclosures" or "Wallplates" in str(cand.get(TAX_COL, "")))
        and box_component(target).endswith("cover")
        and box_component(cand).endswith("cover")
    ):
        return "cover_substitute"
    for box, connector in ((target, cand), (cand, target)):
        if "Boxes & Enclosures" in str(box.get(TAX_COL, "")) and box_component(box) == "box" and re.search(
            r"\b(?:connector|conn)\b", _text(connector), re.I
        ):
            openings = knockout_sizes(box)
            entries = knockout_sizes(connector) or trade_sizes(connector)[:1]
            if openings and entries and not set(openings) & set(entries):
                return "knockout_size"
    if (
        "Boxes & Enclosures" in str(target.get(TAX_COL, ""))
        and box_kind(target) in {"4sq", "4-11/16"}
        and re.search(r"\b(?:rhms|fhms|machine screw)\b", candidate_text, re.I)
    ):
        threads = re.findall(r"\b(?:\d{1,2}|\d+/\d+)-(?:32|24|20|16|13|11|10)\b", candidate_text)
        if not threads or any(thread != "8-32" for thread in threads):
            return "cover_thread"
    if is_ground_rod(target) and re.search(r"\bclamp\b", candidate_text, re.I):
        if not is_ground_rod_clamp(cand):
            return "rod_clamp"
        if not rod_sizes_compatible(target, cand):
            return "rod_diameter"
    if is_ground_rod_clamp(target) and is_ground_rod_clamp(cand):
        return "clamp_substitute"
    if re.search(r"\bh-164(?:-|\b)", target_text, re.I) and re.search(r"\bn-82\d\b", candidate_text, re.I):
        return "strut_spring"
    if (
        "Bushing / K.O. Seal / Locknuts" in str(target.get(TAX_COL, ""))
        and "bushing" in target_text.lower()
        and "bushing" in candidate_text.lower()
    ):
        return "bushing_substitute"
    if bushing_kind(target) == "grounding" and cparts[0] == "Wire, Cable & Cord":
        if "Bare Copper" not in str(cand.get(TAX_COL, "")) and conductor_role(cand) != "ground":
            return "bonding_wire"
        wanted, have = gauge_range(target), gauge_range(cand)
        if not wanted or not have or not wanted[0] <= have[0] <= have[1] <= wanted[1]:
            return "gauge"
    t_family, c_family = cable_family(target), cable_family(cand)
    if (
        (t_family == "nm" and c_family in {"ac", "ac_mc", "mc", "fmc", "lfnc", "lfmc"})
        or (c_family == "nm" and t_family in {"ac", "ac_mc", "mc", "fmc", "lfnc", "lfmc"})
    ):
        return "cable_type"
    if "Conduit Bodies" in str(target.get(TAX_COL, "")) and re.search(
        r"\bthreaded\b|\bbody complete\b", target_text + " " + str(target.get("normalized_specs", "")), re.I
    ) and conduit_materials(cand) == {"emt"} and re.search(r"Conduit - ", str(cand.get(TAX_COL, ""))):
        return "threaded_transition"
    if (
        re.search(r"\b(?:omt|oscillat\w*|multi-tool blade|open-lok|starlock)\b", target_text, re.I)
        and re.search(r"\b(?:sawzall|hackzall|reciprocating)\b", candidate_text, re.I)
    ):
        return "tool_mismatch"
    if re.search(r"\bflbc8500\b", target_text, re.I):
        if not re.search(r"\bflbc85(?:10|20)\w*\b", candidate_text, re.I):
            return "unverified_floor_box_accessory"
    if re.search(r"\bl11-tl3\b", target_text, re.I):
        if not re.search(r"\bl11-(?:j400|nu400)-1753\b", candidate_text, re.I):
            return "unverified_lug_accessory"
    if re.search(r"\bca7-td855\b.*\brenewal coil\b", target_text, re.I):
        if not (re.search(r"\bca7-(?:43|55)-", candidate_text, re.I)
                and re.search(r"\b24\s*(?:v|volt)\b", candidate_text, re.I)):
            return "unverified_coil_accessory"
    if re.search(r"\bs2-lf(?:sq)?\b", target_text, re.I):
        if "Fans & Fan Controls" in str(cand.get(TAX_COL, "")):
            return "unverified_fan_control_accessory"
        if "Boxes & Enclosures" in str(cand.get(TAX_COL, "")) and box_kind(cand) not in {"1g", "2g"}:
            return "fan_control_box"
        if re.search(r"\bpulling\b|\bclearglide\b", candidate_text, re.I):
            return "unrelated"
        if str(cand.get(ID_COL, "")) in {"1021", "1026"}:
            return "unverified_control_cable"
    if re.search(r"\bbibs2/03\b", target_text, re.I) and re.search(r"\bnm-b\b", candidate_text, re.I):
        return "unverified_connector_cable"
    if re.search(r"\bbibs2/03\b", target_text, re.I) and cparts[0] == "Connectors & Crimps":
        return "connector_substitute"
    if (
        tparts[0] == "Fasteners & Hardware"
        and "Screws" in tparts[1:2]
        and re.search(r"\b(?:machine screw|rhms|fhms)\b", target_text, re.I)
    ):
        thread = re.search(r"\b(\d{1,2}|\d+/\d+)-(?:32|24|20|16|13|11|10)\b", target_text, re.I)
        if thread:
            is_washer = "Nuts, Bolts, Washers" in cparts[1:2] and bool(re.search(r"\b(?:washer|lockwasher)\b", candidate_text, re.I))
            is_nut = "Nuts, Bolts, Washers" in cparts[1:2] and bool(re.search(r"\b(?:hex )?nut\b", candidate_text, re.I))
            matching_nut = is_nut and bool(re.search(rf"\b{re.escape(thread.group(0))}\b", candidate_text, re.I))
            matching_washer = is_washer and fastener_diameter(target) == fastener_diameter(cand)
            if not (matching_nut or matching_washer):
                return "unverified_fastener_accessory"
    if re.search(r"\bhalco\s+8720[0-5]\b", target_text, re.I):
        if cparts[0] == "Boxes & Enclosures" or re.search(r"\bpulling\b|\bclearglide\b", candidate_text, re.I):
            return "unrelated"
        if "Wire Connectors" in str(cand.get(TAX_COL, "")):
            conductor_range = gauge_range({"PRODUCT": candidate_text + " " + str(cand.get("normalized_specs", ""))})
            twelve_awg = _gauge_area("12", "AWG")
            if not conductor_range or twelve_awg is None or not conductor_range[0] <= twelve_awg <= conductor_range[1]:
                return "unverified_tube_connector"
        if cparts[0] == "Wire, Cable & Cord" and not re.search(
            r"\bthhn-(?:12|14)-(?:black|white|green)-(?:sol|solid)\b", candidate_text, re.I
        ):
            return "unverified_tube_wire"
        if "Drivers" in str(cand.get(TAX_COL, "")):
            return "type_b_driver"
        if "Lampholders" in str(cand.get(TAX_COL, "")) and not re.search(
            r"\bnon[- ]shunt", candidate_text + " " + str(cand.get("normalized_specs", "")), re.I
        ):
            return "unverified_lampholder"
        if "Lighting Fixtures" in cparts[0] and "Lampholders" not in str(cand.get(TAX_COL, "")):
            if not re.search(r"kt-ddsledt8-4-|wired for directdrive.*(?:4'|48)", candidate_text, re.I):
                return "unverified_tube_fixture"
        if "Bulbs - LED" in str(cand.get(TAX_COL, "")) and not re.search(r"\bnon[- ]shunt", candidate_text, re.I):
            return "lamp_substitute"
    allowed = ONLY_WITH.get(" > ".join(tparts[:2]))
    if allowed and " > ".join(cparts[:2]) not in allowed:
        return "unrelated"
    if tparts[0] == cparts[0] == "Power Distribution":
        tseries, cseries = breaker_series(target), breaker_series(cand)
        if tseries and cseries and tseries != cseries:
            return "series"
        # Panel hardware without a detectable series still pairs only within its brand family
        if not (tseries and cseries):
            t_is_breaker = "Circuit Breakers" in str(target.get(TAX_COL, ""))
            c_is_breaker = "Circuit Breakers" in str(cand.get(TAX_COL, ""))
            t_is_panel = "Load Centers / Panelboards" in str(target.get(TAX_COL, ""))
            c_is_panel = "Load Centers / Panelboards" in str(cand.get(TAX_COL, ""))
            if (t_is_breaker and c_is_panel) or (c_is_breaker and t_is_panel):
                breaker = target if t_is_breaker else cand
                if not re.search(r"\bclassified\b", _text(breaker), re.I):
                    t_brand, c_brand = _brand(target), _brand(cand)
                    if t_brand and c_brand and t_brand != c_brand:
                        return "panel_brand"
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
        if "dry_flex" in tmat and "liquidtight" in cmat and "dry_flex" not in cmat:
            return "material"
        if "liquidtight" in tmat and "dry_flex" in cmat and "liquidtight" not in cmat:
            return "material"
        pvc_locknut = tmat == {"pvc"} and bool(re.search(r"\bmale\b.*adapt", target_text, re.I)) and bushing_kind(cand) == "locknut"
        if len(tmat) == 1 and cmat and not tmat & cmat and not pvc_locknut:
            return "material"
        if (
            tmat == {"pvc"}
            and cparts[1] == "Bushing / K.O. Seal / Locknuts"
            and "pvc" not in cmat
            and not _PLASTIC.search(str(cand.get("PRODUCT", "")))
            and not pvc_locknut
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
    if tparts[0] in GANG_MAINS and cparts[0] in GANG_MAINS:
        tgang, cgang = gang_count(target), gang_count(cand)
        t_is_plate = "Wallplates" in tparts[1:2]
        c_is_plate = "Wallplates" in cparts[1:2]
        t_is_dev = any(k in tparts[1:2] for k in ["Receptacles", "Switches", "GFCI Personnel Protection Devices", "Specialty Switches", "Combination Devices", "Straight Blade 10-20 Amp"])
        c_is_dev = any(k in cparts[1:2] for k in ["Receptacles", "Switches", "GFCI Personnel Protection Devices", "Specialty Switches", "Combination Devices", "Straight Blade 10-20 Amp"])

        # Multi-gang wallplates accept 1-gang wiring devices (e.g. 5-gang plate takes 1-gang Decora devices)
        if (t_is_plate and c_is_dev and cgang == 1) or (c_is_plate and t_is_dev and tgang == 1):
            pass
        elif tgang == 1 and cgang >= 3:
            return "gang"
        elif cgang == 1 and tgang >= 3:
            return "gang"
        elif tgang >= 3 and cgang in (1, 2):
            return "gang"
        elif cgang >= 3 and tgang in (1, 2):
            return "gang"
        elif tgang and cgang and tgang != cgang:
            if t_is_plate or c_is_plate:
                return "gang"
            if abs(tgang - cgang) > 1:
                return "gang"
        # 3+ gang wallplates cannot take 4" square boxes or 4" square mud rings (which are max 2-gang)
        if t_is_plate and tgang >= 3:
            if box_kind(cand) in ("4sq", "4-11/16", "oct3", "oct4", "round_box", "fan_box", "1g", "2g"):
                return "gang"
    # Wallplate cutout vs wiring device style compatibility
    t_is_plate = "Wallplates" in str(target.get(TAX_COL, "")) or "wallplate" in _text(target).lower() or box_component(target).endswith("cover")
    c_is_plate = "Wallplates" in str(cand.get(TAX_COL, "")) or "wallplate" in _text(cand).lower() or box_component(cand).endswith("cover")
    if t_is_plate != c_is_plate:
        t_style = device_cutout(target)
        c_style = device_cutout(cand)
        device = cand if t_is_plate else target
        plate_style, device_style = (t_style, c_style) if t_is_plate else (c_style, t_style)
        is_device = any(k in str(device.get(TAX_COL, "")) for k in ("Receptacles", "Switches", "GFCI", "Combination Devices"))
        if is_device and plate_style == "blank":
            return "cutout_mismatch"
        if is_device and plate_style and not device_style:
            return "unverified_device_cutout"
        if t_style and c_style:
            if t_style == "combo" or c_style == "combo":
                plate = target if t_is_plate else cand
                device_style = c_style if t_is_plate else t_style
                plate_text = _text(plate)
                openings = set()
                for style, pattern in (
                    ("toggle", r"\b(?:toggle|tog)\b"),
                    ("duplex", r"\b(?:duplex|dplx)\b"),
                    ("decora", r"\b(?:decora|decorator|gfci|gfi)\b"),
                    ("single_round", r"\b(?:single receptacle|single recp|1\.406|1\.59\d*)\b"),
                    ("dryer_round", r"\b(?:2\.15|range/dryer)\b"),
                ):
                    if re.search(pattern, plate_text, re.I):
                        openings.add(style)
                if openings and device_style != "combo" and device_style not in openings:
                    return "cutout_mismatch"
            elif t_style != c_style:
                return "cutout_mismatch"
    if (
        "Dimmers/Dimming Controls" in str(target.get(TAX_COL, ""))
        and "Dimmers/Dimming Controls" in str(cand.get(TAX_COL, ""))
        and re.search(r"\bdimmer\b", _text(target), re.I)
        and re.search(r"\bdimmer\b", _text(cand), re.I)
        and not re.search(r"\b(?:companion|remote)\b", _text(target) + " " + _text(cand), re.I)
    ):
        return "dimmer_substitute"
    if re.search(r"\bdvcl-153p\b", _text(target), re.I) and re.search(
        r"\bpico\b|wireless controller|occupancy|vacancy|sensor switch|\b(?:light|steel) pole\b",
        _text(cand),
        re.I,
    ):
        return "dimmer_control_mismatch"
    is_t_ss = "Wallplates" in tparts[1:2] and bool(re.search(r"stainless|302ss|\bss\d\b", _text(target), re.I))
    is_c_ss = "Wallplates" in cparts[1:2] and bool(re.search(r"stainless|302ss|\bss\d\b", _text(cand), re.I))
    if tparts[0] == cparts[0] == "Wiring Devices" and not (is_t_ss or is_c_ss) and not any(
        node.startswith(_COLOR_SKIP_NODES)
        for node in tparts[1:2] + cparts[1:2]
    ):
        tcolor, ccolor = _color(target), _color(cand)
        if tcolor and ccolor and tcolor != ccolor:
            return "color"
    # Wiring devices: voltage mismatches (e.g. 480V/600V vs 125V/250V)
    if tparts[0] == cparts[0] == "Wiring Devices":
        t_text = _text(target).lower()
        c_text = _text(cand).lower()
        t_is_hi_v = bool(re.search(r"\b(?:480\s*v|600\s*v|480\s*volt|600\s*volt)\b", t_text))
        c_is_hi_v = bool(re.search(r"\b(?:480\s*v|600\s*v|480\s*volt|600\s*volt)\b", c_text))
        t_is_std_v = bool(re.search(r"\b(?:120\s*v|125\s*v|240\s*v|250\s*v|125/250\s*v)\b", t_text))
        c_is_std_v = bool(re.search(r"\b(?:120\s*v|125\s*v|240\s*v|250\s*v|125/250\s*v)\b", c_text))
        if (t_is_hi_v and c_is_std_v) or (t_is_std_v and c_is_hi_v):
            return "voltage_mismatch"
    # Standard wallplates do not accept floor boxes
    if "Wallplates" in str(target.get(TAX_COL, "")) and "Floor Boxes" in str(cand.get(TAX_COL, "")) and not re.search(r"floor", _text(target), re.I):
        return "floor_box_mismatch"
    if "Floor Boxes" in str(target.get(TAX_COL, "")) and "Wallplates" in str(cand.get(TAX_COL, "")) and not re.search(r"floor", _text(cand), re.I):
        return "floor_box_mismatch"

    # Never suggest power tools or heavy hand tools on commodity items (fasteners, wiring devices, small hardware)
    is_comm_target = (
        (tparts[0] in ("Fasteners & Hardware", "Wiring Devices", "Connectors & Crimps")
         or bool(re.search(r"\b(?:receptacle|switch|wallplate|outlet|plug|washer|hex nut|locknut|machine screw)\b", _text(target), re.I)))
        and not bool(re.search(r"pop-up|countertop|floor box|flb\d", _text(target), re.I))
    )
    if is_comm_target and cparts[0] == "Tools & Meters":
        if re.search(r"\b(?:plier|wrench|ratchet|saw|driver system|packout|stripper|crimper|bender|meter|tester|tracer|locator|multimeter|laser|camera|heat gun)\b", _text(cand), re.I):
            return "tool_mismatch"

    # Panel gear (load centers, panelboards, breakers) is never an accessory for commodity fittings, conduit, or fasteners
    if (
        tparts[0] in ("Fittings", "Fasteners & Hardware", "Conduit, Strut & Raceways")
        and cparts[0] == "Power Distribution"
        and cparts[1:2]
        and cparts[1] in ("Circuit Breakers", "Load Centers / Panelboards")
    ):
        return "panel_gear_mismatch"

    # Round/ceiling boxes do not accept rectangular wall switches or receptacles
    if box_kind(target) in ("round_box", "fan_box") and cparts[0] == "Wiring Devices" and cparts[1:2] and cparts[1] in ("Receptacles", "Switches", "Combination Devices", "GFCI Personnel Protection Devices", "Specialty Switches", "Straight Blade 10-20 Amp"):
        return "round_box_device"

    # Fuse class, voltage, and amperage compatibility
    if tparts[0] == "Fuses & Accessories" or cparts[0] == "Fuses & Accessories":
        t_text = _text(target).lower()
        c_text = _text(cand).lower()
        t_is_fuse = bool(re.search(r"\bfuse\b", t_text)) and not bool(re.search(r"block|holder|reducer|puller|clip|cover unit", t_text))
        c_is_fuse = bool(re.search(r"\bfuse\b", c_text)) and not bool(re.search(r"block|holder|reducer|puller|clip|cover unit", c_text))
        if t_is_fuse and c_is_fuse:
            return "fuse_substitute"
        c_is_fuse_acc = bool(re.search(r"block|holder|reducer|clip|box cover unit", c_text))
        if t_is_fuse and c_is_fuse_acc:
            t_cls = fuse_class(target)
            c_cls = fuse_class(cand)
            if t_cls and c_cls and t_cls != c_cls:
                return "fuse_class"
            if c_cls and not t_cls:
                return "fuse_class"
            if t_cls and not c_cls and re.search(r"class [a-z]|midget|plug fuse", c_text):
                return "fuse_class"
            t_is_600 = bool(re.search(r"600\s*v", t_text))
            c_is_250 = bool(re.search(r"250\s*v|125\s*v", c_text)) and not bool(re.search(r"600\s*v", c_text))
            if t_is_600 and c_is_250:
                return "fuse_voltage"
            t_amps = amp_ratings(target)
            c_amps = amp_ratings(cand)
            if t_amps and c_amps and max(t_amps) > max(c_amps):
                return "fuse_amps"

    # Spa panels and AC disconnects do not take Emergency Service Disconnect labels
    if (is_hvac_disconnect(target) or re.search(r"\b(?:spa|pool)\b", _text(target), re.I)) and re.search(
        r"emergency service disconnect|service disconnect", _text(cand), re.I
    ):
        return "label_mismatch"

    # Low voltage brackets (Arlington LV1/LV2/LV3) do not take smoke alarms, exit signs, or heavy metal electrical junction boxes
    if re.search(r"low voltage.*(?:bracket|device|plate|frame)|\blv[1-4]\b|\blvn[1-4]\b", _text(target), re.I):
        if re.search(r"smoke|carbon monoxide|detector|fire alarm|exit sign|emergency light|\b4sq\b|4-11/16|square box", _text(cand), re.I):
            return "lv_bracket_mismatch"

    # Box mounting brackets and stud supports do not take smoke alarms or exit signs
    if re.search(r"box mount support|stud bracket|box bracket", _text(target), re.I):
        if re.search(r"smoke|carbon monoxide|alarm|detector|exit sign|emergency light", _text(cand), re.I):
            return "bracket_trade"

    # Fiber optics: Single-Mode vs Multimode conflict
    if "Fiber Optic" in str(target.get(TAX_COL, "")) or "Fiber Optic" in str(cand.get(TAX_COL, "")):
        t_sm = bool(re.search(r"single[- ]?mode|\b9/125\b|\b8\.3/125\b|\bsm\b|os[12]\b", _text(target), re.I))
        c_sm = bool(re.search(r"single[- ]?mode|\b9/125\b|\b8\.3/125\b|\bsm\b|os[12]\b", _text(cand), re.I))
        t_mm = bool(re.search(r"multi[- ]?mode|\b50/125\b|\b62\.5/125\b|\bmm\s*fiber\b|om[1-4]\b", _text(target), re.I))
        c_mm = bool(re.search(r"multi[- ]?mode|\b50/125\b|\b62\.5/125\b|\bmm\s*fiber\b|om[1-4]\b", _text(cand), re.I))
        if (t_sm and c_mm) or (t_mm and c_sm):
            return "fiber_mode"

    # Datacomm / Low Voltage tools & equipment: reject high-voltage power distribution (breakers, load centers, transformers), conduit fittings, and light bulbs
    if tparts[0] == "Data & Telecom" or "Datacomm" in tparts[0]:
        if cparts[0] in ("Power Distribution", "Fuses & Accessories", "Lamps, Bulbs & Ballast") or (cparts[0] in ("Fittings", "Conduit, Strut & Raceways") and "Low Voltage" not in str(cand.get(TAX_COL, "")) and "Raceway" not in str(cand.get(TAX_COL, ""))):
            return "trade_mismatch"
        if re.search(r"staple|wireway|under.?cab|vanity|light switch|\bbr\d{2,3}\b|breaker|panelboard|rolling.*box|work belt|tool belt", _text(cand), re.I):
            return "trade_mismatch"

    # Commercial bolt-on panelboard breakers (GHB, BAB, BQD, 277/480V) reject residential Romex wire and residential range/dryer hookups
    if tparts[0] == "Power Distribution" and "Circuit Breakers" in tparts[1:2]:
        t_is_comm_breaker = bool(re.search(r"\b(?:ghb|bab|bqd|prl|bolt-on|277/480|480v|277v)\b", _text(target), re.I))
        if t_is_comm_breaker:
            c_text = _text(cand).lower()
            if re.search(r"nm-b|romex|dryer|range recp|range receptacle|flush range|ac disconnect|air conditioning disconnect", c_text):
                return "breaker_trade"

    # LED linear tubes (T8, T5, T12) reject fluorescent ballasts (if ballast bypass / type B), ceiling fan boxes, and exhaust fans; linear tubes are substitutes
    if "Bulbs - LED" in str(target.get(TAX_COL, "")) and re.search(r"\b(?:t8|t5|t12|linear tube)\b", _text(target), re.I):
        c_text = _text(cand).lower()
        if re.search(r"exhaust fan|ventilation fan|bath fan|\bcfm\b|fan box|ceiling fan|paddle fan", c_text):
            return "lighting_mismatch"
        if re.search(r"\b(?:t8|t5|t12|linear tube)\b", c_text) and "Bulbs - LED" in str(cand.get(TAX_COL, "")):
            return "tube_substitute"
        if re.search(r"\b(?:byp\b|ballast bypass|type b\b|directdrive)\b", _text(target), re.I) and re.search(r"\bballast\b", c_text) and not re.search(r"bypass", c_text):
            return "ballast_bypass_conflict"

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

    by_subcategory: dict[str, list[dict[str, str]]] = defaultdict(list)
    for candidate in candidates:
        key = candidate.get("tax_sub2") or candidate.get("sub_category") or candidate.get(TAX_COL, "")
        by_subcategory[key].append(candidate)
    for subcategory_candidates in by_subcategory.values():
        subcategory_candidates.sort(key=lambda candidate: (-candidate.get(ORDERS_COL, 0), candidate[ID_COL]))

    selected: list[dict[str, str]] = []
    subcategories = sorted(
        by_subcategory,
        key=lambda s: (-max((c.get(ORDERS_COL, 0) for c in by_subcategory[s]), default=0), s),
    )
    round_index = 0
    limit = min(len(candidates), MAX_CANDIDATES)
    while len(selected) < limit:
        added = False
        for subcategory in subcategories:
            products = by_subcategory[subcategory]
            if round_index < len(products):
                selected.append(products[round_index])
                added = True
                if len(selected) == limit:
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
        is_tool_group = group.startswith("rule") and (
            TAXONOMY_RULES[group].accessory_path == "Tools & Meters > Cordless Tools"
            or bool(TAXONOMY_RULES[group].accessory_keywords and re.search(r"exhaust filter|filter vent|thermostat|contact block|collar with|shroud|legend plate|tagout|kit|hasp|padlock|do not operate", TAXONOMY_RULES[group].accessory_keywords.pattern))
        )
        for candidate in by_group[group]:
            if (
                not is_tool_group
                and not (group in TAXONOMY_RULES and TAXONOMY_RULES[group].verified_fit)
                and candidate.get(ORDERS_COL, MIN_CANDIDATE_ORDERS) < MIN_CANDIDATE_ORDERS
            ) or "obsolete" in candidate["PRODUCT"].lower():
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
            "popularity": f"{int(candidate.get(ORDERS_COL, 0))} orders",
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

    # Interleave rule-based candidates by subcategory
    by_subcat: dict[str, list[dict[str, str]]] = defaultdict(list)
    for c in rule_based:
        key = c.get("tax_sub2") or c.get("sub_category") or c.get(TAX_COL, "")
        by_subcat[key].append(c)
    for subcat_cands in by_subcat.values():
        subcat_cands.sort(key=lambda c: (-c.get(ORDERS_COL, 0), c[ID_COL]))

    interleaved: list[dict[str, str]] = []
    subcats = sorted(
        by_subcat,
        key=lambda s: (-max((c.get(ORDERS_COL, 0) for c in by_subcat[s]), default=0), s),
    )
    round_idx = 0
    while len(interleaved) < len(rule_based):
        added = False
        for subcat in subcats:
            items = by_subcat[subcat]
            if round_idx < len(items):
                interleaved.append(items[round_idx])
                added = True
        if not added:
            break
        round_idx += 1

    result = list(selected)
    for candidate in interleaved:
        if len(result) >= MIN_SUGGESTIONS:
            break
        if candidate[ID_COL] not in result and candidate[ID_COL] != target[ID_COL]:
            result.append(candidate[ID_COL])

    return result


def load_checkpoint(path: str, allow_new: bool = False) -> pd.DataFrame:
    require_checkpoint(path, allow_new)
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
    seed: int = 42,
    pids_only: set[str] | None = None,
    top_selling: int = 0,
    top_offset: int = 0,
    allow_new_checkpoint: bool = False,
) -> int:
    done = load_checkpoint(checkpoint_path, allow_new=allow_new_checkpoint)
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
    load_dotenv()
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set (add it to the environment or .env).")

    source = pd.read_csv(input_path, dtype=str, keep_default_na=False)
    missing = REQUIRED_COLUMNS - set(source.columns)
    if missing:
        raise ValueError(f"{input_path} is missing columns: {sorted(missing)}")
    source = attach_taxonomy(source.drop_duplicates(subset=ID_COL, keep="first"))
    curated = load_curated_accessories()
    validate_curated_accessories(curated, set(source[ID_COL]))
    by_group = _candidate_indexes(source)
    if top_selling:
        source = source.sort_values(by=ORDERS_COL, ascending=False).iloc[top_offset : top_offset + top_selling]
    elif sample:
        active = source[source[ORDERS_COL] >= MIN_CANDIDATE_ORDERS]
        pool = active if len(active) >= sample else source
        sampled = pool.sample(n=min(sample * 3, len(pool)), random_state=seed)
        valid = [t for t in sampled.to_dict("records") if build_candidates(t, by_group)]
        source = pd.DataFrame(valid[:sample])
    curated_ids = set(curated[OUTPUT_ID_COL])
    if pids_only:
        pending = source[source[ID_COL].isin(pids_only) & ~source[ID_COL].isin(curated_ids)]
        # Remove pending items from done so they get recomputed
        done = done[~done[OUTPUT_ID_COL].isin(pids_only)]
    else:
        pending = source[~source[ID_COL].isin(set(done[OUTPUT_ID_COL])) & ~source[ID_COL].isin(curated_ids)]
    logger.info(
        "%d rows total (%d curated locked), %d done, %d pending",
        len(source),
        len(curated_ids),
        len(done),
        len(pending),
    )

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

    # Guarantee locked curated accessories overwrite any batch or stale checkpoint rows
    if not curated.empty:
        curated_map = curated.set_index(OUTPUT_ID_COL)
        mask = final[OUTPUT_ID_COL].isin(curated_ids)
        for col in RELATION_COLUMNS:
            final.loc[mask, col] = final.loc[mask, OUTPUT_ID_COL].map(curated_map[col])
        logger.info("Preserved %d curated accessories in final output.", len(curated_ids))

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
    parser.add_argument(
        "--allow-new-checkpoint",
        action="store_true",
        help="Allow a missing checkpoint and start a new one; Gemini calls may incur costs.",
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
    parser.add_argument(
        "--top-selling",
        type=int,
        default=0,
        help="Run Gemini on top N best-selling products by order volume.",
    )
    parser.add_argument(
        "--top-offset",
        type=int,
        default=0,
        help="Offset for --top-selling (e.g. 50 to run ranks 51-100).",
    )
    parser.add_argument("--seed", type=int, default=42, help="Random seed for --sample.")
    parser.add_argument(
        "--rebuild-rules",
        action="store_true",
        help="Rebuild only products affected by the newly added rules (rules 560-879).",
    )
    args = parser.parse_args()
    if args.sample or args.top_selling:
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
    pids_only = None
    if args.rebuild_rules:
        source_df = pd.read_csv(args.input, dtype=str, keep_default_na=False)
        source_df = attach_taxonomy(source_df.drop_duplicates(subset=ID_COL, keep="first"))
        new_rule_indices = [f"rule{i}" for i in range(560, 880)]
        pids_only = set()
        for r in source_df.to_dict("records"):
            groups = _matching_groups(r)
            if any(g in new_rule_indices for g in groups):
                pids_only.add(r[ID_COL])
        logger.info("Found %d products affected by rules 560-880 to regenerate.", len(pids_only))
    try:
        failures = asyncio.run(
            run(
                args.input,
                args.output,
                args.checkpoint,
                args.model,
                args.sample,
                args.seed,
                pids_only=pids_only,
                top_selling=args.top_selling,
                top_offset=args.top_offset,
                allow_new_checkpoint=args.allow_new_checkpoint,
            )
        )
    except (OSError, RuntimeError, ValueError) as exc:
        parser.error(str(exc))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
