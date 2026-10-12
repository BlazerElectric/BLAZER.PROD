import unittest

import pandas as pd

from generate_accessories import (
    _candidate_indexes,
    _matching_groups,
    build_candidates,
    top_up_selection,
    trade_sizes,
)


class CandidateEligibilityTests(unittest.TestCase):
    def test_unsupported_target_does_not_fall_back_to_primary_category(self):
        target = {
            "BLAZER ID": "target",
            "PRODUCT": "GFCI receptacle",
            "clean_title": "GFCI Receptacle",
            "normalized_specs": "15A, 125V",
            "primary_category": "Wiring Devices",
            "sub_category": "GFCI Receptacles",
        }
        source = pd.DataFrame(
            [
                target,
                {
                    **target,
                    "BLAZER ID": "wire-connector",
                    "PRODUCT": "Wire connector",
                    "clean_title": "Wire connector",
                    "sub_category": "Wire Connectors",
                },
                {
                    **target,
                    "BLAZER ID": "box-shim",
                    "PRODUCT": "Box spacer shim",
                    "clean_title": "Electrical box spacer shim",
                    "sub_category": "Electrical Box Accessories",
                },
            ]
        )

        self.assertEqual(_matching_groups(target), [])
        self.assertEqual(build_candidates(target, _candidate_indexes(source)), [])

    def test_conduit_target_only_gets_explicit_conduit_accessory_categories(self):
        target = {
            "BLAZER ID": "target",
            "PRODUCT": "Rigid conduit",
            "clean_title": "Rigid Conduit",
            "normalized_specs": "1 inch",
            "primary_category": "Conduit & Fittings",
            "sub_category": "Rigid Conduit",
        }
        source = pd.DataFrame(
            [
                target,
                {
                    **target,
                    "BLAZER ID": "conduit-fitting",
                    "PRODUCT": "Conduit connector",
                    "clean_title": "Conduit Connector",
                    "sub_category": "Conduit Connectors",
                },
                {
                    **target,
                    "BLAZER ID": "wire-connector",
                    "PRODUCT": "Wire connector",
                    "clean_title": "Wire Connector",
                    "sub_category": "Wire Connectors",
                },
                {
                    **target,
                    "BLAZER ID": "box-shim",
                    "PRODUCT": "Box spacer shim",
                    "clean_title": "Electrical Box Spacer Shim",
                    "sub_category": "Electrical Box Accessories",
                },
            ]
        )

        candidates = build_candidates(target, _candidate_indexes(source))

        self.assertEqual([candidate["BLAZER ID"] for candidate in candidates], ["conduit-fitting"])

    def test_primary_category_alone_does_not_activate_a_relationship(self):
        row = {
            "BLAZER ID": "target",
            "PRODUCT": "Accessory",
            "clean_title": "Accessory",
            "normalized_specs": "",
            "primary_category": "Conduit & Fittings",
            "sub_category": "Other",
        }

        self.assertEqual(_matching_groups(row), [])

    def test_large_pools_are_balanced_across_accessory_subcategories(self):
        target = {
            "BLAZER ID": "target",
            "PRODUCT": "Rigid conduit",
            "clean_title": "Rigid Conduit",
            "normalized_specs": "1 inch",
            "primary_category": "Conduit & Fittings",
            "sub_category": "Rigid Conduit",
        }
        rows = [target]
        rows.extend(
            {
                **target,
                "BLAZER ID": f"connector-{index:03}",
                "PRODUCT": "Conduit connector",
                "clean_title": "Conduit Connector",
                "sub_category": "Conduit Connectors",
            }
            for index in range(151)
        )
        rows.append(
            {
                **target,
                "BLAZER ID": "adapter",
                "PRODUCT": "Conduit adapter",
                "clean_title": "Conduit Adapter",
                "sub_category": "Conduit Adapters",
            }
        )

        candidates = build_candidates(target, _candidate_indexes(pd.DataFrame(rows)))

        self.assertEqual(len(candidates), 150)
        self.assertIn("adapter", {candidate["BLAZER ID"] for candidate in candidates})


