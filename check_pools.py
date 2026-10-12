"""Audit candidate pools for obvious mismatches without calling Gemini.

Usage:
    python check_pools.py [--csv pool_issues.csv] [--top 25]

Builds every product's candidate pool, flags target/candidate pairs that break
simple compatibility checks (gang count, colour, trade size), and ranks the
target-node -> candidate-node pairs with the most violations so rule fixes can
be made in batches.
"""

import argparse
import re
from collections import Counter, defaultdict

import pandas as pd

import generate_accessories as g

SIZED_MAINS = ("Fittings", "Conduit", "Raceway")
FLOOD_SHARE = 0.05


def _main(row: dict[str, str]) -> str:
    return row[g.TAX_COL].split(">")[0].strip()


def issues(target: dict[str, str], cand: dict[str, str]) -> list[str]:
    found = []
    tm, cm = _main(target), _main(cand)
    conflict = g.hard_conflict(target, cand)
    if conflict:
        found.append(conflict)
    if tm in SIZED_MAINS and cm in SIZED_MAINS:
        ts, cs = g.trade_sizes(target), g.trade_sizes(cand)
        if ts and cs and not any(abs(ts[0] - s) < 1e-6 for s in cs):
            found.append("size")
    return found


def node(row: dict[str, str]) -> str:
    return " > ".join(p.strip() for p in row[g.TAX_COL].split(">")[:2])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", help="write every flagged pair to this CSV")
    parser.add_argument("--top", type=int, default=25)
    args = parser.parse_args()

    source = pd.read_csv(g.INPUT_PATH if hasattr(g, "INPUT_PATH") else "enriched_products.csv",
                         dtype=str, keep_default_na=False)
    source = g.attach_taxonomy(source.drop_duplicates(subset=g.ID_COL, keep="first"))
    indexes = g._candidate_indexes(source)

    pairs = 0
    by_kind: Counter = Counter()
    by_node: dict[tuple[str, str, str], list] = defaultdict(list)
    appearances: Counter = Counter()
    names: dict[str, str] = {}
    flagged_rows = []
    pool_sizes = []
    for target in source.to_dict("records"):
        pool = g.build_candidates(target, indexes)
        pool_sizes.append(len(pool))
        for cand in pool:
            pairs += 1
            appearances[cand[g.ID_COL]] += 1
            names[cand[g.ID_COL]] = cand["PRODUCT"]
            for kind in issues(target, cand):
                by_kind[kind] += 1
                key = (kind, node(target), node(cand))
                by_node[key].append((target["PRODUCT"], cand["PRODUCT"]))
                flagged_rows.append(
                    {
                        "issue": kind,
                        "target_id": target[g.ID_COL],
                        "target": target["PRODUCT"],
                        "candidate_id": cand[g.ID_COL],
                        "candidate": cand["PRODUCT"],
                    }
                )

    print(f"{len(source)} products, {pairs} pairs, avg pool {pairs / len(source):.1f}")
    print("Flagged pairs:", dict(by_kind) or "none")
    print(f"\nTop {args.top} target -> candidate node pairs by violations:")
    ranked = sorted(by_node.items(), key=lambda kv: -len(kv[1]))[: args.top]
    for (kind, tnode, cnode), examples in ranked:
        print(f"  {len(examples):5d}  [{kind}] {tnode}  ->  {cnode}")
        t, c = examples[0]
        print(f"         e.g. {t[:45]} | {c[:45]}")

    limit = FLOOD_SHARE * len(source)
    print(f"\nCandidates in more than {FLOOD_SHARE:.0%} of pools:")
    for cid, count in appearances.most_common(10):
        if count > limit:
            print(f"  {count:5d}  {names[cid][:70]}")

    if args.csv:
        pd.DataFrame(flagged_rows).to_csv(args.csv, index=False)
        print(f"\nWrote {len(flagged_rows)} flagged pairs to {args.csv}")


if __name__ == "__main__":
    main()
