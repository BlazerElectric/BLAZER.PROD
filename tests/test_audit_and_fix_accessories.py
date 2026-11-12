import unittest

from audit_and_fix_accessories import _device_staple_ids, _is_device_wire_staple


class DeviceStapleTests(unittest.TestCase):
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
