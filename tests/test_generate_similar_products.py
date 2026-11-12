import unittest

from generate_accessories import TAX_COL
from generate_similar_products import find_similar_products


def _row(product_id, product, tax_path, tax_sub2=""):
    return {
        "BLAZER ID": product_id,
        "PRODUCT": product,
        "clean_title": product,
        TAX_COL: tax_path,
        "tax_sub2": tax_sub2,
        "tax_sub3": "",
        "orders_6m": 0,
    }


class SimilarProductScoringTests(unittest.TestCase):
    def test_related_datacomm_tools_rank_above_unrelated_tools(self):
        node = "Tools & Meters > Datacomm Tools"
        target = _row("target", "KLE VDV226-110 Modular Crimper", node)
        related = _row("related", "KLE VDV427 Punchdown Tool", node)
        unrelated = _row("unrelated", "KLE Adjustable Wrench", node)

        result = find_similar_products(target, {node: [target, unrelated, related]}, {})

        self.assertLess(result.index("related"), result.index("unrelated"))

    def test_matching_deeper_taxonomy_ranks_above_other_subcategories(self):
        node = "Wiring Devices > Receptacles"
        target = _row("target", "Generic duplex receptacle", node, "Residential")
        matching = _row("matching", "Generic duplex receptacle", node, "Residential")
        different = _row("different", "Generic duplex receptacle", node, "Industrial")

        result = find_similar_products(target, {node: [target, different, matching]}, {})

        self.assertLess(result.index("matching"), result.index("different"))


if __name__ == "__main__":
    unittest.main()
