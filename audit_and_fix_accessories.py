"""Deterministic quality audit and fixer for product_accessories.csv (0 API Calls).

Performs comprehensive validation across all 8,477 items:
1. Locks in all curated hero packages from curated_accessories.csv.
2. Fills short/empty rows (under 10 items) using balanced rule candidates and universal trade staples.
3. Enforces trade sanity (e.g. no power tools/expensive ratchets on small commodity fasteners).
4. Verifies no self-recommendations or duplicates in any row.
"""

import csv
import logging
import os
import re
from collections import defaultdict

import pandas as pd
import generate_accessories as ga
import export_web_catalog

OUTPUT_FILE = "product_accessories.csv"
CURATED_FILE = "curated_accessories.csv"
RELATION_COLS = [f"PROD_ID_REL{i}" for i in range(1, 11)]

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("audit_accessories")


_DEVICE_WIRE_IDS = frozenset({"1021", "1026"})


def _device_staple_ids(is_20a: bool) -> list[str]:
    return [
        "1021" if is_20a else "1026",
        "222277",
        "329271",
        "132497",
        "136127",
        "766",
        "222273",
    ]


def _is_device_wire_staple(target_taxonomy: str, product_id: str) -> bool:
    return "Wiring Devices" in target_taxonomy and product_id in _DEVICE_WIRE_IDS


def _is_rough_in_box(row: dict[str, str]) -> bool:
    taxonomy = row.get(ga.TAX_COL, "")
    text = row.get("clean_title", "") + " " + row.get("PRODUCT", "")
    return (
        "Boxes & Enclosures" in taxonomy
        and any(k in taxonomy for k in ["Boxes - Non-Metallic", "Boxes - Metallic", "Boxes - Weatherproof"])
        and not bool(re.search(r"\b(?:shim|spacer|extender|bushing|clip)\b", text, re.I))
    )


def _is_wire_nut(row: dict[str, str]) -> bool:
    text = " ".join(row.get(key, "") for key in ("clean_title", "PRODUCT", "normalized_specs"))
    return "Wire Connectors" in row.get(ga.TAX_COL, "") and bool(
        re.search(r"\b(?:wire.?nut|wing.?nut|spring connector|twister|twist[- ]on)\b", text, re.I)
    )


def _installation_essentials(
    target: dict[str, str], candidates: list[dict[str, str]]
) -> list[str]:
    text = ga._text(target)
    node = target.get(ga.TAX_COL, "")
    kinds = []
    if re.search(r"\b38ast\b", text, re.I):
        kinds = ["mc", "box"]
    elif ga.box_component(target).endswith("cover") and ga.box_kind(target) in {"4sq", "4-11/16"}:
        kinds = ["cover_screw"]
    elif ga.bushing_kind(target) == "grounding":
        kinds = ["bonding_wire"]
    elif "PVC Fittings" in node and re.search(r"\bmale\b.*adapt", text, re.I):
        kinds = ["cement", "locknut"]

    selected = []
    for kind in kinds:
        for candidate in candidates:
            c_node, c_text = candidate.get(ga.TAX_COL, ""), ga._text(candidate)
            matches = (
                (kind == "mc" and "Armored Cable" in c_node and ga.cable_family(candidate) == "mc")
                or (kind == "box" and "Boxes & Enclosures" in c_node and ga.box_component(candidate) == "box" and 0.5 in ga.knockout_sizes(candidate))
                or (kind == "cover_screw" and "Screws" in c_node and bool(re.search(r"\b8-32\b.*(?:rhms|machine screw)", c_text, re.I)))
                or (kind == "bonding_wire" and "Wire, Cable & Cord" in c_node and ("Bare Copper" in c_node or ga.conductor_role(candidate) == "ground"))
                or (kind == "cement" and "Paint, Chemicals" in c_node and bool(re.search(r"pvc.*cement|solvent.*cement|quick.?set cement", c_text, re.I)))
                or (kind == "locknut" and ga.bushing_kind(candidate) == "locknut")
            )
            if matches and not ga.hard_conflict(target, candidate):
                selected.append(candidate[ga.ID_COL])
                break
    return selected


