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
    def test_verified_cover_fit_is_available_even_without_orders(self):
        base = {"clean_title": "", "normalized_specs": "", "primary_category": "",
                "sub_category": "Floor Boxes", "tax_path": "Boxes & Enclosures > Floor Boxes",
                "orders_6m": 0}
        box = {**base, "BLAZER ID": "box", "PRODUCT": "ARL FLBC8500 GANGABLE CONCRETE BOX"}
        cover = {**base, "BLAZER ID": "cover", "PRODUCT": "ARL FLBC8510GY SINGLE GANG GRAY FLOOR"}
        unrelated = {**base, "BLAZER ID": "other", "PRODUCT": "ARL OTHER FLOOR BOX"}
        self.assertEqual(
            [row["BLAZER ID"] for row in build_candidates(box, _candidate_indexes(pd.DataFrame([box, cover, unrelated])))],
            ["cover"],
        )
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

    def test_box_connector_fit_uses_knockouts_not_box_dimensions(self):
        from generate_accessories import hard_conflict

        box = self._row("4 Inch Square Box, 2-1/8 Inch Deep, 1/2 Inch and 3/4 Inch Knockouts",
                        "Boxes & Enclosures > Boxes - Metallic")
        for size, expected in (("1", "knockout_size"), ("1/2", "")):
            connector = self._row(f"{size} Inch Flex Connector", "Fittings > MC, NMB, Liquidtight, Flex Fittings")
            with self.subTest(size=size):
                self.assertEqual(hard_conflict(box, connector), expected)
                self.assertEqual(hard_conflict(connector, box), expected)
        snap = self._row("ARL 38AST 3/8 Inch Snap-In AC/MC Connector", "Fittings > MC, NMB, Liquidtight, Flex Fittings")
        snap["normalized_specs"] = "3/8 Inch Trade Size, 1/2 Inch Knockout"
        self.assertEqual(hard_conflict(box, snap), "")

    def test_blank_covers_reject_devices_even_with_unknown_cutout(self):
        from generate_accessories import hard_conflict

        device = self._row("USB CHARGER OUTLET", "Wiring Devices > Receptacles")
        for path in ("Wiring Devices > Wallplates", "Boxes & Enclosures > Boxes - Weatherproof"):
            plate = self._row("1 Gang Blank Cover", path)
            with self.subTest(path=path):
                self.assertEqual(hard_conflict(plate, device), "cutout_mismatch")
                self.assertEqual(hard_conflict(device, plate), "cutout_mismatch")

    def test_decora_plate_rejects_four_way_toggle_model(self):
        from generate_accessories import hard_conflict

        plate = self._row("2 Gang Decora Wallplate", "Wiring Devices > Wallplates")
        toggle = self._row("PNS 664-WG SW WHT 4W 15A", "Wiring Devices > Switches")
        self.assertEqual(hard_conflict(plate, toggle), "cutout_mismatch")

    def test_square_cover_uses_cover_screw_thread(self):
        from generate_accessories import hard_conflict

        cover = self._row("4 Inch Square Blank Cover", "Boxes & Enclosures > Boxes - Metallic")
        for thread, expected in (("8-32", ""), ("6-32", "cover_thread"), ("1/4-20", "cover_thread")):
            screw = self._row(f"{thread} RHMS MACHINE SCREW", "Fasteners & Hardware > Screws")
            with self.subTest(thread=thread):
                self.assertEqual(hard_conflict(cover, screw), expected)

    def test_ground_rod_requires_a_rod_clamp_with_matching_diameter(self):
        from generate_accessories import hard_conflict

        rod = self._row("5/8 Inch x 8 Foot Ground Rod", "Connectors & Crimps > Grounding Products")
        for text, expected in (
            ("5/8 Inch Ground Rod Clamp", ""),
            ("3/4 Inch Ground Rod Clamp", "rod_diameter"),
            ("5/8 Inch Water Pipe Ground Clamp", "rod_clamp"),
        ):
            clamp = self._row(text, "Connectors & Crimps > Grounding Products")
            with self.subTest(text=text):
                self.assertEqual(hard_conflict(rod, clamp), expected)

    def test_shallow_strut_rejects_regular_spring_nuts(self):
        from generate_accessories import hard_conflict

        channel = self._row("HDN H-164-OSx10PG SHALLOW CHANNEL", "Conduit, Strut & Raceways > Strut")
        regular = self._row("HDN N-820 1/4 SPRING NUT", "Conduit, Strut & Raceways > Strut")
        short = self._row("HDN N-810 1/4 SHORT SPRING NUT", "Conduit, Strut & Raceways > Strut")
        self.assertEqual(hard_conflict(channel, regular), "strut_spring")
        self.assertEqual(hard_conflict(channel, short), "")

    def test_nm_cable_does_not_accept_ac_mc_connector(self):
        from generate_accessories import hard_conflict

        cable = self._row("14/2 NM-B CABLE", "Wire, Cable & Cord > Building Wire")
        connector = self._row("ARL 38AST SNAP-IN AC/MC CONNECTOR", "Fittings > MC, NMB, Liquidtight, Flex Fittings")
        self.assertEqual(hard_conflict(cable, connector), "cable_type")

    def test_threaded_lb_requires_an_emt_transition(self):
        from generate_accessories import hard_conflict

        body = self._row("3/4 Inch LB CONDUIT BODY COMPLETE THREADED", "Fittings > Conduit Bodies")
        emt = self._row("3/4 Inch EMT CONDUIT", "Conduit, Strut & Raceways > Conduit - Metallic")
        transition = self._row("3/4 Inch EMT CONNECTOR NPT THREAD", "Fittings > EMT Fittings")
        self.assertEqual(hard_conflict(body, emt), "threaded_transition")
        self.assertEqual(hard_conflict(body, transition), "")

    def test_runtime_taxonomy_keeps_two_level_candidate_nodes_after_detailed_override(self):
        from generate_accessories import _taxonomy_paths
        from unittest.mock import patch

        products = pd.DataFrame([{"BLAZER ID": "cover", "PRODUCT": "RAC 752 COVER", "clean_title": ""}])
        with patch("generate_accessories._raw_taxonomy_paths", return_value=pd.Series(["Boxes & Enclosures > Boxes - Metallic"])):
            self.assertEqual(_taxonomy_paths(products, "unused").tolist(), ["Boxes & Enclosures > Boxes - Metallic"])

    def test_mapper_does_not_reclassify_mc_clips_as_cable(self):
        import contextlib
        import io
        import os
        import tempfile
        from unittest.mock import patch
        import map_taxonomy

        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, "products.csv")
            categories = os.path.join(directory, "categories.csv")
            output = os.path.join(directory, "taxonomy.csv")
            pd.DataFrame([{
                "BLAZER ID": "clip", "PRODUCT": "MIN 121 JIFFY CLIP 12/2 MC CABLE",
                "clean_title": "Minerallac Jiffy Clip for MC Cable",
                "primary_category": "Fittings", "sub_category": "Clips, Clamps, Hangers",
            }]).to_csv(source, index=False)
            pd.DataFrame([
                ["Fittings", "Clips, Clamps, Hangers", "", "", ""],
                ["Wire, Cable & Cord", "Armored Cable", "", "", ""],
            ], columns=map_taxonomy.LEVELS).to_csv(categories, index=False)
            with patch("sys.argv", ["map_taxonomy", "--input", source, "--taxonomy", categories, "--output", output]):
                with contextlib.redirect_stdout(io.StringIO()):
                    map_taxonomy.main()
            result = pd.read_csv(output, keep_default_na=False)
            self.assertEqual(result.loc[0, "Main Category"], "Fittings")
            self.assertEqual(result.loc[0, "Subcategory 1"], "Clips, Clamps, Hangers")

    def test_bushing_substitutes_belong_in_similar_not_accessories(self):
        from generate_accessories import hard_conflict

        target = self._row("2 Inch Plastic Bushing", "Fittings > Bushing / K.O. Seal / Locknuts")
        substitute = self._row("2 Inch Grounding Bushing", "Fittings > Bushing / K.O. Seal / Locknuts")
        locknut = self._row("2 Inch Locknut", "Fittings > Bushing / K.O. Seal / Locknuts")
        self.assertEqual(hard_conflict(target, substitute), "bushing_substitute")
        self.assertEqual(hard_conflict(target, locknut), "")

    def test_cover_alternatives_are_not_installation_accessories(self):
        from generate_accessories import hard_conflict

        blank = self._row("1 Gang Blank Cover", "Boxes & Enclosures > Boxes - Weatherproof")
        other = self._row("1 Gang Weatherproof Toggle Cover", "Boxes & Enclosures > Boxes - Weatherproof")
        box = self._row("1 Gang Weatherproof Box", "Boxes & Enclosures > Boxes - Weatherproof")
        self.assertEqual(hard_conflict(blank, other), "cover_substitute")
        self.assertEqual(hard_conflict(blank, box), "")

    def test_skylark_dual_control_requires_designer_wallplate(self):
        from generate_accessories import hard_conflict

        control = self._row("LUT S2-LF-LA SKYLARK DUAL FAN/LIGHT", "Fans, Heaters > Fans & Fan Controls")
        decora = self._row("DECORA WALLPLATE", "Wiring Devices > Wallplates")
        duplex = self._row("DUPLEX WALLPLATE", "Wiring Devices > Wallplates")
        self.assertEqual(hard_conflict(control, decora), "")
        self.assertEqual(hard_conflict(control, duplex), "cutout_mismatch")
        fan = self._row("BRO 80 CFM VENTILATION FAN", "Fans, Heaters > Fans & Fan Controls")
        self.assertEqual(hard_conflict(control, fan), "unverified_fan_control_accessory")

    def test_oscillating_blade_does_not_recommend_reciprocating_saw(self):
        from generate_accessories import hard_conflict

        blade = self._row("MIL NITRUS OMT CARBIDE BLADE", "Tools & Meters > Bits, Blades, Punches, Holesaws")
        saw = self._row("MIL M18 HACKZALL", "Tools & Meters > Cordless Tools")
        self.assertEqual(hard_conflict(blade, saw), "tool_mismatch")

    def test_verified_floor_box_and_lug_accessories_are_not_generic_parts(self):
        from generate_accessories import hard_conflict

        box = self._row("ARL FLBC8500 CONCRETE FLOOR BOX", "Boxes & Enclosures > Floor Boxes")
        cover = self._row("ARL FLBC8510GY SINGLE GANG GRAY FLOOR", "Boxes & Enclosures > Floor Boxes")
        receptacle = self._row("HUB DUPLEX RECEPTACLE", "Wiring Devices > Receptacles")
        lug = self._row("SPS L11-TL3 TERMINAL LUG KIT", "Connectors & Crimps > Lugs & Split Bolts")
        switch = self._row("SPS L11-J400-1753 FUSED DISCONNECT", "Control & Automation > Disconnect Switches")
        fork = self._row("SMALL FORK TERMINAL", "Connectors & Crimps > Small Terminals, Forks, Rings")
        self.assertEqual(hard_conflict(box, cover), "")
        self.assertEqual(hard_conflict(box, receptacle), "unverified_floor_box_accessory")
        self.assertEqual(hard_conflict(lug, switch), "")
        self.assertEqual(hard_conflict(lug, fork), "unverified_lug_accessory")

    def test_machine_screw_accepts_matching_nuts_and_washers_only(self):
        from generate_accessories import hard_conflict

        screw = self._row("10-32 RHMS MACHINE SCREW", "Fasteners & Hardware > Screws")
        nut = self._row("10-32 HEX NUT", "Fasteners & Hardware > Nuts, Bolts, Washers")
        wrong_nut = self._row("10-24 HEX NUT", "Fasteners & Hardware > Nuts, Bolts, Washers")
        washer = self._row("#10 FLAT WASHER", "Fasteners & Hardware > Nuts, Bolts, Washers")
        tek = self._row("10-16 HWH TEK SCREW Hex Washer Head", "Fasteners & Hardware > Screws")
        self.assertEqual(hard_conflict(screw, nut), "")
        self.assertEqual(hard_conflict(screw, washer), "")
        self.assertEqual(hard_conflict(screw, wrong_nut), "unverified_fastener_accessory")
        self.assertEqual(hard_conflict(screw, tek), "unverified_fastener_accessory")

    def test_halco_tube_rejects_external_drivers_and_unverified_sockets(self):
        from generate_accessories import hard_conflict

        lamp = self._row("HALCO 87204 48 INCH LED T8 TYPE B", "Lamps, Bulbs & Ballast > Bulbs - LED")
        driver = self._row("LED DRIVER", "Lamps, Bulbs & Ballast > Drivers - LED")
        socket = self._row("T8 BIPIN SOCKET", "Lighting Fixtures, Control > Lampholders, Adapters & Extensions")
        known = self._row("T8 NON-SHUNTED SOCKET", "Lighting Fixtures, Control > Lampholders, Adapters & Extensions")
        self.assertEqual(hard_conflict(lamp, driver), "type_b_driver")
        self.assertEqual(hard_conflict(lamp, socket), "unverified_lampholder")
        self.assertEqual(hard_conflict(lamp, known), "")

    def test_known_dimmer_and_toggle_switch_match_correct_wallplates(self):
        from generate_accessories import hard_conflict

        dimmer = self._row(
            "LUT DVCL-153P-WH CFL/LED DIMMER", "Lighting Fixtures, Control > Dimmers/Dimming Controls"
        )
        switch = self._row("PNS 660-WG WHITE 1P SWITCH", "Wiring Devices > Switches")
        decora = self._row("PNS TP26-W WHITE DECORA WALLPLATE", "Wiring Devices > Wallplates")
        duplex = self._row("PNS TP8-W WHITE DUPLEX WALLPLATE", "Wiring Devices > Wallplates")
        toggle = self._row("PNS TP1-W WHITE TOGGLE WALLPLATE", "Wiring Devices > Wallplates")
        blank = self._row("PNS TP13-W WHITE BLANK WALLPLATE", "Wiring Devices > Wallplates")
        combo = self._row(
            "PNS TP18-W 1 TOGGLE 1 DUPLEX COMBINATION WALLPLATE", "Wiring Devices > Wallplates"
        )
        single_combo = self._row(
            "1 TOGGLE 1 SINGLE RECEPTACLE COMBINATION WALLPLATE", "Wiring Devices > Wallplates"
        )
        single = self._row("1 GANG SINGLE RECEPTACLE", "Wiring Devices > Receptacles")
        combo_device = self._row("COMBINATION SWITCH RECEPTACLE", "Wiring Devices > Combination Devices")
        radiant = self._row(
            "PNS RHL153PW RADIANT LED DIMMER", "Wiring Devices > Switches"
        )

        self.assertEqual(hard_conflict(dimmer, decora), "")
        self.assertEqual(hard_conflict(dimmer, duplex), "cutout_mismatch")
        self.assertEqual(hard_conflict(dimmer, blank), "cutout_mismatch")
        self.assertEqual(hard_conflict(dimmer, combo), "cutout_mismatch")
        self.assertEqual(hard_conflict(single, single_combo), "")
        self.assertEqual(hard_conflict(combo_device, combo), "")
        self.assertEqual(hard_conflict(switch, toggle), "")
        self.assertEqual(hard_conflict(duplex, switch), "cutout_mismatch")
        self.assertEqual(hard_conflict(duplex, radiant), "cutout_mismatch")

    def test_dimmer_substitutes_are_not_installation_accessories(self):
        from generate_accessories import hard_conflict

        path = "Lighting Fixtures, Control > Dimmers/Dimming Controls"
        dimmer = self._row("LUT DVCL-153P-WH LED DIMMER", path)
        alternate = self._row("LUT AYCL-153P-WH LED DIMMER", path)
        companion = self._row("LUT COMPANION DIMMER", path)
        self.assertEqual(hard_conflict(dimmer, alternate), "dimmer_substitute")
        self.assertEqual(hard_conflict(dimmer, companion), "")

    def test_dvcl_dimmer_does_not_get_unverified_lighting_control_hardware(self):
        from generate_accessories import hard_conflict

        dimmer = self._row(
            "LUT DVCL-153P-WH LED DIMMER", "Lighting Fixtures, Control > Dimmers/Dimming Controls"
        )
        pico = self._row("LUT PICO-WBX-ADAPT WALLBOX ADAPTER", "Lighting Fixtures, Control > Indoor Lighting")
        pole = self._row("LITH 20 FOOT STEEL LIGHT POLE", "Lighting Fixtures, Control > Outdoor Lighting")
        plate = self._row("PNS TP26-W WHITE DECORA WALLPLATE", "Wiring Devices > Wallplates")

        self.assertEqual(hard_conflict(dimmer, pico), "dimmer_control_mismatch")
        self.assertEqual(hard_conflict(dimmer, pole), "dimmer_control_mismatch")
        self.assertEqual(hard_conflict(dimmer, plate), "")

    def test_unknown_dimmer_style_is_not_guessed(self):
        from generate_accessories import device_cutout

        self.assertEqual(
            device_cutout(self._row("UNKNOWN DIMMER", "Lighting Fixtures, Control > Dimmers/Dimming Controls")),
            "",
        )

    def test_square_box_cover_requires_matching_shape_and_size(self):
        from generate_accessories import hard_conflict

        path = "Boxes & Enclosures > Boxes - Metallic"
        box = self._row("RAC 232 SQUARE BOX 4 -2-1/8DP", path)
        square_cover = self._row("RAC 752 4 SQ FLAT BLANK COVER", path)
        larger_cover = self._row("4-11/16 SQUARE FLAT BLANK COVER", path)
        octagon_cover = self._row("4 OCTAGON FLAT BLANK COVER", path)

        self.assertEqual(hard_conflict(box, square_cover), "")
        self.assertEqual(hard_conflict(box, larger_cover), "box")
        self.assertEqual(hard_conflict(box, octagon_cover), "box")

    def test_gang_mismatch_is_dropped(self):
        from generate_accessories import hard_conflict

        box = self._row("ALL P-240OW OLD WK 2G BOX", "Boxes & Enclosures > Boxes - Non-Metallic")
        plate = self._row("PNS TP8-W 1 Gang WALLPLATE WHITE", "Wiring Devices > Wallplates")
        self.assertEqual(hard_conflict(box, plate), "gang")

        # 1-gang device rejects 4-gang box
        recp_1g = self._row("PNS 3232-I 15A DUPLEX RECEPTACLE", "Wiring Devices > Receptacles")
        box_4g = self._row("ALL P-764B 74.8 CI 4G DVC BOX", "Boxes & Enclosures > Boxes - Non-Metallic")
        self.assertEqual(hard_conflict(recp_1g, box_4g), "gang")

    def test_cutout_mismatch_is_dropped(self):
        from generate_accessories import hard_conflict

        duplex_recp = self._row("PNS 3232-I 15A IVORY RESI DUPLEX RECEPTACLE", "Wiring Devices > Receptacles")
        single_round_plate = self._row("PNS TP7-I 1G SINGLE RECEPTACLE PLATE 1.406", "Wiring Devices > Wallplates")
        toggle_plate = self._row("PNS TP1-I 1G TOGGLE SWITCH PLATE", "Wiring Devices > Wallplates")
        duplex_plate = self._row("PNS TP8-I 1G DUPLEX RECEPTACLE PLATE", "Wiring Devices > Wallplates")

        self.assertEqual(hard_conflict(duplex_recp, single_round_plate), "cutout_mismatch")
        self.assertEqual(hard_conflict(duplex_recp, toggle_plate), "cutout_mismatch")
        self.assertEqual(hard_conflict(duplex_recp, duplex_plate), "")

    def test_commodity_wiring_device_rejects_heavy_tools(self):
        from generate_accessories import hard_conflict

        recp = self._row("PNS 3232-I 15A RESI DUPLEX RECEPTACLE", "Wiring Devices > Receptacles")
        stripper = self._row("KLE K1412 KURVED ROMEX WIRE STRIPPER", "Tools & Meters > Hand Tools")
        meter = self._row("KLE RT210 GFCI RECEPTACLE TESTER", "Tools & Meters > Meters - Test Equipment - Calculators")

        self.assertEqual(hard_conflict(recp, stripper), "tool_mismatch")
        self.assertEqual(hard_conflict(recp, meter), "tool_mismatch")

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

    def test_spa_panel_rejects_emergency_service_disconnect_label(self):
        from generate_accessories import hard_conflict

        spa = self._row("Eaton 60A Spa Panel GFCI", "Power Distribution > Safety Switches / Disconnects")
        label = self._row("Emergency Service Disconnect Label", "Tools & Meters > Marking & Labeling")
        self.assertEqual(hard_conflict(spa, label), "label_mismatch")

    def test_fuse_class_voltage_and_amp_conflicts(self):
        from generate_accessories import hard_conflict

        fuse_70a_600v = self._row("Mersen AJT 70 Amp 600 Volt Class J Fuse", "Fuses & Accessories > Power Fuses")
        reducer_class_r = self._row("Bussmann 60-100A 600V Class R Fuse Reducer", "Fuses & Accessories > Fuse Blocks, Holders & Accessories")
        block_class_cc = self._row("Ferraz Shawmut 600V 30A Class CC Fuse Block", "Fuses & Accessories > Fuse Blocks, Holders & Accessories")
        reducer_250v = self._row("Bussmann 250V 30A Fuse Reducer", "Fuses & Accessories > Fuse Blocks, Holders & Accessories")
        puller = self._row("Ferraz Shawmut Plastic Fuse Puller", "Fuses & Accessories > Fuse Blocks, Holders & Accessories")

        self.assertEqual(hard_conflict(fuse_70a_600v, reducer_class_r), "fuse_class")
        self.assertEqual(hard_conflict(fuse_70a_600v, block_class_cc), "fuse_class")
        self.assertEqual(hard_conflict(fuse_70a_600v, reducer_250v), "fuse_voltage")
        self.assertEqual(hard_conflict(fuse_70a_600v, puller), "")


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
