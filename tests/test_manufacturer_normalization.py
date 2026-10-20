import json
import unittest

import pandas as pd

from manufacturer_normalization import normalize_enriched_products


class ManufacturerNormalizationTests(unittest.TestCase):
    def test_known_prefixes_override_model_manufacturer_guesses(self):
        products = pd.DataFrame(
            [
                {
                    "BLAZER ID": "dewalt-guess",
                    "PRODUCT": "DBL DAG3090 7/8 in. Auger Bit",
                    "brand_manufacturer": "DeWalt",
                    "manufacturer_part_number": "DAG3090",
                    "clean_title": "DeWalt 7/8 Inch Auger Bit",
                    "normalized_specs": "7/8 Inch",
                    "short_description": "DeWalt auger bit for drilling.",
                    "search_keywords": json.dumps(["DeWalt auger bit", "drill bit"]),
                    "common_misspellings": json.dumps(["Dewalt bit"]),
                },
                {
                    "BLAZER ID": "copper-generic",
                    "PRODUCT": "COP BARE-SD-4-SOL-CU 200R",
                    "brand_manufacturer": "Southwire",
                    "manufacturer_part_number": "",
                    "clean_title": "Southwire 4 AWG Solid Bare Copper Grounding Wire",
                    "normalized_specs": "4 AWG",
                    "short_description": "Southwire copper wire for grounding.",
                    "search_keywords": json.dumps(["Southwire copper wire", "grounding wire"]),
                    "common_misspellings": json.dumps(["Southwire wire"]),
                },
                {
                    "BLAZER ID": "aluminum-generic",
                    "PRODUCT": "ALW XHHW-250-BROWN ALUM",
                    "brand_manufacturer": "Generic",
                    "manufacturer_part_number": "",
                    "clean_title": "250 kcmil XHHW-2 Aluminum Building Wire, Brown",
                    "normalized_specs": "250 kcmil, Brown",
                    "short_description": "Aluminum building wire.",
                    "search_keywords": json.dumps(["aluminum building wire"]),
                    "common_misspellings": json.dumps(["aluminum wire"]),
                },
                {
                    "BLAZER ID": "named-aluminum-maker",
                    "PRODUCT": "ALW URD 1/0-1/0-1/0 BERGEN 1000RL ALUM",
                    "brand_manufacturer": "Bergen Cable",
                    "manufacturer_part_number": "",
                    "clean_title": "Bergen 1/0-1/0-1/0 Aluminum URD Cable",
                    "normalized_specs": "1/0-1/0-1/0",
                    "short_description": "Bergen underground distribution cable.",
                    "search_keywords": json.dumps(["Bergen cable", "URD cable"]),
                    "common_misspellings": json.dumps(["Bergen cable"]),
                },
                {
                    "BLAZER ID": "dottie-wall-anchor",
                    "PRODUCT": "DOT HXDOG 3/16IN Dia X 1-1/4 Wall Dog Kit",
                    "brand_manufacturer": "DeWalt",
                    "manufacturer_part_number": "HXDOG",
                    "clean_title": "DeWalt 3/16 Inch Wall Dog Kit",
                    "normalized_specs": "3/16 Inch",
                    "short_description": "DeWalt wall anchor kit.",
                    "search_keywords": json.dumps(["DeWalt anchors"]),
                    "common_misspellings": json.dumps(["Dewalt HXDOG"]),
                },
                {
                    "BLAZER ID": "rack-a-tiers",
                    "PRODUCT": "RAT 65300 LADDER MATE TOOL STORAGE",
                    "brand_manufacturer": "RAB Lighting",
                    "manufacturer_part_number": "65300",
                    "clean_title": "RAB Lighting Ladder Mate Tool Storage Accessory",
                    "normalized_specs": "",
                    "short_description": "RAB storage tool.",
                    "search_keywords": json.dumps(["RAB ladder tool"]),
                    "common_misspellings": json.dumps(["RAB ladder"]),
                },
            ]
        )

        result = normalize_enriched_products(products)

        self.assertEqual(result.loc[0, "brand_manufacturer"], "Diablo")
        self.assertEqual(result.loc[0, "clean_title"], "Diablo 7/8 Inch Auger Bit")
        self.assertIn("Diablo", result.loc[0, "short_description"])
        self.assertEqual(result.loc[1, "brand_manufacturer"], "Copper Generic")
        self.assertEqual(result.loc[1, "clean_title"], "4 AWG Solid Bare Copper Grounding Wire")
        self.assertNotIn("Southwire", result.loc[1, "short_description"])
        self.assertEqual(json.loads(result.loc[1, "search_keywords"]), ["copper wire", "grounding wire"])
        self.assertEqual(result.loc[2, "brand_manufacturer"], "Aluminum Generic")
        self.assertEqual(result.loc[3, "brand_manufacturer"], "Bergen Cable")
        self.assertEqual(result.loc[4, "brand_manufacturer"], "Dottie")
        self.assertEqual(result.loc[4, "clean_title"], "Dottie 3/16 Inch Wall Dog Kit")
        self.assertNotIn("Dewalt", result.loc[4, "common_misspellings"])
        self.assertEqual(result.loc[5, "brand_manufacturer"], "Rack-A-Tiers")
        self.assertEqual(
            result.loc[5, "clean_title"],
            "Rack-A-Tiers Ladder Mate Tool Storage Accessory",
        )

    def test_ambiguous_dbl_and_other_products_are_left_unchanged(self):
        products = pd.DataFrame(
            [
                {
                    "BLAZER ID": "dbl-exception",
                    "PRODUCT": "DBL 23 CD PROMO",
                    "brand_manufacturer": "Daburn Electronics",
                    "clean_title": "Daburn 23 Series Cord Grip Connector",
                },
                {
                    "BLAZER ID": "unrelated",
                    "PRODUCT": "BPT 250-RT2 Rain Tight Connector",
                    "brand_manufacturer": "Wrong Guess",
                    "clean_title": "Bridgeport Rain Tight Connector",
                },
            ]
        )

        result = normalize_enriched_products(products)

        pd.testing.assert_frame_equal(result, products)

    def test_commodity_codes_use_generic_product_class_labels(self):
        examples = [
            ("NIP", "Nipple Generic", "Patriot Industries", "Patriot 1/2 Inch Nipple"),
            ("ELB", "Elbow Generic", "Eaton", "Eaton 3/4 Inch EMT Elbow"),
            ("CON", "Conduit Generic", "Allied Tube & Conduit", "Allied 1 Inch EMT Conduit"),
            ("PVC", "PVC Generic", "Unknown", "PVC 2 Inch Schedule 40 Conduit"),
            ("LQT", "Liquidtight Generic", "Electri-Flex Company", "Electri-Flex 1/2 Inch Liquidtight Conduit"),
            ("MC", "MC Cable Generic", "Southwire", "Southwire 12/2 MC Cable"),
        ]
        products = pd.DataFrame(
            [
                {
                    "BLAZER ID": code,
                    "PRODUCT": f"{code} sample product",
                    "brand_manufacturer": brand,
                    "manufacturer_part_number": "",
                    "clean_title": title,
                    "normalized_specs": "",
                    "short_description": f"{brand} commodity product.",
                    "search_keywords": json.dumps([f"{brand} product"]),
                    "common_misspellings": json.dumps([f"{brand} typo"]),
                }
                for code, _, brand, title in examples
            ]
        )

        result = normalize_enriched_products(products)

        for index, (code, generic_label, prior_brand, _) in enumerate(examples):
            self.assertEqual(result.loc[index, "brand_manufacturer"], generic_label, code)
            brand_word = prior_brand.split()[0].split("-")[0]
            self.assertNotIn(brand_word, result.loc[index, "clean_title"], code)
            self.assertNotIn(brand_word, result.loc[index, "short_description"], code)
        self.assertTrue(result.loc[0, "clean_title"].startswith("1/2 Inch"))

    def test_confirmed_vendor_prefixes_override_conflicting_enrichment(self):
        examples = [
            ("CUL", "Cully", "Cullman", "Cullman drill bit"),
            ("HDN", "Haydon Strut", "Unistrut", "Unistrut strut fitting"),
            ("POR", "Tiffin Insulators", "Porter", "Porter insulator"),
            ("GLA", "Glas Masters", "Glasdon", "Glasdon polymer enclosure"),
            ("VC", "Vertical Cable", "Vanco", "Vanco speaker cable"),
            ("PNS", "Pass & Seymour", "Panasonic", "Panasonic receptacle"),
            ("DIO", "Diode LED", "Diodes Incorporated", "Diodes Incorporated LED jumper"),
            ("RAC", "RACO", "Rack-A-Tiers", "Rack-A-Tiers 4-Inch Square Electrical Box"),
        ]
        products = pd.DataFrame(
            [
                {
                    "BLAZER ID": code,
                    "PRODUCT": f"{code} product description",
                    "brand_manufacturer": prior_brand,
                    "manufacturer_part_number": "PART-1",
                    "clean_title": old_title,
                    "normalized_specs": f"{prior_brand} specification",
                    "short_description": f"{prior_brand} product description.",
                    "search_keywords": json.dumps([f"{prior_brand} accessory"]),
                    "common_misspellings": json.dumps([f"{prior_brand} typo"]),
                }
                for code, _, prior_brand, old_title in examples
            ]
        )

        result = normalize_enriched_products(products)

        for index, (code, manufacturer, prior_brand, _) in enumerate(examples):
            self.assertEqual(result.loc[index, "brand_manufacturer"], manufacturer, code)
            self.assertTrue(
                result.loc[index, "clean_title"].startswith(manufacturer),
                code,
            )
            self.assertNotIn(prior_brand, result.loc[index, "clean_title"], code)
            self.assertEqual(
                result.loc[index, "short_description"],
                f"{manufacturer} product description.",
                code,
            )
            self.assertIn(manufacturer, result.loc[index, "normalized_specs"], code)
            self.assertNotIn(prior_brand, result.loc[index, "search_keywords"], code)


if __name__ == "__main__":
    unittest.main()
