import csv
import os
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

import audit_and_fix_accessories as audit
from audit_and_fix_accessories import _device_staple_ids, _is_device_wire_staple


class DeviceStapleTests(unittest.TestCase):
    def test_essentials_replace_full_row_filler_without_changing_locked_packages(self):
        base = {"clean_title": "", "normalized_specs": ""}
        target = {**base, "BLAZER ID": "target", "PRODUCT": "ARL 38AST AC/MC CONNECTOR",
                  "tax_path": "Fittings > MC, NMB, Liquidtight, Flex Fittings"}
        locked = {**target, "BLAZER ID": "locked"}
        box = {**base, "BLAZER ID": "box", "PRODUCT": "4 Inch Square Box, 1/2 Inch Knockout",
               "tax_path": "Boxes & Enclosures > Boxes - Metallic"}
        wire = {**base, "BLAZER ID": "wire", "PRODUCT": "12/2 MC Cable",
                "tax_path": "Wire, Cable & Cord > Armored Cable"}
        tapes = [{**base, "BLAZER ID": f"tape{i}", "PRODUCT": "Vinyl Electrical Tape",
                  "tax_path": "Fasteners & Hardware > Tapes"} for i in range(10)]
        source = pd.DataFrame([target, locked, box, wire, *tapes])
        locked_row = {"PROD_ID": "locked", **dict.fromkeys(audit.RELATION_COLS, ""),
                      "PROD_ID_REL1": "tape0"}
        with tempfile.TemporaryDirectory() as directory:
            output = os.path.join(directory, "accessories.csv")
            with open(output, "w", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["PROD_ID", *audit.RELATION_COLS])
                writer.writeheader()
                writer.writerow({"PROD_ID": "target", **dict(zip(audit.RELATION_COLS, [t["BLAZER ID"] for t in tapes]))})
                writer.writerow(locked_row)
            with (
                patch.object(audit, "OUTPUT_FILE", output),
                patch.object(audit.pd, "read_csv", return_value=source),
                patch.object(audit.ga, "attach_taxonomy", return_value=source),
                patch.object(audit.ga, "attach_orders", return_value=source),
                patch.object(audit.ga, "_candidate_indexes", return_value={}),
                patch.object(audit.ga, "build_candidates", return_value=[box, wire]),
                patch.object(audit.ga, "load_curated_accessories", return_value=pd.DataFrame([locked_row])),
                patch.object(audit.export_web_catalog, "export_catalog"),
            ):
                audit.main()
                with open(output, "rb") as handle:
                    first = handle.read()
                audit.main()
                with open(output, "rb") as handle:
                    self.assertEqual(first, handle.read())
            with open(output, newline="") as handle:
                rows = list(csv.DictReader(handle))
        self.assertEqual([rows[0]["PROD_ID_REL1"], rows[0]["PROD_ID_REL2"]], ["wire", "box"])
        self.assertEqual(rows[1], locked_row)

    def test_twist_on_connectors_are_counted_as_wire_nuts(self):
        row = {"PRODUCT": "3M SUPER TAN CONNECTOR", "clean_title": "Wire Connector",
               "normalized_specs": "Tan/Red, Winged, Twist-On", "tax_path": "Connectors & Crimps > Wire Connectors"}
        self.assertTrue(audit._is_wire_nut(row))

    def test_universal_fallback_respects_box_and_wire_nut_limits(self):
        source = pd.DataFrame([
            {"BLAZER ID": "target", "PRODUCT": "20A DUPLEX RECEPTACLE",
             "clean_title": "", "tax_path": "Wiring Devices > Receptacles"},
            {"BLAZER ID": "box1", "PRODUCT": "1 GANG DEVICE BOX",
             "clean_title": "", "tax_path": "Boxes & Enclosures > Boxes - Non-Metallic"},
            {"BLAZER ID": "box2", "PRODUCT": "1 GANG PLASTIC DEVICE BOX",
             "clean_title": "", "tax_path": "Boxes & Enclosures > Boxes - Non-Metallic"},
            {"BLAZER ID": "nut", "PRODUCT": "YELLOW WIRE NUT",
             "clean_title": "", "tax_path": "Connectors & Crimps > Wire Connectors"},
            {"BLAZER ID": "2951", "PRODUCT": "YELLOW WING NUT",
             "clean_title": "", "tax_path": "Connectors & Crimps > Wire Connectors"},
            {"BLAZER ID": "3223", "PRODUCT": "4 SQUARE BOX",
             "clean_title": "", "tax_path": "Boxes & Enclosures > Boxes - Metallic"},
        ])
        with tempfile.TemporaryDirectory() as directory:
            output = os.path.join(directory, "accessories.csv")
            with open(output, "w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=["PROD_ID", *audit.RELATION_COLS])
                writer.writeheader()
                writer.writerow({"PROD_ID": "target", "PROD_ID_REL1": "box1",
                                 "PROD_ID_REL2": "box2", "PROD_ID_REL3": "nut"})
            with (
                patch.object(audit, "OUTPUT_FILE", output),
                patch.object(audit.pd, "read_csv", return_value=source),
                patch.object(audit.ga, "attach_taxonomy", return_value=source),
                patch.object(audit.ga, "attach_orders", return_value=source),
                patch.object(audit.ga, "_candidate_indexes", return_value={}),
                patch.object(audit.ga, "build_candidates", return_value=[]),
                patch.object(audit.ga, "load_curated_accessories", return_value=pd.DataFrame()),
                patch.object(audit.export_web_catalog, "export_catalog"),
            ):
                audit.main()
            with open(output, newline="", encoding="utf-8") as handle:
                recommendations = next(csv.DictReader(handle))

        self.assertNotIn("3223", recommendations.values())
        self.assertNotIn("2951", recommendations.values())

    def test_missing_curated_references_are_reported_for_active_products(self):
        curated = pd.DataFrame([
            {"PROD_ID": "active", **dict.fromkeys(audit.RELATION_COLS, ""), "PROD_ID_REL1": "missing"},
            {"PROD_ID": "dropped", **dict.fromkeys(audit.RELATION_COLS, ""), "PROD_ID_REL1": "missing"},
        ])
        with self.assertRaisesRegex(ValueError, "active.*PROD_ID_REL1.*missing"):
            audit.ga.validate_curated_accessories(curated, {"active"})

        audit.ga.validate_curated_accessories(curated, {"other"})

    def test_audit_does_not_replace_output_when_curated_reference_is_missing(self):
        source = pd.DataFrame([{"BLAZER ID": "active", "PRODUCT": "Device", "tax_path": "Wiring Devices > Receptacles"}])
        curated = pd.DataFrame([
            {"PROD_ID": "active", **dict.fromkeys(audit.RELATION_COLS, ""), "PROD_ID_REL1": "missing"},
        ])
        with tempfile.TemporaryDirectory() as directory:
            output = os.path.join(directory, "accessories.csv")
            with open(output, "w", encoding="utf-8") as handle:
                handle.write("existing output")
            with (
                patch.object(audit, "OUTPUT_FILE", output),
                patch.object(audit.pd, "read_csv", return_value=source),
                patch.object(audit.ga, "attach_taxonomy", return_value=source),
                patch.object(audit.ga, "attach_orders", return_value=source),
                patch.object(audit.ga, "_candidate_indexes", return_value={}),
                patch.object(audit.ga, "load_curated_accessories", return_value=curated),
                patch.object(audit.export_web_catalog, "export_catalog") as export,
            ):
                with self.assertRaisesRegex(ValueError, "active.*missing"):
                    audit.main()
                export.assert_not_called()
            with open(output, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "existing output")

    def test_audit_fallback_for_20a_receptacle_does_not_add_14_2(self):
        source = pd.DataFrame([
            {
                "BLAZER ID": "12834",
                "PRODUCT": "HUB CR20WHITR TAMP RES, COM GRD, 20A 125V",
                "clean_title": "Hubbell 20 Amp Duplex Receptacle White",
                "tax_path": "Wiring Devices > Receptacles",
            },
            {
                "BLAZER ID": "1021", "PRODUCT": "COP NM-B-12/2-CU-WG",
                "clean_title": "12/2 NM-B Copper Wire", "tax_path": "Wire & Cable > NM Cable",
            },
            {
                "BLAZER ID": "1026", "PRODUCT": "COP NM-B-14/2-CU-WG",
                "clean_title": "14/2 NM-B Copper Wire", "tax_path": "Wire & Cable > NM Cable",
            },
        ])
        with tempfile.TemporaryDirectory() as directory:
            output = os.path.join(directory, "accessories.csv")
            with open(output, "w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=["PROD_ID", *audit.RELATION_COLS])
                writer.writeheader()
                writer.writerow({"PROD_ID": "12834"})
            with (
                patch.object(audit, "OUTPUT_FILE", output),
                patch.object(audit.pd, "read_csv", return_value=source),
                patch.object(audit.ga, "attach_taxonomy", return_value=source),
                patch.object(audit.ga, "attach_orders", return_value=source),
                patch.object(audit.ga, "_candidate_indexes", return_value={}),
                patch.object(audit.ga, "build_candidates", return_value=[]),
                patch.object(audit.ga, "load_curated_accessories", return_value=pd.DataFrame()),
                patch.object(audit.export_web_catalog, "export_catalog"),
            ):
                audit.main()
            with open(output, newline="", encoding="utf-8") as handle:
                recommendations = next(csv.DictReader(handle))

        self.assertIn("1021", recommendations.values())
        self.assertNotIn("1026", recommendations.values())

    def test_20a_devices_get_only_12_2_wire(self):
        staple_ids = _device_staple_ids(is_20a=True)

        self.assertIn("1021", staple_ids)
        self.assertNotIn("1026", staple_ids)

    def test_other_devices_get_only_14_2_wire(self):
        staple_ids = _device_staple_ids(is_20a=False)

        self.assertIn("1026", staple_ids)
        self.assertNotIn("1021", staple_ids)

    def test_universal_wire_staples_are_skipped_for_wiring_devices(self):
        for product_id in ("1021", "1026"):
            self.assertTrue(_is_device_wire_staple("Wiring Devices > Receptacles", product_id))
            self.assertFalse(_is_device_wire_staple("Power Distribution > Circuit Breakers", product_id))


if __name__ == "__main__":
    unittest.main()
