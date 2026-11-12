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
    def test_functional_classes_do_not_get_filled_with_unrelated_products(self):
        cases = (
            ("Boxes & Enclosures > Boxes - Metallic", "4 Inch Square Box", "4 Inch Square Box", "4 Inch Square Extension Ring"),
            ("Boxes & Enclosures > Boxes - Weatherproof", "Blank Cover", "Blank Cover", "In-Use Cover"),
            ("Fittings > Bushing / K.O. Seal / Locknuts", "2 Inch Grounding Bushing", "1 Inch Grounding Bushing", "2 Inch Plastic Bushing"),
            ("Fittings > Bushing / K.O. Seal / Locknuts", "2 Inch Plastic Bushing", "1 Inch Plastic Bushing", "2 Inch Reducing Bushing"),
            ("Fittings > PVC Fittings", "2 Inch PVC Male Adapter", "1 Inch PVC Male Adapter", "2 Inch PVC Female Adapter"),
            ("Fittings > Clips, Clamps, Hangers", "3/4 Inch EMT Jiffy Clip", "1/2 Inch EMT Jiffy Clip", "MC Cable Jiffy Clip"),
            ("Fittings > Clips, Clamps, Hangers", "MC Cable Jiffy Clip", "MC/AC Stud Clip", "BPT 280SP CUPL AC/MC SNAP-IN TO 1/2IN EMT"),
            ("Fittings > Clips, Clamps, Hangers", "3/4 Inch EMT Jiffy Clip", "1/2 Inch EMT Jiffy Clip", "3/4 Inch GRC Jiffy Clip"),
            ("Tools & Meters > Paint, Chemicals, Cleaning Supplies", "PVC Solvent Cement", "PVC Quick Set Cement", "Silicone Sealant"),
            ("Connectors & Crimps > Grounding Products", "5/8 Inch Ground Rod", "3/4 Inch Ground Rod", "Intersystem Bonding Bridge"),
            ("Fittings > Conduit Bodies", "3/4 Inch LB Body Complete With Cover", "1 Inch LB Body Complete With Cover", "3/4 Inch LB Cover Only"),
            ("Fittings > MC, NMB, Liquidtight, Flex Fittings", "ARL NMLT50 LFNC-B Liquidtight Connector", "LFNC-B Liquidtight Connector", "FMC Squeeze Connector"),
            ("Wire, Cable & Cord > Armored Cable", "12/2 MC Cable", "12/3 MC Cable", "12/2 HCF-AC Cable"),
            ("Power Distribution > Circuit Breakers", "CUT BR120 20A/1 Pole Breaker", "CUT BR115 15A/1 Pole Breaker", "CUT BR2020 Tandem Breaker"),
            ("Control & Automation > Disconnect Switches", "MDW U065P HVAC AC Disconnect", "HVAC AC Disconnect U065", "Three Phase Motor Disconnect"),
            ("Fittings > Bushing / K.O. Seal / Locknuts", '2 Inch Plastic Bushing', '1 Inch Plastic Bushing', '2" Nylon Knock-Out Bushing'),
            ("Fittings > Bushing / K.O. Seal / Locknuts", '2 Inch Plastic Bushing', '1 Inch Plastic Bushing', 'ARL EMT200 2" NM Bushing'),
            ("Fittings > Bushing / K.O. Seal / Locknuts", '2 Inch Plastic Bushing', '1 Inch Plastic Bushing', 'MDW B20 2" HUB'),
            ("Power Distribution > Circuit Breakers", "CUT BR120 20A Breaker", "CUT BR115 15A Breaker", "CUT BRLW1 Handle Lockoff for 1 Pole BR Breaker"),
            ("Control & Automation > Disconnect Switches", "MDW U065P HVAC Disconnect", "HVAC Disconnect U065", "PNS 7803-MD 3 PH AC Motor Disconnect Switch"),
            ("Control & Automation > Disconnect Switches", "MDW U065P HVAC Disconnect", "HVAC Disconnect U065", "ABB OT63F3 3 POLE 60 AMP 600 VOLT AC DISCONNECT"),
            ("Fittings > Conduit Bodies", "3/4 Inch LB Body Complete With Cover", "1 Inch LB Body Complete With Cover", "KIL 270 Steel CVR Conduit Body Cover"),
        )
        for node, target_text, same_text, wrong_text in cases:
            with self.subTest(target=target_text):
                target = _row("target", target_text, node)
                same = _row("same", same_text, node)
                wrong = _row("wrong", wrong_text, node)
                wrong["orders_6m"] = 2000
                self.assertEqual(find_similar_products(target, {node: [target, same, wrong]}, {}), ["same"])

    def test_wallplates_preserve_complete_plate_gang_and_opening(self):
        node = "Wiring Devices > Wallplates"
        target = _row("target", "2 Gang Decora Wallplate White", node)
        same = _row("same", "2 Gang Decora Wallplate Gray", node)
        wrong = [
            _row("insert", "2 Gang Decora Wallplate Adapter Insert", node),
            _row("gang", "3 Gang Decora Wallplate White", node),
            _row("opening", "2 Gang Toggle Wallplate White", node),
        ]
        self.assertEqual(find_similar_products(target, {node: [target, same, *wrong]}, {}), ["same"])

    def test_thhn_preserves_grounding_and_neutral_color_roles(self):
        node = "Wire, Cable & Cord > Building Wire"
        for color in ("White", "Green"):
            with self.subTest(color=color):
                target = _row("target", f"12 AWG {color} THHN Wire Solid", node)
                same = _row("same", f"10 AWG {color} THHN Wire Stranded", node)
                wrong = _row("wrong", "12 AWG Black THHN Wire Solid", node)
                self.assertEqual(find_similar_products(target, {node: [target, same, wrong]}, {}), ["same"])

    def test_wr_gfci_preserves_weather_resistance(self):
        node = "Wiring Devices > GFCI Personnel Protection Devices"
        target = _row("target", "15A WR GFCI RECEPTACLE", node)
        same = _row("same", "20A WEATHER RESISTANT GFCI RECEPTACLE", node)
        indoor = _row("indoor", "15A GFCI RECEPTACLE", node)
        self.assertEqual(find_similar_products(target, {node: [target, same, indoor]}, {}), ["same"])

    def test_exact_trade_size_outweighs_brand_and_sales_for_siblings(self):
        node = "Fittings > EMT Fittings"
        target = _row("target", "3/4 Inch EMT Connector", node)
        target["brand_manufacturer"] = "Brand A"
        exact = _row("exact", "3/4 Inch EMT Connector", node)
        exact["brand_manufacturer"] = "Brand B"
        other = _row("other", "1 Inch EMT Connector", node)
        other["brand_manufacturer"] = "Brand A"
        other["orders_6m"] = 2000
        result = find_similar_products(target, {node: [target, other, exact]}, {})
        self.assertEqual(result, ["exact", "other"])

    def test_blank_plates_do_not_match_decora_blank_combinations(self):
        node = "Wiring Devices > Wallplates"
        target = _row("target", "1 Gang Blank Wallplate", node)
        same = _row("same", "1 Gang Blank Wallplate Gray", node)
        combo = _row("combo", "PNS SP1426-W WALLPLATE 1 DECOR 1 BLANK", node)
        self.assertEqual(find_similar_products(target, {node: [target, same, combo]}, {}), ["same"])

    def test_raised_cover_depth_is_not_an_opening_count(self):
        node = "Boxes & Enclosures > Boxes - Metallic"
        target = _row("target", "RAC 902C COVER 4SQ RSD 1/2 DPLX RECP", node)
        same = _row("same", "4 Inch Square Duplex Cover Raised 5/8 Inch", node)
        double = _row("double", "RAC 907C COVER 4SQ RSD1/2-2DPLX RECP", node)
        self.assertEqual(find_similar_products(target, {node: [target, same, double]}, {}), ["same"])

    def test_shallow_strut_ranks_plain_same_profile_before_popular_deep_channel(self):
        node = "Conduit, Strut & Raceways > Strut"
        target = _row("target", "HDN H-164-OSx10PG Shallow Channel", node)
        same = _row("same", "HDN H-164x10PG Plain Shallow Channel", node)
        deep = _row("deep", "HDN H-132-OSx10PG Deep Channel", node)
        deep["orders_6m"] = 2000
        self.assertEqual(find_similar_products(target, {node: [target, same, deep]}, {}), ["same", "deep"])
    def test_blades_do_not_mix_oscillating_and_reciprocating_tools(self):
        node = "Tools & Meters > Bits, Blades, Punches, Holesaws"
        target = _row("target", "MIL NITRUS OMT CARBIDE BLADE", node)
        oscillating = _row("omt", "MIL OSCILLATING MULTI-TOOL BLADE", node)
        sawzall = _row("saw", "MIL NITRUS CARBIDE SAWZALL BLADE", node)
        self.assertEqual(find_similar_products(target, {node: [target, oscillating, sawzall]}, {}), ["omt"])

    def test_driver_bits_do_not_match_saw_blades(self):
        node = "Tools & Meters > Bits, Blades, Punches, Holesaws"
        target = _row("target", "MIL 48-32-4805 SQ2 Power Bit", node)
        insert = _row("insert", "MIL Shockwave Phillips #2 Insert Bit 5-Pack", node)
        bandsaw = _row("bandsaw", "MIL Compact Band Saw Blade 35-3/8 Inch 18 TPI", node)
        sawzall = _row("sawzall", "MIL Super Sawzall 6 Inch Reciprocating Saw Blade", node)
        bandsaw["orders_6m"] = 2000
        sawzall["orders_6m"] = 2000
        result = find_similar_products(target, {node: [target, insert, bandsaw, sawzall]}, {})
        self.assertNotIn("bandsaw", result)
        self.assertNotIn("sawzall", result)
        self.assertEqual(result[0], "insert")

    def test_renewal_coils_are_not_contactors_or_other_frame_coils(self):
        node = "Control & Automation > Starters & Contactors"
        target = _row("target", "SPS CA7-TD855 24V Renewal Coil", node)
        same_frame = _row("same", "SPS CA7-TD475 480V Renewal Coil", node)
        other_frame = _row("other", "SPS CA7-TA855 24V Renewal Coil", node)
        contactor = _row("contactor", "SPS CA7-43 24V Coil Contactor", node)
        self.assertEqual(find_similar_products(target, {node: [target, same_frame, other_frame, contactor]}, {}), ["same"])

    def test_machine_screws_require_the_same_thread(self):
        node = "Fasteners & Hardware > Screws"
        target = _row("target", "10-32 RHMS MACHINE SCREW", node)
        same = _row("same", "10-32 RHMS MACHINE SCREW", node)
        different = _row("different", "10-24 RHMS MACHINE SCREW", node)
        tek = _row("tek", "10-32 TEK SELF-DRILLING SCREW", node)
        self.assertEqual(find_similar_products(target, {node: [target, same, different, tek]}, {}), ["same"])

    def test_gfci_receptacle_does_not_match_dead_front(self):
        node = "Wiring Devices > GFCI Personnel Protection Devices"
        target = _row("target", "20A GFCI RECEPTACLE", node)
        receptacle = _row("receptacle", "15A GFCI RECEPTACLE", node)
        dead_front = _row("dead", "20A GFCI DEAD FRONT", node)
        self.assertEqual(find_similar_products(target, {node: [target, receptacle, dead_front]}, {}), ["receptacle"])

    def test_floor_box_does_not_match_flbc85_cover_kits(self):
        node = "Boxes & Enclosures > Floor Boxes"
        target = _row("target", "ARL FLBC8500 CONCRETE FLOOR BOX", node)
        box = _row("box", "ARL FLBC4500 CONCRETE FLOOR BOX", node)
        cover = _row("cover", "ARL FLBC8510GY SINGLE GANG GRAY FLOOR", node)
        self.assertEqual(find_similar_products(target, {node: [target, box, cover]}, {}), ["box"])

    def test_t8_alternatives_have_matching_length(self):
        node = "Lamps, Bulbs & Ballast > Bulbs - LED"
        target = _row("target", "HALCO 48 Inch LED T8 TYPE B", node)
        same = _row("same", "48T8-12 LED TUBE", node)
        model = _row("model", "KT-LED-14.5T8-48G LED", node)
        short = _row("short", "36T8-12 LED TUBE", node)
        ubend = _row("ubend", "48 INCH T8 U-BEND LED", node)
        self.assertEqual(set(find_similar_products(target, {node: [target, same, model, short, ubend]}, {})), {"same", "model"})

    def test_unitap_and_l11_terminal_lug_kits_do_not_match_other_lugs(self):
        node = "Connectors & Crimps > Lugs & Split Bolts"
        target = _row("target", "BUR BIBS2/03 UNITAP", node)
        unitap = _row("unitap", "BUR BIBD2/03 UNITAP", node)
        lug = _row("lug", "SPS L11-TL3 TERMINAL LUG KIT", node)
        other = _row("other", "COPPER MECHANICAL LUG", node)
        items = [target, unitap, lug, other]
        self.assertEqual(find_similar_products(target, {node: items}, {}), ["unitap"])
        self.assertEqual(find_similar_products(lug, {node: items}, {}), [])

    def test_fan_control_is_not_a_ventilation_fan(self):
        node = "Fans, Heaters > Fans & Fan Controls"
        target = _row("target", "LUT S2-LF DUAL FAN AND LIGHT CONTROL", node)
        control = _row("control", "LUT S2-LF FAN CONTROL", node)
        fan = _row("fan", "BRO 80 CFM VENTILATION FAN", node)
        self.assertEqual(find_similar_products(target, {node: [target, control, fan]}, {}), ["control"])

    def test_nonmetallic_splice_kits_do_not_match_heat_trace(self):
        node = "Fans, Heaters > Heat Cable"
        kit = _row("315350", "NSI Non Metallic Splice Kit 12/2", node)
        other_kit = _row("179503", "NSI Non Metallic Splice Kit 12/3", node)
        cable = _row("13596", "EAS SR TRACE 750 FT SPOOL 120V", node)
        seal = _row("328525", "DREX Lighted End Seal 120-240V", node)
        items = [kit, other_kit, cable, seal]

        self.assertEqual(find_similar_products(kit, {node: items}, {}), ["179503"])
        self.assertNotIn("315350", find_similar_products(cable, {node: items}, {}))

    def test_ground_rod_clamps_do_not_match_pipe_or_fence_clamps(self):
        node = "Connectors & Crimps > Grounding Products"
        rod = _row("1961", "GLV 5/8 GROUND ROD CLAMP", node)
        universal = _row("28394", "UNIVERSAL GROUND ROD CLAMP 1/2, 5/8, 3/4", node)
        pipe = _row("42920", "GROUND CLAMP 1-1/4 TO 2IN PIPE", node)
        fence = _row("194306", "FENCE FABRIC GROUND CLAMP #6", node)
        items = [rod, universal, pipe, fence]

        self.assertEqual(find_similar_products(rod, {node: items}, {}), ["28394"])
        self.assertNotIn("1961", find_similar_products(pipe, {node: items}, {}))

    def test_intersystem_bonding_bridges_match_each_other(self):
        node = "Data & Telecom > Grounding and Bonding"
        bridge = _row("125416", "INTERSYSTEM BOND WALL MOUNT", node)
        other_bridge = _row("292190", "INTERSYSTEM GROUNDING BOND", node)
        wedge = _row("12229", "WEDGE GRIP 2/0-4/0 ACSR", node)
        items = [bridge, other_bridge, wedge]

        self.assertEqual(find_similar_products(bridge, {node: items}, {}), ["292190"])
        self.assertNotIn("125416", find_similar_products(wedge, {node: items}, {}))

    def test_br_breaker_prioritizes_same_pole_and_excludes_other_series(self):
        node = "Power Distribution > Circuit Breakers"
        target = _row("2995", "CUT BR120 Type BR Breaker 20A/1 Pole 120/240V 10k", node)
        same_pole = _row("2993", "CUT BR115 Type BR Breaker 15A/1 Pole 120/240V 10K", node)
        other_pole = _row("2998", "CUT BR220 Type BR Breaker 20A/2 Pole 120/240V 10K", node)
        other_series = _row("ch", "CUT CH120 Type CH Breaker 20A/1 Pole 120/240V 10K", node)

        result = find_similar_products(
            target, {node: [target, other_pole, other_series, same_pole]}, {}
        )

        self.assertNotIn("ch", result)
        self.assertLess(result.index("2993"), result.index("2998"))

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
