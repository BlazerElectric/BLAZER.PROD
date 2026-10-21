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

OUTPUT_FILE = "product_accessories.csv"
CURATED_FILE = "curated_accessories.csv"
RELATION_COLS = [f"PROD_ID_REL{i}" for i in range(1, 11)]

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("audit_accessories")


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
        "2387",    # 3M Scotch Super 33+ Vinyl Electrical Tape
        "122128",  # KLE NCVT3P Non-Contact Voltage Tester
        "2956",    # IDE 31-388 ClearGlide Wire Pulling Lubricant
        "3223",    # RAC 232 4-Square Deep Metal Box
        "3415",    # RAC 768 4-Square 5/8in 1-Gang Mud Ring
        "161475",  # NSI PG12C-8 Grounding Pigtail with Ground Screw
        "1021",    # COP NM-B-12/2-CU-WG 250ft coil
        "2915",    # CUL RCR50 1/2in Push-in Romex Connector
        "239455",  # MIL Clear Performance Safety Glasses
        "273445",  # KLE 32500MAG 11-in-1 Magnetic Screwdriver
        "80267",   # AFA G-Tek MaxiFlex Protective Gloves
    ]

    # Load locked curated accessories
    curated = ga.load_curated_accessories(CURATED_FILE)
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
    filled_count = 0

    for r in current_rows:
        pid = r["PROD_ID"]
        target = source_map.get(pid, {})
        t_text = target.get("clean_title", "") + " " + target.get("PRODUCT", "")
        t_tax = target.get(ga.TAX_COL, "")

        # 1. Apply locked curated row if present
        if pid in curated_map:
            fixed_rows.append({"PROD_ID": pid, **dict(zip(RELATION_COLS, curated_map[pid]))})
            locked_count += 1
            continue

        acc_ids = [r.get(col, "").strip() for col in RELATION_COLS if r.get(col, "").strip()]

        # 2. Sanity check: remove self-recommendation or duplicate
        seen = {pid}
        cleaned_accs = []
        for a_id in acc_ids:
            if a_id and a_id not in seen and a_id in source_map:
                # 3. Check tool on small commodity fastener
                a_row = source_map[a_id]
                a_tax = a_row.get(ga.TAX_COL, "")
                a_text = a_row.get("clean_title", "") + " " + a_row.get("PRODUCT", "")

                is_small_hardware = bool(re.search(r"\b(?:washer|hex nut|locknut|lock nut|machine screw|drywall screw)\b", t_text, re.I)) and "Fasteners & Hardware" in t_tax
                is_heavy_tool = ("Tools & Meters > Hand Tools" in a_tax or "Tools & Meters > Cordless Tools" in a_tax) and bool(re.search(r"\b(?:plier|wrench|ratchet|saw|driver system|packout)\b", a_text, re.I))

                if is_small_hardware and is_heavy_tool:
                    tools_cleaned_count += 1
                    continue

                seen.add(a_id)
                cleaned_accs.append(a_id)

        # 4. Fill short rows up to 10 using rule-based candidates then universal staples
        if len(cleaned_accs) < 10 and target:
            cands = ga.build_candidates(target, by_group)
            for c in cands:
                c_id = c[ga.ID_COL]
                if c_id not in seen:
                    seen.add(c_id)
                    cleaned_accs.append(c_id)
                    if len(cleaned_accs) == 10:
                        break

        if len(cleaned_accs) < 10:
            for s_id in universal_staples:
                if s_id not in seen and s_id in source_map:
                    seen.add(s_id)
                    cleaned_accs.append(s_id)
                    if len(cleaned_accs) == 10:
                        break
            filled_count += 1

        fixed_rows.append({"PROD_ID": pid, **dict(zip(RELATION_COLS, cleaned_accs[:10]))})

    df_out = pd.DataFrame(fixed_rows)
    ga.save_atomic(df_out, OUTPUT_FILE)
    logger.info("Successfully audited and updated %s:", OUTPUT_FILE)
    logger.info("  - %d Curated items locked and preserved", locked_count)
    logger.info("  - %d Tool mismatches on small hardware replaced", tools_cleaned_count)
    logger.info("  - %d Short rows topped up to full 10/10 capacity", filled_count)


if __name__ == "__main__":
    main()
