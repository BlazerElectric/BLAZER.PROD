import json
import os
import subprocess
import sys
import tempfile
import unittest

from export_web_catalog import (
    _parse_json_list,
    build_catalog_dict,
    export_catalog,
    load_accessories,
    load_enriched_products,
    load_products,
    load_ranks,
    load_similar,
)


class TestExportWebCatalog(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_parse_json_list(self):
        context = {"sku": "101", "column": "search_keywords"}
        self.assertEqual(_parse_json_list('["a", "b"]', **context), ["a", "b"])
        self.assertEqual(_parse_json_list("", **context), [])
        self.assertEqual(_parse_json_list(None, **context), [])
        self.assertEqual(_parse_json_list('["foo", null, 123]', **context), ["foo", "123"])
        with self.assertRaisesRegex(ValueError, "Invalid JSON list in search_keywords for product 101"):
            _parse_json_list("invalid json", **context)
        with self.assertRaisesRegex(ValueError, "Expected JSON list in search_keywords for product 101"):
            _parse_json_list('{"not": "a list"}', **context)

    def test_load_products(self):
        path = os.path.join(self.temp_dir.name, "products.csv")
        with open(path, "w", encoding="utf-8") as f:
            f.write("BLAZER ID,PRODUCT\n")
            f.write('101,"Item 101 Description with special characters: 1/2"" EMT"\n')
            f.write("102,Item 102 Simple Description\n")

        products = load_products(path)
        self.assertEqual(len(products), 2)
        self.assertEqual(products["101"], 'Item 101 Description with special characters: 1/2" EMT')
        self.assertEqual(products["102"], "Item 102 Simple Description")

    def test_load_enriched_products(self):
        path = os.path.join(self.temp_dir.name, "enriched_products.csv")
        with open(path, "w", encoding="utf-8") as f:
            f.write(
                "BLAZER ID,PRODUCT,brand_manufacturer,manufacturer_part_number,clean_title,"
                "normalized_specs,short_description,primary_category,sub_category,search_keywords,common_misspellings\n"
            )
            f.write(
                '101,Desc,Bridgeport,SB-101,Clean Title,"2 Inch, Plastic",Short Desc,'
                'Fittings,Bushings,"[""kw1"", ""kw2""]","[""err1""]"\n'
            )

        enriched = load_enriched_products(path)
        self.assertIn("101", enriched)
        item = enriched["101"]
        self.assertEqual(item["brand"], "Bridgeport")
        self.assertEqual(item["mpn"], "SB-101")
        self.assertEqual(item["category"], "Fittings")
        self.assertEqual(item["subcategory"], "Bushings")
        self.assertEqual(item["specs"], "2 Inch, Plastic")
        self.assertEqual(item["search_keywords"], ["kw1", "kw2"])
        self.assertEqual(item["common_misspellings"], ["err1"])

    def test_load_ranks_from_order_volume(self):
        path = os.path.join(self.temp_dir.name, "BLAZER.RANK.csv")
        with open(path, "w", encoding="utf-8-sig") as f:
            f.write("PROD_ID,PRODUCT,TIMES ORDERED PAST 6 MONTHS\n")
            f.write("101,Item 101,50\n")
            f.write("102,Item 102,200\n")
            f.write("103,Item 103,150\n")

        ranks = load_ranks(path, fallback=999999)
        # Higher order count = better rank (lower integer number)
        # 102 (200 orders) -> Rank 1
        # 103 (150 orders) -> Rank 2
        # 101 (50 orders) -> Rank 3
        self.assertEqual(ranks["102"], 1)
        self.assertEqual(ranks["103"], 2)
        self.assertEqual(ranks["101"], 3)
        self.assertEqual(ranks.get("999", 999999), 999999)

    def test_load_ranks_with_explicit_rank_column(self):
        path = os.path.join(self.temp_dir.name, "rank_explicit.csv")
        with open(path, "w", encoding="utf-8") as f:
            f.write("BLAZER ID,RANK\n")
            f.write("101,10\n")
            f.write("102,5\n")

        ranks = load_ranks(path)
        self.assertEqual(ranks["101"], 10)
        self.assertEqual(ranks["102"], 5)

    def test_load_accessories(self):
        path = os.path.join(self.temp_dir.name, "product_accessories.csv")
        with open(path, "w", encoding="utf-8") as f:
            f.write(
                "PROD_ID,PROD_ID_REL1,PROD_ID_REL2,PROD_ID_REL3,PROD_ID_REL4,PROD_ID_REL5,"
                "PROD_ID_REL6,PROD_ID_REL7,PROD_ID_REL8,PROD_ID_REL9,PROD_ID_REL10\n"
            )
            f.write("101,882,675,845,,,,,,,\n")

        accessories = load_accessories(path)
        self.assertEqual(accessories["101"], ["882", "675", "845"])

    def test_load_similar(self):
        path = os.path.join(self.temp_dir.name, "similar_products.csv")
        with open(path, "w", encoding="utf-8") as f:
            f.write(
                "PROD_ID,SIMILAR_ID_1,SIMILAR_ID_2,SIMILAR_ID_3,SIMILAR_ID_4,SIMILAR_ID_5,"
                "SIMILAR_ID_6,SIMILAR_ID_7,SIMILAR_ID_8,SIMILAR_ID_9,SIMILAR_ID_10\n"
            )
            f.write("101,684,845,69673,,,,,,,\n")

        similar = load_similar(path)
        self.assertEqual(similar["101"], ["684", "845", "69673"])

    def test_missing_carousels_fail_without_replacing_output_or_allow_partial_export(self):
        prod_path = os.path.join(self.temp_dir.name, "products.csv")
        enr_path = os.path.join(self.temp_dir.name, "enriched.csv")
        acc_path = os.path.join(self.temp_dir.name, "accessories.csv")
        sim_path = os.path.join(self.temp_dir.name, "similar.csv")
        out_path = os.path.join(self.temp_dir.name, "catalog.json")
        with open(prod_path, "w", encoding="utf-8") as f:
            f.write("BLAZER ID,PRODUCT\n101,Item\n")
        with open(enr_path, "w", encoding="utf-8") as f:
            f.write("BLAZER ID\n101\n")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("existing catalog")

        paths = {
            "products_path": prod_path, "enriched_path": enr_path,
            "rank_path": os.path.join(self.temp_dir.name, "rank.csv"),
            "accessories_path": acc_path, "similar_path": sim_path,
            "output_path": out_path,
        }
        for missing, present, contents in (
            (acc_path, sim_path, "PROD_ID,SIMILAR_ID_1\n101,202\n"),
            (sim_path, acc_path, "PROD_ID,PROD_ID_REL1\n101,303\n"),
        ):
            with self.subTest(missing=missing):
                with open(present, "w", encoding="utf-8") as f:
                    f.write(contents)
                with self.assertRaisesRegex(FileNotFoundError, os.path.basename(missing)):
                    export_catalog(**paths)
                with open(out_path, encoding="utf-8") as f:
                    self.assertEqual(f.read(), "existing catalog")
                with self.assertLogs("export_web_catalog", level="WARNING") as logs:
                    export_catalog(**paths, allow_partial=True)
                self.assertTrue(any(missing in message for message in logs.output))
                with open(out_path, encoding="utf-8") as f:
                    item = json.load(f)["products"]["101"]
                if missing == acc_path:
                    self.assertEqual(item["accessories"], [])
                    self.assertEqual(item["similar"], ["202"])
                else:
                    self.assertEqual(item["accessories"], ["303"])
                    self.assertEqual(item["similar"], [])
                os.remove(present)
                with open(out_path, "w", encoding="utf-8") as f:
                    f.write("existing catalog")

    def test_cli_partial_export_requires_explicit_flag(self):
        products = os.path.join(self.temp_dir.name, "products.csv")
        enriched = os.path.join(self.temp_dir.name, "enriched.csv")
        output = os.path.join(self.temp_dir.name, "catalog.json")
        with open(products, "w", encoding="utf-8") as f:
            f.write("BLAZER ID,PRODUCT\n101,Item\n")
        with open(enriched, "w", encoding="utf-8") as f:
            f.write("BLAZER ID\n101\n")
        script = os.path.join(os.path.dirname(os.path.dirname(__file__)), "export_web_catalog.py")
        command = [
            sys.executable, script, "--products", products, "--enriched", enriched,
            "--accessories", os.path.join(self.temp_dir.name, "missing-accessories.csv"),
            "--similar", os.path.join(self.temp_dir.name, "missing-similar.csv"),
            "--rank", os.path.join(self.temp_dir.name, "missing-rank.csv"), "--output", output,
        ]
        blocked = subprocess.run(command, capture_output=True, text=True)
        self.assertNotEqual(blocked.returncode, 0)
        self.assertFalse(os.path.exists(output))
        allowed = subprocess.run([*command, "--allow-partial"], capture_output=True, text=True)
        self.assertEqual(allowed.returncode, 0, allowed.stderr)
        self.assertIn("will be empty", allowed.stderr)
        with open(output, encoding="utf-8") as f:
            item = json.load(f)["products"]["101"]
        self.assertEqual(item["accessories"], [])
        self.assertEqual(item["similar"], [])

    def test_full_export_and_schema(self):
        prod_path = os.path.join(self.temp_dir.name, "products.csv")
        enr_path = os.path.join(self.temp_dir.name, "enriched_products.csv")
        rank_path = os.path.join(self.temp_dir.name, "BLAZER.RANK.csv")
        acc_path = os.path.join(self.temp_dir.name, "product_accessories.csv")
        sim_path = os.path.join(self.temp_dir.name, "similar_products.csv")
        out_path = os.path.join(self.temp_dir.name, "blazer_web_catalog.json")

        with open(prod_path, "w", encoding="utf-8") as f:
            f.write("BLAZER ID,PRODUCT\n")
            f.write("100184,BPT SB-386NM 2IN PLASTIC SPILT BUSHING\n")
            f.write("999999,UNRANKED ITEM\n")

        with open(enr_path, "w", encoding="utf-8") as f:
            f.write(
                "BLAZER ID,PRODUCT,brand_manufacturer,manufacturer_part_number,clean_title,"
                "normalized_specs,short_description,primary_category,sub_category,search_keywords,common_misspellings\n"
            )
            f.write(
                '100184,Desc,Bridgeport Fittings,SB-386NM,Clean Title,"2 Inch (2.00 in), Plastic",Short Desc,'
                'Fittings,Bushing / K.O. Seal / Locknuts,"[""split bushing""]","[""spilt bushing""]"\n'
            )

        with open(rank_path, "w", encoding="utf-8") as f:
            f.write("PROD_ID,PRODUCT,TIMES ORDERED PAST 6 MONTHS\n")
            f.write("100184,BPT SB-386NM,142\n")

        with open(acc_path, "w", encoding="utf-8") as f:
            f.write("PROD_ID,PROD_ID_REL1,PROD_ID_REL2\n")
            f.write("100184,882,675\n")

        with open(sim_path, "w", encoding="utf-8") as f:
            f.write("PROD_ID,SIMILAR_ID_1,SIMILAR_ID_2\n")
            f.write("100184,684,845\n")

        res_path = export_catalog(
            products_path=prod_path,
            enriched_path=enr_path,
            rank_path=rank_path,
            accessories_path=acc_path,
            similar_path=sim_path,
            output_path=out_path,
        )
        self.assertEqual(res_path, out_path)
        self.assertTrue(os.path.exists(out_path))

        with open(out_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.assertIn("last_updated", data)
        self.assertEqual(data["total_products"], 2)
        self.assertIn("100184", data["products"])
        self.assertIn("999999", data["products"])

        item = data["products"]["100184"]
        self.assertEqual(item["sku"], "100184")
        self.assertEqual(item["title"], "BPT SB-386NM 2IN PLASTIC SPILT BUSHING")
        self.assertEqual(item["brand"], "Bridgeport Fittings")
        self.assertEqual(item["mpn"], "SB-386NM")
        self.assertEqual(item["category"], "Fittings")
        self.assertEqual(item["subcategory"], "Bushing / K.O. Seal / Locknuts")
        self.assertEqual(item["specs"], "2 Inch (2.00 in), Plastic")
        self.assertEqual(item["rank"], 1)
        self.assertEqual(item["search_keywords"], ["split bushing"])
        self.assertEqual(item["common_misspellings"], ["spilt bushing"])
        self.assertEqual(item["accessories"], ["882", "675"])
        self.assertEqual(item["similar"], ["684", "845"])

        unranked_item = data["products"]["999999"]
        self.assertEqual(unranked_item["rank"], 999999)
        self.assertEqual(unranked_item["accessories"], [])
        self.assertEqual(unranked_item["similar"], [])


if __name__ == "__main__":
    unittest.main()