class TradeSizeTests(unittest.TestCase):
    def test_parses_fractions_mixed_numbers_and_decimals(self):
        row = {"normalized_specs": "1-1/2 Inch (1.50 in), 3/4 Inch, 2 Inch", "clean_title": ""}
        self.assertEqual(trade_sizes(row), [1.5, 1.5, 0.75, 2.0])

    def test_conduit_candidates_must_match_trade_size(self):
        base = {"PRODUCT": "", "primary_category": "Conduit & Fittings"}
        target = {
            **base,
            "BLAZER ID": "t",
            "clean_title": "Rigid Conduit",
            "normalized_specs": "3/4 Inch (0.75 in) Trade Size",
            "sub_category": "Rigid Conduit",
        }
        rows = [
            target,
            {**base, "BLAZER ID": "same", "clean_title": "Connector",
             "normalized_specs": "3/4 Inch (0.75 in)", "sub_category": "Conduit Connectors"},
            {**base, "BLAZER ID": "other", "clean_title": "Connector",
             "normalized_specs": "2 Inch (2.00 in)", "sub_category": "Conduit Connectors"},
            {**base, "BLAZER ID": "nosize", "clean_title": "Duct seal",
             "normalized_specs": "1 lb", "sub_category": "Duct Seal & Putty"},
        ]
        ids = {c["BLAZER ID"] for c in build_candidates(target, _candidate_indexes(pd.DataFrame(rows)))}
        self.assertEqual(ids, {"same", "nosize"})


    def test_fittings_group_drops_unsized_candidates(self):
        base = {"PRODUCT": "", "primary_category": "Conduit & Fittings"}
        target = {**base, "BLAZER ID": "t", "clean_title": "EMT Connector",
                  "normalized_specs": "3/4 Inch (0.75 in)", "sub_category": "EMT Fittings"}
        rows = [
            target,
            {**base, "BLAZER ID": "locknut", "clean_title": "Locknut",
             "normalized_specs": "3/4 Inch (0.75 in)", "sub_category": "Bushings & Locknuts"},
            {**base, "BLAZER ID": "stud", "clean_title": "Stud Bushing",
             "normalized_specs": "Metal, 100 Pack", "sub_category": "Bushings & Locknuts"},
        ]
        ids = {c["BLAZER ID"] for c in build_candidates(target, _candidate_indexes(pd.DataFrame(rows)))}
        self.assertEqual(ids, {"locknut"})


    def test_wallplates_require_same_color_but_not_brand(self):
        def row(id_, title, path, brand):
            return {
                "BLAZER ID": id_, "PRODUCT": title, "clean_title": title,
                "normalized_specs": "", "primary_category": "", "sub_category": "x",
                "brand_manufacturer": brand, "tax_path": path,
            }

        target = row("r", "Duplex Receptacle White", "Wiring Devices > Receptacles", "Leviton")
        plates = [
            row("ok", "1-Gang Wall Plate White", "Wiring Devices > Wallplates", "Leviton"),
            row("color", "1-Gang Wall Plate Black", "Wiring Devices > Wallplates", "Leviton"),
            row("brand", "1-Gang Wall Plate White", "Wiring Devices > Wallplates", "Hubbell"),
        ]
        by_group = _candidate_indexes(pd.DataFrame([target, *plates]))
        ids = {c["BLAZER ID"] for c in build_candidates(target, by_group)}
        self.assertEqual(ids, {"ok", "brand"})


if __name__ == "__main__":
    unittest.main()


class MatchHelperTests(unittest.TestCase):
    def test_fastener_diameter_and_amps(self):
        from generate_accessories import amp_ratings, fastener_diameter

        row = {"PRODUCT": "", "clean_title": "Cully 1/4-20 x 2 Inch Machine Screw"}
        self.assertEqual(fastener_diameter(row), "1/4")
        self.assertEqual(fastener_diameter({"PRODUCT": "#8 x 1 screw", "clean_title": ""}), "#8")
        self.assertEqual(amp_ratings({"PRODUCT": "30 Amp Fuse", "clean_title": ""}), {30})

    def test_fallback_fills_from_own_node_and_skips_low_orders(self):
        base = {
            "PRODUCT": "x",
            "normalized_specs": "",
            "primary_category": "Tools",
            "sub_category": "Pliers",
            "orders_6m": 10,
        }
        rows = [
            {**base, "BLAZER ID": str(i), "clean_title": f"Pliers {i}",
             "orders_6m": 0 if i == 3 else 10}
            for i in range(8)
        ]
        by_group = _candidate_indexes(pd.DataFrame(rows))
        ids = {c["BLAZER ID"] for c in build_candidates(rows[0], by_group)}
        self.assertEqual(ids, {"1", "2", "4", "5", "6", "7"})