def main() -> None:
    logger.info("Loading catalog, taxonomy, and order volumes...")
    source = pd.read_csv("enriched_products.csv", dtype=str, keep_default_na=False)
    source = ga.attach_taxonomy(source, ga.TAXONOMY_PATH)
    source = ga.attach_orders(source, ga.RANK_PATH)

    records = source.to_dict("records")
    source_map = {r[ga.ID_COL]: r for r in records}
    by_group = ga._candidate_indexes(source)

    # Universal high-volume trade staples for topping up short rows (ordered by utility)
    # Wire nuts, electrical tape, voltage tester, pulling lube, 4sq box, 12/2 romex, grounding pigtail
    universal_staples = [
        "2951",    # IDE 30-651 Yellow Wire Nut 500-pack
        "329271",  # IDE 46-1700 Black Vinyl Tape
        "122128",  # KLE NCVT3P Non-Contact Voltage Tester
        "2956",    # IDE 31-388 ClearGlide Wire Pulling Lubricant
        "3223",    # RAC 232 4-Square Deep Metal Box
        "3415",    # RAC 768 4-Square 5/8in 1-Gang Mud Ring
        "161475",  # NSI PG12C-8 Grounding Pigtail with Ground Screw
        "1021",    # COP NM-B-12/2-CU-WG 250ft coil
        "1026",    # COP NM-B-14/2-CU-WG 250ft coil
        "222277",  # GB MS-550 Romex Metal Staples 1/2in (500-pack)
        "2915",    # CUL RCR50 1/2in Push-in Romex Connector
        "239455",  # MIL Clear Performance Safety Glasses
        "273445",  # KLE 32500MAG 11-in-1 Magnetic Screwdriver
        "80267",   # AFA G-Tek MaxiFlex Protective Gloves
        "60472",   # MIL 48-22-8722 Level 2 Cut Resistant Gloves
        "179229",  # MIL 48-22-3100 INKZALL Black Fine Point Marker
    ]

    # Load locked curated accessories
    curated = ga.load_curated_accessories(CURATED_FILE)
    ga.validate_curated_accessories(curated, set(source_map))
    curated_map = {}
    if not curated.empty:
        for r in curated.to_dict("records"):
            curated_map[r[ga.OUTPUT_ID_COL]] = [r[col] for col in RELATION_COLS]
    logger.info("Loaded %d locked curated items from %s", len(curated_map), CURATED_FILE)

    # Load current product_accessories.csv
    current_rows = []
    with open(OUTPUT_FILE, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        current_rows = list(reader)

    fixed_rows = []
    locked_count = 0
    tools_cleaned_count = 0
    boxes_cleaned_count = 0
    nuts_cleaned_count = 0
    incompat_cleaned_count = 0
    filled_count = 0
    short_count = 0

    for r in current_rows:
        pid = r["PROD_ID"]
        target = source_map.get(pid, {})
        t_text = target.get("clean_title", "") + " " + target.get("PRODUCT", "")
        t_tax = target.get(ga.TAX_COL, "")
        is_device = "Wiring Devices" in t_tax or ga.box_component(target).endswith("cover") or bool(re.search(r"\bs2-lf(?:sq)?\b", t_text, re.I))

        # 1. Apply locked curated row if present
        if pid in curated_map:
            fixed_rows.append({"PROD_ID": pid, **dict(zip(RELATION_COLS, curated_map[pid]))})
            locked_count += 1
            continue

        acc_ids = [r.get(col, "").strip() for col in RELATION_COLS if r.get(col, "").strip()]
        cands = None
        needs_essentials = (
            bool(re.search(r"\b38ast\b", t_text, re.I))
            or (ga.box_component(target).endswith("cover") and ga.box_kind(target) in {"4sq", "4-11/16"})
            or ga.bushing_kind(target) == "grounding"
            or ("PVC Fittings" in t_tax and bool(re.search(r"\bmale\b.*adapt", t_text, re.I)))
        )
        if needs_essentials:
            cands = ga.build_candidates(target, by_group)
            acc_ids = _installation_essentials(target, cands) + acc_ids

        # 2. Sanity check: remove self-recommendation or duplicate, enforce trade sanity & limits
        seen = {pid}
        cleaned_accs = []
        box_count = 0
        wire_nut_count = 0
        wallplate_count = 0

        for a_id in acc_ids:
            if not a_id or a_id in seen or a_id not in source_map:
                continue

            a_row = source_map[a_id]
            a_tax = a_row.get(ga.TAX_COL, "")
            a_text = a_row.get("clean_title", "") + " " + a_row.get("PRODUCT", "")

            # Hard physical conflict check (cutout, gang, conduit, color, tool mismatch)
            conflict = ga.hard_conflict(target, a_row)
            if conflict:
                if conflict == "tool_mismatch":
                    tools_cleaned_count += 1
                else:
                    incompat_cleaned_count += 1
                continue

            # Box redundancy: max 2 rough-in boxes for wiring devices (exclude shims/extenders)
            is_box = _is_rough_in_box(a_row)
            if is_device and is_box:
                if box_count >= 2:
                    boxes_cleaned_count += 1
                    continue
                box_count += 1

            # Duplicate wire connectors: max 1 twist-on wire nut
            is_wire_nut = _is_wire_nut(a_row)
            if is_wire_nut:
                if wire_nut_count >= 1:
                    nuts_cleaned_count += 1
                    continue
                wire_nut_count += 1

            # Wallplate redundancy: max 1 wallplate per gang for wiring devices
            is_wallplate = "Wallplates" in a_tax or "wallplate" in a_text.lower()
            if is_wallplate and is_device:
                if wallplate_count >= 2:
                    continue
                wallplate_count += 1

            seen.add(a_id)
            cleaned_accs.append(a_id)

        # 4. Fill short rows up to 10 using rule-based candidates, device staples, then universal staples
        if len(cleaned_accs) < 10 and target:
            if cands is None:
                cands = ga.build_candidates(target, by_group)
            for c in cands:
                c_id = c[ga.ID_COL]
                if c_id not in seen:
                    if ga.hard_conflict(target, c):
                        continue
                    is_c_box = _is_rough_in_box(c)
                    if is_device and is_c_box and box_count >= 2:
                        continue
                    is_c_nut = _is_wire_nut(c)
                    if is_c_nut and wire_nut_count >= 1:
                        continue
                    is_c_plate = "Wallplates" in c.get(ga.TAX_COL, "") or "wallplate" in ga._text(c).lower()
                    if is_device and is_c_plate and wallplate_count >= 2:
                        continue
                    if is_c_box:
                        box_count += 1
                    if is_c_nut:
                        wire_nut_count += 1
                    if is_c_plate:
                        wallplate_count += 1

                    seen.add(c_id)
                    cleaned_accs.append(c_id)
                    if len(cleaned_accs) == 10:
                        break

        # Priority staples for residential wiring devices (receptacles, switches, GFCI)
        if len(cleaned_accs) < 10 and target and ("Wiring Devices > Receptacles" in t_tax or "Wiring Devices > Switches" in t_tax or "Wiring Devices > GFCI" in t_tax):
            is_20a = bool(re.search(r"\b20\s?a(?:mp)?\b|20a\b", t_text, re.I))
            device_staples = _device_staple_ids(is_20a)
            for s_id in device_staples:
                if s_id not in seen and s_id in source_map:
                    if not ga.hard_conflict(target, source_map[s_id]):
                        seen.add(s_id)
                        cleaned_accs.append(s_id)
                        if len(cleaned_accs) == 10:
                            break

        if len(cleaned_accs) < 10:
            for s_id in universal_staples:
                if _is_device_wire_staple(t_tax, s_id):
                    continue
                if s_id not in seen and s_id in source_map:
                    staple = source_map[s_id]
                    is_box = _is_rough_in_box(staple)
                    is_nut = _is_wire_nut(staple)
                    if is_device and is_box and box_count >= 2:
                        continue
                    if is_nut and wire_nut_count >= 1:
                        continue
                    if not ga.hard_conflict(target, staple):
                        box_count += is_box
                        wire_nut_count += is_nut
                        seen.add(s_id)
                        cleaned_accs.append(s_id)
                        if len(cleaned_accs) == 10:
                            break
        if len(acc_ids) < len(cleaned_accs):
            filled_count += 1
        if len(cleaned_accs) < 10:
            short_count += 1

        fixed_rows.append({"PROD_ID": pid, **dict(zip(RELATION_COLS, cleaned_accs[:10]))})

    df_out = pd.DataFrame(fixed_rows)
    ga.save_atomic(df_out, OUTPUT_FILE)
    logger.info("Successfully audited and updated %s:", OUTPUT_FILE)
    logger.info("  - %d Curated items locked and preserved", locked_count)
    logger.info("  - %d Tool mismatches on commodity items replaced", tools_cleaned_count)
    logger.info("  - %d Excess redundant boxes capped and replaced", boxes_cleaned_count)
    logger.info("  - %d Duplicate wire nuts deduplicated and replaced", nuts_cleaned_count)
    logger.info("  - %d Incompatible plates/gang mismatches replaced", incompat_cleaned_count)
    logger.info("  - %d Rows gained relevant accessories", filled_count)
    if short_count:
        logger.warning("  - %d Rows remain below 10 accessories; incompatible filler was not added", short_count)
    else:
        logger.info("  - No short accessory rows remain")

    logger.info("Triggering final pipeline step: web catalog JSON export...")
    export_web_catalog.export_catalog()


if __name__ == "__main__":
    main()
