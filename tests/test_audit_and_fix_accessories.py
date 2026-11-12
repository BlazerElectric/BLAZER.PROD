import csv
import os
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

import audit_and_fix_accessories as audit
from audit_and_fix_accessories import _device_staple_ids, _is_device_wire_staple


class DeviceStapleTests(unittest.TestCase):
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