class JobKitTests(unittest.TestCase):
    def test_kit_loader_and_slot_matching(self):
        import os
        import tempfile

        from generate_accessories import _slot_matches, load_job_kits

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "kits.csv")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("job,slot,path,keywords,max\n")
                handle.write("EV,breaker,Power Distribution,50A,2\n")
                handle.write("EV,cover,,in.?use,\n")
            kits = load_job_kits(path)
        slots = {s.slot: s for s in kits["EV"]}
        self.assertEqual(slots["cover"].per_slot, 3)
        self.assertEqual(slots["breaker"].per_slot, 2)
        row = {"PRODUCT": "BR250 50A", "clean_title": "", "tax_path": "Power Distribution > Circuit Breakers"}
        self.assertTrue(_slot_matches(slots["breaker"], row))
        row["tax_path"] = "Tools & Meters > Hand Tools"
        self.assertFalse(_slot_matches(slots["breaker"], row))


class HardConflictTests(unittest.TestCase):
    def _row(self, product, path):
        return {"PRODUCT": product, "clean_title": "", "normalized_specs": "", "tax_path": path}

    def test_gang_mismatch_is_dropped(self):
        from generate_accessories import hard_conflict

        box = self._row("ALL P-240OW OLD WK 2G BOX", "Boxes & Enclosures > Boxes - Non-Metallic")
        plate = self._row("PNS TP8-W 1 Gang WALLPLATE WHITE", "Wiring Devices > Wallplates")
        self.assertEqual(hard_conflict(box, plate), "gang")

    def test_conduit_material_and_size_conflicts(self):
        from generate_accessories import hard_conflict

        emt = self._row("CON EMT 3/4-EMT * CONDUIT", "Conduit, Strut & Raceways > Conduit - Metallic")
        pvc_fit = self._row("TB 3/4 PVC COUPLING", "Fittings > PVC Fittings")
        emt_fit = self._row("AMF 3/4 EMT COMP CONN", "Fittings > EMT Fittings")
        big_fit = self._row("AMF EC752US USA 1 EMT COMP CONN", "Fittings > EMT Fittings")
        self.assertEqual(hard_conflict(emt, pvc_fit), "material")
        self.assertEqual(hard_conflict(emt, big_fit), "size")
        self.assertEqual(hard_conflict(emt, emt_fit), "")

    def test_color_mismatch_ignores_plugs(self):
        from generate_accessories import hard_conflict

        gfci = self._row("PNS GFCI IVORY", "Wiring Devices > GFCI Personnel Protection Devices")
        switch = self._row("PNS SW WHITE", "Wiring Devices > Switches")
        plug = self._row("HUB PLUG BLACK", "Wiring Devices > Straight Blade 10-20 Amp")
        self.assertEqual(hard_conflict(gfci, switch), "color")
        self.assertEqual(hard_conflict(gfci, plug), "")


class TopUpTests(unittest.TestCase):
    def test_short_selection_is_filled_from_rule_candidates_only(self):
        base = {
            "PRODUCT": "x",
            "normalized_specs": "",
            "primary_category": "Tools",
            "sub_category": "Pliers",
            "orders_6m": 10,
        }
        rows = [
            {**base, "BLAZER ID": str(i), "clean_title": f"Pliers {i}"}
            for i in range(8)
        ]
        by_group = _candidate_indexes(pd.DataFrame(rows))
        # Same-node products are fallback filler, not rule matches.
        self.assertEqual(top_up_selection(rows[0], by_group, ["1"]), ["1"])
        full = ["1", "2", "3", "4", "5"]
        self.assertEqual(top_up_selection(rows[0], by_group, full), full)
