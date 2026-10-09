"""Map enriched products onto the website taxonomy (CATEGORY.csv) without any API calls.

Usage:
    python map_taxonomy.py [--input enriched_products.csv] [--taxonomy CATEGORY.csv]
        [--output product_taxonomy.csv]

Each product is scored against every taxonomy path using IDF-weighted token overlap.
The output has the best path, a confidence margin, and the runner-up so low-confidence
rows can be reviewed.
"""

import argparse
import math
import re
from collections import Counter

import pandas as pd

ID_COL = "BLAZER ID"
LEVELS = ["Main Category", "Subcategory 1", "Subcategory 2", "Subcategory 3", "Subcategory 4"]
_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = frozenset({"and", "the", "for", "with", "of", "in", "type", "other", "inch", "in"})


def stem(token: str) -> str:
    for suffix in ("ies", "es", "s"):
        if len(token) > len(suffix) + 2 and token.endswith(suffix):
            return token[: -len(suffix)] + ("y" if suffix == "ies" else "")
    return token


def tokens(text: str) -> list[str]:
    return [stem(t) for t in _TOKEN.findall(text.lower()) if t not in _STOP and not t.isdigit()]


def load_paths(taxonomy: pd.DataFrame) -> list[tuple[str, ...]]:
    paths = []
    for row in taxonomy[LEVELS].itertuples(index=False):
        path = tuple(value.strip() for value in row if value.strip())
        if path:
            paths.append(path)
    return paths


def build_index(paths: list[tuple[str, ...]]):
    """Weights: the deepest node name counts most, ancestors add context."""
    documents = []
    for path in paths:
        weights: Counter[str] = Counter()
        for depth, name in enumerate(path):
            is_leaf = depth == len(path) - 1
            for token in set(tokens(name)):
                weights[token] += 3.0 if is_leaf else 1.0
        documents.append(weights)
    document_frequency: Counter[str] = Counter()
    for weights in documents:
        document_frequency.update(weights.keys())
    n = len(documents)
    idf = {token: math.log((n + 1) / (count + 1)) + 1 for token, count in document_frequency.items()}
    return documents, idf


def product_tokens(row: dict[str, str]) -> Counter[str]:
    weights: Counter[str] = Counter()
    for column, weight in (("clean_title", 2.0), ("sub_category", 3.0), ("primary_category", 1.0)):
        for token in set(tokens(row.get(column, ""))):
            weights[token] += weight
    for token in set(tokens(row.get("PRODUCT", ""))):
        weights[token] += 1.0
    return weights


def best_matches(row, paths, documents, idf, top=2):
    query = product_tokens(row)
    scored = []
    for path, weights in zip(paths, documents):
        score = sum(
            query[token] * weights[token] * idf[token] for token in query if token in weights
        )
        if score:
            norm = math.sqrt(sum((w * idf[t]) ** 2 for t, w in weights.items()))
            scored.append((score / norm, path))
    scored.sort(key=lambda item: -item[0])
    return scored[:top]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default="enriched_products.csv")
    parser.add_argument("--taxonomy", default="CATEGORY.csv")
    parser.add_argument("--output", default="product_taxonomy.csv")
    args = parser.parse_args()

    products = pd.read_csv(args.input, dtype=str, keep_default_na=False).drop_duplicates(ID_COL)
    paths = load_paths(pd.read_csv(args.taxonomy, dtype=str, keep_default_na=False))
    documents, idf = build_index(paths)

    rows = []
    for row in products.to_dict("records"):
        matches = best_matches(row, paths, documents, idf)
        best_score, best_path = matches[0] if matches else (0.0, ())
        second_score = matches[1][0] if len(matches) > 1 else 0.0
        rows.append(
            {
                ID_COL: row[ID_COL],
                **{level: (best_path[i] if i < len(best_path) else "") for i, level in enumerate(LEVELS)},
                "score": round(best_score, 3),
                "margin": round(best_score - second_score, 3),
                "runner_up": " > ".join(matches[1][1]) if len(matches) > 1 else "",
            }
        )
    pd.DataFrame(rows).to_csv(args.output, index=False, encoding="utf-8")
    print(f"Wrote {args.output} ({len(rows)} products)")


if __name__ == "__main__":
    main()
