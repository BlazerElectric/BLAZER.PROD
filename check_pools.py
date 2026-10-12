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
WIRE_MAINS = frozenset({"Wire, Cable & Cord", "Connectors & Crimps"})
FLOOD_SHARE = 0.05


def _main(row: dict[str, str]) -> str:
    return row[g.TAX_COL].split(">")[0].strip()


AMP_NODES = ("Receptacles", "Straight Blade", "Twistlock", "Locking")


def _sub(row: dict[str, str]) -> str:
    parts = [p.strip() for p in row[g.TAX_COL].split(">")]
    return parts[1] if len(parts) > 1 else ""


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
    if tm == cm == "Power Distribution":
        ta, ca = g.breaker_series(target), g.breaker_series(cand)
        if ta and ca and ta != ca:
            found.append("series")
    if tm == cm == "Wiring Devices" and _sub(target).startswith(AMP_NODES) and _sub(cand).startswith(AMP_NODES):
        ta, ca = g.amp_ratings(target), g.amp_ratings(cand)
        if ta and ca and not ta & ca:
            found.append("amps")
    if tm == cm == "Boxes & Enclosures":
        tk, ck = g.box_kind(target), g.box_kind(cand)
        if tk and ck and tk != ck:
            found.append("box")
    if tm == cm:
        tp, cp = g.platform(target), g.platform(cand)
        if tp and cp and tp != cp:
            found.append("platform")
    if tm in WIRE_MAINS and cm in WIRE_MAINS and "Instrumentation" not in node(target) + node(cand):
        tr, cr = g.gauge_range(target), g.gauge_range(cand)
        if tr and cr and not (cr[0] * 0.999 <= tr[1] and tr[0] <= cr[1] * 1.001):
            found.append("gauge")
    return found


def source_of(target, cand, indexes, id_sets) -> str:
    """Name the rule group, kit or fallback that put cand into target's pool."""
    cid = cand[g.ID_COL]
    for group in g._matching_groups(target):
        if group not in id_sets:
            id_sets[group] = {r[g.ID_COL] for r in indexes[group]}
        if cid in id_sets[group] and (
            group not in g.TAXONOMY_RULES or g._taxonomy_compatible(group, target, cand)
        ):
            return group
    return "kit/fallback"


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
    id_sets: dict[str, set[str]] = {}
    sources: dict[tuple[str, str, str], Counter] = defaultdict(Counter)
    pool_sizes = []
    for target in source.to_dict("records"):
        pool = g.build_candidates(target, indexes)
        pool_sizes.append(len(pool))
        if any(x[g.ID_COL] == target[g.ID_COL] for x in pool):
            by_kind["self"] += 1
        for cand in pool:
            pairs += 1
            appearances[cand[g.ID_COL]] += 1
            names[cand[g.ID_COL]] = cand["PRODUCT"]
            for kind in issues(target, cand):
                by_kind[kind] += 1
                key = (kind, node(target), node(cand))
                by_node[key].append((target["PRODUCT"], cand["PRODUCT"]))
                origin = source_of(target, cand, indexes, id_sets)
                sources[key][origin] += 1
                flagged_rows.append(
                    {
                        "issue": kind,
                        "target_id": target[g.ID_COL],
                        "target": target["PRODUCT"],
                        "candidate_id": cand[g.ID_COL],
                        "candidate": cand["PRODUCT"],
                        "source": origin,
                    }
                )

    print(f"{len(source)} products, {pairs} pairs, avg pool {pairs / len(source):.1f}")
    print("Flagged pairs:", dict(by_kind) or "none")
    sizes = pd.Series(pool_sizes)
    print(
        f"Pool sizes: empty {(sizes == 0).sum()}, under 5 {((sizes > 0) & (sizes < 5)).sum()}, "
        f"median {int(sizes.median())}, max {sizes.max()}"
    )
    print(f"\nTop {args.top} target -> candidate node pairs by violations:")
    ranked = sorted(by_node.items(), key=lambda kv: -len(kv[1]))[: args.top]
    for (kind, tnode, cnode), examples in ranked:
        print(f"  {len(examples):5d}  [{kind}] {tnode}  ->  {cnode}")
        t, c = examples[0]
        top = ", ".join(f"{k[:60]} x{v}" for k, v in sources[(kind, tnode, cnode)].most_common(2))
        print(f"         from: {top}")
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
