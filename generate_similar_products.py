"""Generate similar and alternative products for each catalog item.

Usage:
    python generate_similar_products.py [--input enriched_products.csv] [--output similar_products.csv]

Produces similar/alternative products (Carousel 2) using taxonomy, brand,
series, and sales volume ranking. Complements product_accessories.csv (Carousel 1).
"""

import argparse
import logging
import os
import re
from collections import defaultdict

import pandas as pd

import generate_accessories as ga

ID_COL = "BLAZER ID"
OUTPUT_ID_COL = "PROD_ID"
RELATION_COLUMNS = [f"SIMILAR_ID_{i}" for i in range(1, 11)]
ORDERS_COL = "orders_6m"
MIN_ORDERS = 1

logger = logging.getLogger("generate_similar")


def _tool_type(text: str) -> str:
    """Identify tool sub-type to keep pliers with pliers, drivers with drivers, etc."""
    patterns = (
        ("sawzall_blade", r"sawzall|recip.*blade|the torch|the ax|\btorch\b.*nitrus|nitrus.*carbide|demo demon.*recip|steel demon.*recip|super sawzall"),
        ("oscillating_blade", r"omt|oscillat|multi-tool blade|starlock|open-lok"),
        ("bandsaw_blade", r"band ?saw blade|compact.*band saw|deep cut.*band saw"),
        ("modular_crimper", r"modular.*crimp|vdv226|pass-thru crimp|compression crimper"),
        ("stripper", r"wire stripper|croc stripper|k12075|k11095|11045|11055|13 7[12] 8"),
        ("linemans", r"lineman|side-cutting plier|09 1[12] 240|d213"),
        ("diagonal_cutters", r"diagonal|diag.*plier|d248|d2000-48|j2000-48|d2000-28|78 61 125"),
        ("pump_pliers", r"pump plier|tongue and groove|d502|cobra"),
        ("needle_nose", r"needle nose|long-nose|long nose|d203"),
        ("crimper", r"crimp|crimper"),
        ("pliers", r"plier"),
        ("screwdriver", r"screwdriver|nut driver|multi-bit|11-in-1|6-in-1|cushion-grip"),
        ("hex_key", r"hex key|allen\b|fldg hex|fold hex|ball-end hex|l-style.*hex"),
        ("wrench", r"wrench|torque|adjustable"),
        ("saw", r"saw|hacksaw|jab saw"),
        ("cutter", r"cable cutter|bolt cutter|pipe cutter"),
        ("level", r"level|torpedo"),
        ("tester", r"tester|multimeter|voltage|meter|detector"),
        ("drill_bit", r"drill bit|auger|step bit|hole saw"),
        ("tape_measure", r"tape measure|measuring tape|rule"),
        ("fish_tape", r"fish tape|glow rod|fish rod"),
        ("socket_ratchet", r"socket|flip socket|impact socket|ratchet"),
    )
    for name, pattern in patterns:
        if re.search(pattern, text, re.I):
            return name
    return ""


def _lighting_type(text: str) -> str:
    """Identify lighting fixture sub-type to keep downlights with downlights, troffers with troffers, etc."""
    patterns = (
        ("step_light", r"step light|steplight|path light"),
        ("wallpack", r"wallpack|wall pack|wpled|xtor"),
        ("floodlight", r"floodlight|flood light|\bflood\b|ll1200|fled\d+"),
        ("vanity", r"vanity|bath bar"),
        ("flush_mount", r"flush mount|semi-flush"),
        ("downlight", r"downlight|wafer|recessed|can light|flush wafer|kt-wdled|lrkr|retrofit kit"),
        ("panel_troffer", r"flat panel|troffer|\bcpx\b|\bcbt\b|backlit|back-lit|fpl-\d{2}|panel 2x[24]|panel 1x4"),
        ("strip_wrap", r"strip|wrap|linear|\bcss\b|\bcsl\b|slstp|l48|l96|cwp4|lsf8"),
        ("undercabinet_tape", r"undercabinet|tape light|spool|puck|tape/rope|blaze"),
        ("high_bay", r"high ?bay|low ?bay|ufo|\buhb\b|clh1|ibe|ibg|rebl|cprb"),
        ("tube_bulb", r"t8|t5|t12|tube|u-bend|a19|lamp|bulb"),
        ("accessory_guard", r"guard|cage|bracket|hanger|lens|louvre|trim|frame|whip|adapt|pico"),
    )
    for name, pattern in patterns:
        if re.search(pattern, text, re.I):
            return name
    return ""


def find_similar_products(
    target: dict[str, str],
    by_node: dict[str, list[dict[str, str]]],
    source_map: dict[str, dict[str, str]],
) -> list[str]:
    """Find up to 10 similar or alternative products for the target."""
    target_id = target[ID_COL]
    target_node = target.get(ga.TAX_COL, "")
    if not target_node or target_node not in by_node:
        return []

    target_text = ga._text(target)
    target_brand = ga._brand(target)
    target_series = ga.breaker_series(target) if "Circuit Breakers" in target_node else ""
    target_tool = _tool_type(target_text) if "Tools" in target_node else ""
    target_color = ga._color(target)

    candidates = [
        c for c in by_node[target_node]
        if c[ID_COL] != target_id
        and "obsolete" not in c["PRODUCT"].lower()
    ]

    # Score and rank candidates:
    # We want alternatives that are closely related (same breaker line, same tool type, same device style)
    scored = []
    for c in candidates:
        c_text = ga._text(c)
        c_brand = ga._brand(c)
        c_orders = c.get(ORDERS_COL, 0)
        score = 0

        # Breakers: Must match series (e.g. Eaton BR with Eaton BR, Homeline with Homeline)
        if target_series:
            c_series = ga.breaker_series(c)
            if c_series != target_series:
                continue
            score += 100
            # For breakers, boost matching number of poles (e.g. 3-pole with 3-pole)
            t_poles = re.search(r"\b([1234])\s*(?:-| )?pole\b|\b([1234])p\b|/\d/([1234])\b|/([1234])/", target_text, re.I)
            c_poles = re.search(r"\b([1234])\s*(?:-| )?pole\b|\b([1234])p\b|/\d/([1234])\b|/([1234])/", c_text, re.I)
            t_p_val = next((g for g in t_poles.groups() if g), "") if t_poles else ""
            c_p_val = next((g for g in c_poles.groups() if g), "") if c_poles else ""
            if t_p_val and c_p_val and t_p_val == c_p_val:
                score += 50
            # Boost matching amperage rating
            t_amps = ga.amp_ratings(target)
            c_amps = ga.amp_ratings(c)
            if t_amps and c_amps and (t_amps & c_amps):
                score += 30

        # Tools: Prefer same tool type (e.g. strippers with strippers, drivers with drivers)
        if target_tool:
            c_tool = _tool_type(c_text)
            if c_tool == target_tool:
                score += 120
                # Extra boost if both are blades or both are actual saw tools
                t_is_blade = bool(re.search(r"\bblade|pk\b|\bpack\b", target_text, re.I))
                c_is_blade = bool(re.search(r"\bblade|pk\b|\bpack\b", c_text, re.I))
                if t_is_blade == c_is_blade:
                    score += 40
                else:
                    score -= 40
                # For oscillating blades: match multi-blade sets (e.g. 5 pc set with 5 pc / 9 pc set) vs individual/multi-packs
                if target_tool == "oscillating_blade":
                    t_is_set = bool(re.search(r"\bset\b|\b(?:5|9)\s*pc\b", target_text, re.I))
                    c_is_set = bool(re.search(r"\bset\b|\b(?:5|9)\s*pc\b", c_text, re.I))
                    if t_is_set and c_is_set:
                        score += 80
                    elif t_is_set != c_is_set:
                        score -= 30
                    # Starlock vs Universal fit
                    t_sl = bool(re.search(r"starlock|\bdos\w*", target_text, re.I))
                    c_sl = bool(re.search(r"starlock|\bdos\w*", c_text, re.I))
                    if t_sl and c_sl:
                        score += 50
                    # Carbide vs Bi-Metal match
                    t_carb = bool(re.search(r"carbide|tct|nitrus", target_text, re.I))
                    c_carb = bool(re.search(r"carbide|tct|nitrus", c_text, re.I))
                    if t_carb and c_carb:
                        score += 40
            elif c_tool and c_tool != target_tool:
                # penalize mixing unrelated tools (e.g. fish tape with screwdriver)
                score -= 80

        # Brand consistency
        if target_brand and c_brand == target_brand:
            score += 40

        # Color consistency for wiring devices
        if "Wiring Devices" in target_node:
            c_color = ga._color(c)
            if target_color and c_color == target_color:
                score += 30
            # Decora vs Traditional
            t_decora = bool(re.search(r"decora|decorator|rocker", target_text, re.I))
            c_decora = bool(re.search(r"decora|decorator|rocker", c_text, re.I))
            if t_decora == c_decora:
                score += 20
            # Hospital Grade (HG) receptacle affinity
            t_hg = bool(re.search(r"\bhg\b|hospital", target_text, re.I))
            c_hg = bool(re.search(r"\bhg\b|hospital", c_text, re.I))
            if t_hg and c_hg:
                score += 100
            elif t_hg != c_hg:
                score -= 40
            # Wallplate gang count matching
            if "Wallplates" in target_node:
                t_g = ga.gang_count(target)
                c_g = ga.gang_count(c)
                if t_g and c_g:
                    if t_g == c_g:
                        score += 80
                    else:
                        score -= 60
                # Match opening style: 2 duplex vs toggle vs blank vs decora
                t_2dup = bool(re.search(r"2\s*d(?:p)?lx|2\s*duplex|tp82", target_text, re.I))
                c_2dup = bool(re.search(r"2\s*d(?:p)?lx|2\s*duplex|tp82", c_text, re.I))
                if t_2dup and c_2dup:
                    score += 80
                elif t_2dup != c_2dup:
                    score -= 40

        # Boxes: Keep box with box, cover with cover
        if "Boxes & Enclosures" in target_node:
            t_is_cover = bool(re.search(r"cover|cvr|ring|plate|lid\b", target_text, re.I))
            c_is_cover = bool(re.search(r"cover|cvr|ring|plate|lid\b", c_text, re.I))
            if t_is_cover == c_is_cover:
                score += 50
            else:
                score -= 50
            # Keep 4-square boxes with other 4-square / square boxes, not handy or single-gang switch boxes
            t_kind = ga.box_kind(target)
            c_kind = ga.box_kind(c)
            if t_kind and c_kind:
                if t_kind == c_kind:
                    score += 80
                elif (t_kind, c_kind) in (("4sq", "4-11/16"), ("4-11/16", "4sq")):
                    score += 30
                else:
                    score -= 60
            # Enclosure climate control / fans: keep filter fans with filter fans, and panels with panels
            t_is_fan = bool(re.search(r"filterfan|filter fan|exhaust filter|fan package|climate", target_text, re.I))
            c_is_fan = bool(re.search(r"filterfan|filter fan|exhaust filter|fan package|climate", c_text, re.I))
            if t_is_fan and c_is_fan:
                score += 120
            elif t_is_fan != c_is_fan:
                score -= 60

            # Box extenders / spacer shims: keep extenders with extenders, not junction boxes
            t_is_ext = bool(re.search(r"box extender|\bbe[1-4]r?\b|spacer shims|receptacle shim", target_text, re.I))
            c_is_ext = bool(re.search(r"box extender|\bbe[1-4]r?\b|spacer shims|receptacle shim", c_text, re.I))
            if t_is_ext and c_is_ext:
                score += 140
            elif t_is_ext != c_is_ext:
                score -= 60

        # Building Wire: Keep Romex with Romex, THHN with THHN, Aluminum with Aluminum
        if "Building Wire" in target_node or "Armored Cable" in target_node:
            t_is_romex = bool(re.search(r"nm-b|romex|sheathed", target_text, re.I))
            c_is_romex = bool(re.search(r"nm-b|romex|sheathed", c_text, re.I))
            if t_is_romex and c_is_romex:
                score += 100
            elif t_is_romex != c_is_romex:
                score -= 100
            t_is_mc = bool(re.search(r"\bmc\b|metal clad|armored|hcf-ac", target_text, re.I))
            c_is_mc = bool(re.search(r"\bmc\b|metal clad|armored|hcf-ac", c_text, re.I))
            if t_is_mc and c_is_mc:
                score += 100
            elif t_is_mc != c_is_mc:
                score -= 100
            t_is_al = bool(re.search(r"\bal\b|alum|xhhw|mhf|ser\b", target_text, re.I))
            c_is_al = bool(re.search(r"\bal\b|alum|xhhw|mhf|ser\b", c_text, re.I))
            if t_is_al != c_is_al:
                score -= 60
            t_g = ga.gauge_range(target)
            c_g = ga.gauge_range(c)
            if t_g and c_g and abs(t_g[0] - c_g[0]) < 1e-4:
                score += 80
                # Extra boost if exact conductor count matches (e.g. 12/2 with 12/2)
                t_cond = re.search(r"\b(\d{1,2})/([2-4])\b", target_text)
                c_cond = re.search(r"\b(\d{1,2})/([2-4])\b", c_text)
                if t_cond and c_cond and t_cond.groups() == c_cond.groups():
                    score += 60
            # Health Care Facility (HCF) cable affinity
            t_hcf = bool(re.search(r"hcf|health care", target_text, re.I))
            c_hcf = bool(re.search(r"hcf|health care", c_text, re.I))
            if t_hcf and c_hcf:
                score += 80

        # Conduit: Keep EMT with EMT, GRC/Rigid with GRC/Rigid, PVC with PVC, Sch 40 with Sch 40, Sch 80 with Sch 80
        if "Conduit - Metallic" in target_node or "Conduit - Non-Metallic" in target_node:
            t_is_emt = bool(re.search(r"\bemt\b", target_text, re.I))
            c_is_emt = bool(re.search(r"\bemt\b", c_text, re.I))
            if t_is_emt and c_is_emt:
                score += 40
            elif t_is_emt != c_is_emt:
                score -= 40
            t_is_pvc = bool(re.search(r"\bpvc\b", target_text, re.I))
            c_is_pvc = bool(re.search(r"\bpvc\b", c_text, re.I))
            if t_is_pvc and c_is_pvc:
                # Match Schedule 40 vs Schedule 80
                t_sch80 = bool(re.search(r"sched(?:ule)?[- ]?80|sch[- ]?80", target_text, re.I))
                c_sch80 = bool(re.search(r"sched(?:ule)?[- ]?80|sch[- ]?80", c_text, re.I))
                if t_sch80 == c_sch80:
                    score += 60
                else:
                    score -= 30
                # Trade size proximity match
                t_sz = ga.trade_sizes(target)
                c_sz = ga.trade_sizes(c)
                if t_sz and c_sz:
                    diff = abs(t_sz[0] - c_sz[0])
                    if diff < 1e-4:
                        score += 50
                    elif diff <= 0.5:
                        score += 30
                    else:
                        score -= 20

        # Conduit Fittings & Straps: Keep coupling with coupling, connector with connector, elbow with elbow, strap with strap
        if "Fittings" in target_node or "Straps" in target_node:
            if re.search(r"coupling|cplg", target_text, re.I) and re.search(r"coupling|cplg", c_text, re.I):
                score += 60
            elif re.search(r"connector|conn\b", target_text, re.I) and re.search(r"connector|conn\b", c_text, re.I):
                score += 60
            elif re.search(r"elbow|elb\b", target_text, re.I) and re.search(r"elbow|elb\b", c_text, re.I):
                score += 60
            elif re.search(r"\bstrap\b", target_text, re.I) and re.search(r"\bstrap\b", c_text, re.I):
                score += 80
                # Match 1-hole with 1-hole, 2-hole with 2-hole
                t_2h = bool(re.search(r"2\s*(?:-| )?hole|th-18\d{2}", target_text, re.I))
                c_2h = bool(re.search(r"2\s*(?:-| )?hole|th-18\d{2}", c_text, re.I))
                t_1h = bool(re.search(r"1\s*(?:-| )?hole|th-19\d{2}|\b190\d\b", target_text, re.I)) and not t_2h
                c_1h = bool(re.search(r"1\s*(?:-| )?hole|th-19\d{2}|\b190\d\b", c_text, re.I)) and not c_2h
                if (t_2h and c_2h) or (t_1h and c_1h):
                    score += 120
                elif (t_2h and not c_2h) or (t_1h and not c_1h):
                    score -= 80
                # Boost matching conduit trade size
                t_sz = ga.trade_sizes(target)
                c_sz = ga.trade_sizes(c)
                if t_sz and c_sz:
                    if abs(t_sz[0] - c_sz[0]) < 1e-4:
                        score += 100
                    else:
                        score -= 40

        # Conduit Bodies: Keep replacement covers & gaskets with other covers & gaskets, and complete bodies with complete bodies
        if "Conduit Bodies" in target_node:
            t_is_cvr = bool(re.search(r"wedgenut|cover for form|gasket|cvr/gask|cvr only|\bgask\b", target_text, re.I)) and not bool(re.search(r"conduit body complete|\bcndt body\b|\bcndt outlet body\b", target_text, re.I))
            c_is_cvr = bool(re.search(r"wedgenut|cover for form|gasket|cvr/gask|cvr only|\bgask\b", c_text, re.I)) and not bool(re.search(r"conduit body complete|\bcndt body\b|\bcndt outlet body\b", c_text, re.I))
            if t_is_cvr and c_is_cvr:
                score += 150
            elif t_is_cvr != c_is_cvr:
                score -= 60
            # Form style matching (Form 7 vs Form 8 vs Form 35 vs Form 85)
            # Note: GASK57x are standard Form 7 gaskets (Killark/Appleton/Crouse-Hinds Form 7 series)
            t_form = re.search(r"form\s*([0-9]{1,2})|\bfm([0-9]{1,2})\b|gask5([0-9])", target_text, re.I)
            c_form = re.search(r"form\s*([0-9]{1,2})|\bfm([0-9]{1,2})\b|gask5([0-9])", c_text, re.I)
            if t_form and c_form:
                t_f_num = t_form.group(1) or t_form.group(2) or t_form.group(3)
                c_f_num = c_form.group(1) or c_form.group(2) or c_form.group(3)
                if t_f_num == c_f_num:
                    score += 80

        # Cable Management: Keep cable ties with cable ties, staples with staples
        if "Cable Management" in target_node:
            t_is_tie = bool(re.search(r"cable tie|ty-rap|ty5", target_text, re.I))
            c_is_tie = bool(re.search(r"cable tie|ty-rap|ty5", c_text, re.I))
            t_is_staple = bool(re.search(r"staple", target_text, re.I))
            c_is_staple = bool(re.search(r"staple", c_text, re.I))
            if (t_is_tie and c_is_tie) or (t_is_staple and c_is_staple):
                score += 50
            elif (t_is_tie and not c_is_tie) or (t_is_staple and not c_is_staple):
                score -= 50

        # Heavy duty receptacles (Dryer/Range/Welder): Keep with other heavy duty, not standard 15A duplex
        if "Receptacles" in target_node:
            t_heavy = bool(re.search(r"dryer|range|welder|30a|50a|14-30|14-50|6-50|10-30", target_text, re.I))
            c_heavy = bool(re.search(r"dryer|range|welder|30a|50a|14-30|14-50|6-50|10-30", c_text, re.I))
            if t_heavy == c_heavy:
                score += 50
                # Match specific application: dryer (14-30, 10-30, 30A 125/250V) vs range/ev (14-50, 50A)
                t_dryer = bool(re.search(r"dryer|14-30|10-30|3864|3860|3884|9430", target_text, re.I))
                c_dryer = bool(re.search(r"dryer|14-30|10-30|3864|3860|3884|9430", c_text, re.I))
                if t_dryer and c_dryer:
                    score += 80
                elif t_dryer != c_dryer:
                    score -= 40
            else:
                score -= 50

        # Lighting: Keep downlights with downlights, wallpacks with wallpacks, floods with floods, vanity with vanity
        if "Indoor Lighting" in target_node or "Outdoor Lighting" in target_node:
            t_lt = _lighting_type(target_text)
            c_lt = _lighting_type(c_text)
            if t_lt and c_lt:
                if t_lt == c_lt:
                    score += 80
                else:
                    score -= 50
            elif t_lt and not c_lt:
                # Target has a specific fixture type but candidate doesn't match any known fixture
                score -= 40
            # For vanity and flush mount residential fixtures, prefer other decorative/residential fixtures
            if t_lt in ("vanity", "flush_mount"):
                if c_lt in ("vanity", "flush_mount"):
                    score += 60
                elif c_lt in ("undercabinet_tape", "accessory_guard", "high_bay"):
                    score -= 40
                # Match fixture finish/color (e.g. white with white, chrome with chrome/nickel/silver)
                t_finish = re.search(r"chrome|nickel|bronze|white|black", target_text, re.I)
                c_finish = re.search(r"chrome|nickel|bronze|white|black", c_text, re.I)
                if t_finish and c_finish and t_finish.group(0).lower() == c_finish.group(0).lower():
                    score += 30

            # Downlights / Canless Wafers: Boost matching downlight aperture size (4", 6", 8") and commercial slim style
            if t_lt == "downlight" and c_lt == "downlight":
                # Check aperture size: 4in, 6in, 8in
                t_sz = re.search(r"\b([468])\s*(?:in|inch|\"|(?=[a-z]))", target_text, re.I)
                c_sz = re.search(r"\b([468])\s*(?:in|inch|\"|(?=[a-z]))", c_text, re.I)
                if t_sz and c_sz and t_sz.group(1) == c_sz.group(1):
                    score += 60
                # Commercial slim / multi-volt (120-277V) preference
                t_comm = bool(re.search(r"commercial|120-277|0-10v|csdl", target_text, re.I))
                c_comm = bool(re.search(r"commercial|120-277|0-10v|csdl", c_text, re.I))
                if t_comm and c_comm:
                    score += 40

            # Commercial troffers and flat panels: prioritize matching panels, troffers, and linear architectural fixtures
            if t_lt == "panel_troffer":
                if c_lt == "panel_troffer":
                    score += 100
                elif c_lt == "strip_wrap":
                    score += 40
                else:
                    score -= 60

            # Linear LED tubes: prioritize matching tubes by length (36", 48", 24", 96") and type
            if t_lt == "tube_bulb":
                if c_lt == "tube_bulb":
                    score += 100
                    # Check length: 24in/2ft, 36in/3ft, 48in/4ft, 96in/8ft
                    t_len = re.search(r"\b(24|36|48|96)\s*(?:in|inch|\"|(?=t8|t5))|\b([2348])\s*ft\b", target_text, re.I)
                    c_len = re.search(r"\b(24|36|48|96)\s*(?:in|inch|\"|(?=t8|t5))|\b([2348])\s*ft\b", c_text, re.I)
                    if t_len and c_len:
                        t_norm = t_len.group(1) or ("24" if t_len.group(2) == "2" else "36" if t_len.group(2) == "3" else "48" if t_len.group(2) == "4" else "96")
                        c_norm = c_len.group(1) or ("24" if c_len.group(2) == "2" else "36" if c_len.group(2) == "3" else "48" if c_len.group(2) == "4" else "96")
                        if t_norm == c_norm:
                            score += 60
                else:
                    score -= 60

        # Drivers: Keep linear architectural constant-current drivers with linear drivers, and 24V tape drivers with 24V tape drivers
        if "Drivers - LED" in target_node or "Drivers" in target_node:
            t_is_linear = bool(re.search(r"oti|optotronic|linear|constant current|fieldset|iota", target_text, re.I))
            c_is_linear = bool(re.search(r"oti|optotronic|linear|constant current|fieldset|iota", c_text, re.I))
            if t_is_linear and c_is_linear:
                score += 100
            elif t_is_linear != c_is_linear:
                score -= 60
            t_is_24v = bool(re.search(r"24v|omnidrive|switchex|diode", target_text, re.I))
            c_is_24v = bool(re.search(r"24v|omnidrive|switchex|diode", c_text, re.I))
            if t_is_24v and c_is_24v:
                score += 80
            elif t_is_24v != c_is_24v:
                score -= 40

        # Pump Panels / Combination Starters / Soft Starters: Keep with same starter type
        if "Starters & Contactors" in target_node:
            t_is_pp = bool(re.search(r"pump panel", target_text, re.I))
            c_is_pp = bool(re.search(r"pump panel", c_text, re.I))
            if t_is_pp == c_is_pp:
                score += 80
            else:
                score -= 80

            # Soft Starters / Solid-State Starters
            t_is_ss = bool(re.search(r"softstart|soft start|soft-start|pcs-\d{3}", target_text, re.I))
            c_is_ss = bool(re.search(r"softstart|soft start|soft-start|pcs-\d{3}", c_text, re.I))
            if t_is_ss and c_is_ss:
                score += 120
            elif t_is_ss != c_is_ss:
                score -= 60

        # Bulbs & Tubes: Keep linear tubes with linear tubes, screw-in bulbs with screw-in bulbs
        if "Bulbs" in target_node:
            t_is_tube = bool(re.search(r"\bt[58]\b|\bt12\b|linear|tube|u-bend", target_text, re.I))
            c_is_tube = bool(re.search(r"\bt[58]\b|\bt12\b|linear|tube|u-bend", c_text, re.I))
            if t_is_tube == c_is_tube:
                score += 80
            else:
                score -= 80

        # Load Centers & Panelboards: Keep actual loadcenter panels with other loadcenters, and hardware with hardware
        if "Load Centers" in target_node:
            t_is_panel = bool(re.search(r"loadcenter|load center|panelboard|interior|\bmlo\b|\bmcb\b|spaces|circuits|cir\.", target_text, re.I)) and not bool(re.search(r"filler|ground bar|neutral|screw|card|barrier", target_text, re.I))
            c_is_panel = bool(re.search(r"loadcenter|load center|panelboard|interior|\bmlo\b|\bmcb\b|spaces|circuits|cir\.", c_text, re.I)) and not bool(re.search(r"filler|ground bar|neutral|screw|card|barrier", c_text, re.I))
            if t_is_panel == c_is_panel:
                score += 80
            else:
                score -= 80
            # Pow-R-Xpress / Commercial Panelboards: keep commercial panelboard interiors with other commercial panelboard interiors
            t_is_prl = bool(re.search(r"prl[1-4]|pow-r-xpress|pow-r-stock", target_text, re.I))
            c_is_prl = bool(re.search(r"prl[1-4]|pow-r-xpress|pow-r-stock", c_text, re.I))
            if t_is_prl and c_is_prl:
                score += 120
            elif t_is_prl != c_is_prl:
                score -= 60

            # 3-Phase vs 1-Phase load centers
            t_3ph = bool(re.search(r"3\s*(?:ph|phase)|3br\d", target_text, re.I))
            c_3ph = bool(re.search(r"3\s*(?:ph|phase)|3br\d", c_text, re.I))
            if t_3ph and c_3ph:
                score += 120
            elif t_3ph != c_3ph:
                score -= 50

            # NEMA 1 indoor vs NEMA 3R outdoor enclosure for load centers
            t_n3r = bool(re.search(r"nema\s*3r|n3r|outdoor", target_text, re.I))
            c_n3r = bool(re.search(r"nema\s*3r|n3r|outdoor", c_text, re.I))
            if t_n3r == c_n3r:
                score += 30
            else:
                score -= 20

        # Relays: Keep phase/voltage/power monitors with other monitors, sockets with sockets
        if "Relays" in target_node:
            t_is_mon = bool(re.search(r"monitor|phase loss", target_text, re.I))
            c_is_mon = bool(re.search(r"monitor|phase loss", c_text, re.I))
            if t_is_mon == c_is_mon:
                score += 80
            else:
                score -= 80

        # Bushings & Locknuts: Keep bushings with bushings, locknuts with locknuts, KO seals with KO seals
        if "Bushing / K.O. Seal / Locknuts" in target_node:
            t_is_bushing = bool(re.search(r"bushing", target_text, re.I))
            c_is_bushing = bool(re.search(r"bushing", c_text, re.I))
            t_is_locknut = bool(re.search(r"locknut|lock nut", target_text, re.I))
            c_is_locknut = bool(re.search(r"locknut|lock nut", c_text, re.I))
            if (t_is_bushing and c_is_bushing) or (t_is_locknut and c_is_locknut):
                score += 80
            elif (t_is_bushing and not c_is_bushing) or (t_is_locknut and not c_is_locknut):
                score -= 80
            # For plastic/conduit bushings, strongly favor matching trade size first
            t_sizes = ga.trade_sizes(target)
            c_sizes = ga.trade_sizes(c)
            if t_sizes and c_sizes:
                if abs(t_sizes[0] - c_sizes[0]) < 1e-4:
                    score += 100
                else:
                    score -= 40
            t_is_plastic = bool(re.search(r"plastic|nylon", target_text, re.I))
            c_is_plastic = bool(re.search(r"plastic|nylon", c_text, re.I))
            if t_is_plastic == c_is_plastic:
                score += 30

        # Strut: Keep strut channel with channel, and fittings/spring nuts with fittings
        if "Strut" in target_node:
            t_is_chan = bool(re.search(r"channel|\bstrut\b.*(?:10'|20'|10ft|20ft|1-5/8|7/8|deep|shallow)|\bsec\b.*strut", target_text, re.I)) and not bool(re.search(r"nut|fitting|clamp|strap|angle|bracket|post base|end cap|washer", target_text, re.I))
            c_is_chan = bool(re.search(r"channel|\bstrut\b.*(?:10'|20'|10ft|20ft|1-5/8|7/8|deep|shallow)|\bsec\b.*strut", c_text, re.I)) and not bool(re.search(r"nut|fitting|clamp|strap|angle|bracket|post base|end cap|washer", c_text, re.I))
            if t_is_chan == c_is_chan:
                score += 80
            else:
                score -= 80

        # Marking & Labeling: Keep pre-printed decals/labels with other decals, and wire markers with wire markers
        if "Marking & Labeling" in target_node:
            t_is_decal = bool(re.search(r"wop label|warning label|disconnect|arc flash|placard", target_text, re.I))
            c_is_decal = bool(re.search(r"wop label|warning label|disconnect|arc flash|placard", c_text, re.I))
            if t_is_decal == c_is_decal:
                score += 80
            else:
                score -= 80

        # Lugs & Split Bolts: Keep Unitaps / multi-port splice reducers with other Unitaps / splice reducers
        if "Lugs & Split Bolts" in target_node:
            t_is_unitap = bool(re.search(r"unitap|uni-tap|splice reducer|\bbibd\b|\bpbtd\b|\bpbts\b", target_text, re.I))
            c_is_unitap = bool(re.search(r"unitap|uni-tap|splice reducer|\bbibd\b|\bpbtd\b|\bpbts\b", c_text, re.I))
            if t_is_unitap and c_is_unitap:
                score += 100
                # Further boost matching conductor range / gauge overlap
                t_g = ga.gauge_range(target)
                c_g = ga.gauge_range(c)
                if t_g and c_g:
                    if max(t_g[0], c_g[0]) <= min(t_g[1], c_g[1]):
                        score += 40
            elif t_is_unitap != c_is_unitap:
                score -= 40

            # C Taps / Hytaps / Compression Splices: Keep C-taps with other C-taps
            t_is_ctap = bool(re.search(r"c tap|c-tap|hytap|ygfc|yghc|yc[0-9]c", target_text, re.I))
            c_is_ctap = bool(re.search(r"c tap|c-tap|hytap|ygfc|yghc|yc[0-9]c", c_text, re.I))
            if t_is_ctap and c_is_ctap:
                score += 120
            elif t_is_ctap != c_is_ctap:
                score -= 60

        # Grounding Products: Keep pipe chokes / pipe grounding hubs together with other pipe chokes / pipe grounding clamps
        if "Grounding Products" in target_node:
            # Ground screws & pigtails affinity
            t_is_pig = bool(re.search(r"ground screw|grounding screw|pigtail|grounding clip", target_text, re.I))
            c_is_pig = bool(re.search(r"ground screw|grounding screw|pigtail|grounding clip", c_text, re.I))
            if t_is_pig and c_is_pig:
                score += 150
            elif t_is_pig != c_is_pig:
                score -= 60

            t_is_choke = bool(re.search(r"pipe choke|hub\d+l\b|grounding hub|pipe ground|water pipe", target_text, re.I))
            c_is_choke = bool(re.search(r"pipe choke|hub\d+l\b|grounding hub|pipe ground|water pipe", c_text, re.I))
            if t_is_choke and c_is_choke:
                score += 100
                if re.search(r"pipe choke|hub\d+l\b", target_text, re.I) and re.search(r"pipe choke|hub\d+l\b", c_text, re.I):
                    score += 60
            elif t_is_choke != c_is_choke:
                score -= 40

        # Flashings & Weatherheads: Keep weatherheads with weatherheads, flashings with flashings
        if "Flashings, Weatherhead" in target_node:
            t_is_wh = bool(re.search(r"weatherhead|entrance cap", target_text, re.I))
            c_is_wh = bool(re.search(r"weatherhead|entrance cap", c_text, re.I))
            t_is_fl = bool(re.search(r"flashing", target_text, re.I))
            c_is_fl = bool(re.search(r"flashing", c_text, re.I))
            if (t_is_wh and c_is_wh) or (t_is_fl and c_is_fl):
                score += 80
            elif (t_is_wh and not c_is_wh) or (t_is_fl and not c_is_fl):
                score -= 60
            # Boost exact trade size
            t_sizes = ga.trade_sizes(target)
            c_sizes = ga.trade_sizes(c)
            if t_sizes and c_sizes:
                if abs(t_sizes[0] - c_sizes[0]) < 1e-4:
                    score += 60
                else:
                    score -= 20

        # Disconnect Switches: Keep outdoor AC / non-fused / GFCI disconnects with outdoor AC disconnects
        if "Disconnect Switches" in target_node or "Safety Switches" in target_node:
            t_is_ac = bool(re.search(r"non-fus|nonfus|air condition|\bac\b|gfci|pullout|u065|u38", target_text, re.I))
            c_is_ac = bool(re.search(r"non-fus|nonfus|air condition|\bac\b|gfci|pullout|u065|u38", c_text, re.I))
            if t_is_ac and c_is_ac:
                score += 100
                # Extra boost if both feature GFCI or both are standard AC pullouts
                t_gfci = bool(re.search(r"gfci|gfi", target_text, re.I))
                c_gfci = bool(re.search(r"gfci|gfi", c_text, re.I))
                if t_gfci == c_gfci:
                    score += 40
            elif t_is_ac != c_is_ac:
                score -= 80

            # Heavy Duty (600V DH) vs General Duty (240V DG) safety switches
            t_is_hd = bool(re.search(r"\bdh\d|\bhd\b|600v|heavy duty", target_text, re.I))
            c_is_hd = bool(re.search(r"\bdh\d|\bhd\b|600v|heavy duty", c_text, re.I))
            if t_is_hd and c_is_hd:
                score += 80
            elif t_is_hd != c_is_hd:
                score -= 40

            # Fusible vs Non-Fusible safety switches
            t_is_fus = bool(re.search(r"fusible|\bfused\b", target_text, re.I)) and not bool(re.search(r"non[- ]?fus", target_text, re.I))
            c_is_fus = bool(re.search(r"fusible|\bfused\b", c_text, re.I)) and not bool(re.search(r"non[- ]?fus", c_text, re.I))
            if t_is_fus == c_is_fus:
                score += 60
            else:
                score -= 50

            # Number of poles (3P vs 2P)
            t_p = re.search(r"\b([23])\s*(?:p|pole)\b", target_text, re.I)
            c_p = re.search(r"\b([23])\s*(?:p|pole)\b", c_text, re.I)
            if t_p and c_p and t_p.group(1) == c_p.group(1):
                score += 40

            # NEMA 1 indoor vs NEMA 3R outdoor enclosure
            t_nema1 = bool(re.search(r"nema 1\b|nema1\b|\bngk\b|\bngb\b|\bugk\b|\bugb\b", target_text, re.I))
            c_nema1 = bool(re.search(r"nema 1\b|nema1\b|\bngk\b|\bngb\b|\bugk\b|\bugb\b", c_text, re.I))
            if t_nema1 == c_nema1:
                score += 50
            else:
                score -= 30

        # Data & Telecom Workstation: Keep data jacks (Cat5e / Cat6 / RJ45) with other data jacks
        if "Workstation" in target_node:
            t_is_jack = bool(re.search(r"\bcat ?[56][ae]?\b|rj ?45|keystone jack|data jack|tracjack", target_text, re.I))
            c_is_jack = bool(re.search(r"\bcat ?[56][ae]?\b|rj ?45|keystone jack|data jack|tracjack", c_text, re.I))
            if t_is_jack and c_is_jack:
                score += 100
                # Match category level (Cat5e with Cat5e, Cat6 with Cat6)
                t_cat = re.search(r"cat ?(5e?|6a?)", target_text, re.I)
                c_cat = re.search(r"cat ?(5e?|6a?)", c_text, re.I)
                if t_cat and c_cat and t_cat.group(1).lower().replace(" ", "") == c_cat.group(1).lower().replace(" ", ""):
                    score += 50
            elif t_is_jack != c_is_jack:
                score -= 80

        # Pilot Devices: Keep industrial pushbuttons with other pushbuttons, and selector switches with selector switches
        if "Pilot Devices" in target_node:
            t_is_pb = bool(re.search(r"pushbutton|push button|\bpb0[1-3]c\b|flush pb|momentary", target_text, re.I))
            c_is_pb = bool(re.search(r"pushbutton|push button|\bpb0[1-3]c\b|flush pb|momentary", c_text, re.I))
            if t_is_pb and c_is_pb:
                score += 100
                # Boost matching 22.5mm vs 30.5mm size
                t_22 = bool(re.search(r"22(?:\.5)?\s*mm|\bpb0\d\b", target_text, re.I))
                c_22 = bool(re.search(r"22(?:\.5)?\s*mm|\bpb0\d\b", c_text, re.I))
                if t_22 and c_22:
                    score += 50
            elif t_is_pb != c_is_pb:
                score -= 60

        # Starters & Contactors: Group Soft Starters together vs standard full-voltage starters/contactors
        if "Starters & Contactors" in target_node:
            t_is_soft = bool(re.search(r"softstart|soft start|pcs-\d{3}", target_text, re.I))
            c_is_soft = bool(re.search(r"softstart|soft start|pcs-\d{3}", c_text, re.I))
            if t_is_soft and c_is_soft:
                score += 150
            elif t_is_soft != c_is_soft:
                score -= 80

        # Safety Equipment: Keep Lockout / Tagout (LOTO) items together vs general PPE / caution tape
        if "Safety Equipment" in target_node:
            t_is_loto = bool(re.search(r"lock ?out|lockout|padlock|tagout|hasp|\bubl1\b|44-810|44-97", target_text, re.I))
            c_is_loto = bool(re.search(r"lock ?out|lockout|padlock|tagout|hasp|\bubl1\b|44-810|44-97", c_text, re.I))
            if t_is_loto and c_is_loto:
                score += 150
            elif t_is_loto != c_is_loto:
                score -= 80

        # Commercial Enclosures / CT Cabinets: Keep CT cans together vs general wireways/small junction boxes
        if "Enclosures" in target_node:
            t_is_ct = bool(re.search(r"ct can|trans encl|ct cabinet|current transformer cabinet|csuct|u4468", target_text, re.I))
            c_is_ct = bool(re.search(r"ct can|trans encl|ct cabinet|current transformer cabinet|csuct|u4468", c_text, re.I))
            if t_is_ct and c_is_ct:
                score += 150
            elif t_is_ct != c_is_ct:
                score -= 60

        # Fasteners: Keep nuts with nuts, washers with washers, bolts with bolts, and match thread/diameter
        if "Nuts, Bolts, Washers" in target_node:
            t_is_nut = bool(re.search(r"\bnut\b", target_text, re.I))
            c_is_nut = bool(re.search(r"\bnut\b", c_text, re.I))
            t_is_washer = bool(re.search(r"washer", target_text, re.I))
            c_is_washer = bool(re.search(r"washer", c_text, re.I))
            t_is_bolt = bool(re.search(r"\bbolt\b|cap screw", target_text, re.I))
            c_is_bolt = bool(re.search(r"\bbolt\b|cap screw", c_text, re.I))
            if (t_is_nut and c_is_nut) or (t_is_washer and c_is_washer) or (t_is_bolt and c_is_bolt):
                score += 100
            elif (t_is_nut != c_is_nut) or (t_is_washer != c_is_washer) or (t_is_bolt != c_is_bolt):
                score -= 60

            # Washer sub-type matching: fender washer vs lockwasher vs reducing washer vs flat washer
            if t_is_washer and c_is_washer:
                t_fender = bool(re.search(r"fender", target_text, re.I))
                c_fender = bool(re.search(r"fender", c_text, re.I))
                t_reduc = bool(re.search(r"reduc", target_text, re.I))
                c_reduc = bool(re.search(r"reduc", c_text, re.I))
                t_lock = bool(re.search(r"lock|split lw", target_text, re.I))
                c_lock = bool(re.search(r"lock|split lw", c_text, re.I))
                if (t_fender and c_fender) or (t_reduc and c_reduc) or (t_lock and c_lock):
                    score += 80
                elif (t_fender != c_fender) or (t_reduc != c_reduc) or (t_lock != c_lock):
                    score -= 40

            # Thread size matching
            t_dia = ga.fastener_diameter(target)
            c_dia = ga.fastener_diameter(c)
            if t_dia and c_dia:
                if t_dia == c_dia:
                    score += 80
                else:
                    score -= 30

        # Fuses: Match voltage rating (600V with 600V, 250V with 250V), class (RK5, Class J, Class CC), and amp ratings
        if "Fuses" in target_node:
            # Voltage matching (600V vs 250V/125V)
            t_600 = bool(re.search(r"600\s*v|trs|frsr|flsr", target_text, re.I))
            c_600 = bool(re.search(r"600\s*v|trs|frsr|flsr", c_text, re.I))
            t_250 = bool(re.search(r"250\s*v|\btr\d+r|frnr|flnr", target_text, re.I)) and not t_600
            c_250 = bool(re.search(r"250\s*v|\btr\d+r|frnr|flnr", c_text, re.I)) and not c_600
            if (t_600 and c_600) or (t_250 and c_250):
                score += 80
            elif (t_600 and c_250) or (t_250 and c_600):
                score -= 80

            # Fuse class matching: RK5, Class J, Class CC, Midget
            t_rk5 = bool(re.search(r"rk5|trs|tr\d+r|frsr|frnr", target_text, re.I))
            c_rk5 = bool(re.search(r"rk5|trs|tr\d+r|frsr|frnr", c_text, re.I))
            t_class_j = bool(re.search(r"class j|ajt|jdl|jks", target_text, re.I))
            c_class_j = bool(re.search(r"class j|ajt|jdl|jks", c_text, re.I))
            t_class_cc = bool(re.search(r"class cc|atdr|atqr|ccmr|fnq-r", target_text, re.I))
            c_class_cc = bool(re.search(r"class cc|atdr|atqr|ccmr|fnq-r", c_text, re.I))
            if (t_rk5 and c_rk5) or (t_class_j and c_class_j) or (t_class_cc and c_class_cc):
                score += 80
            elif (t_rk5 != c_rk5) or (t_class_j != c_class_j) or (t_class_cc != c_class_cc):
                score -= 40

            # Amperage proximity match
            t_amps = ga.amp_ratings(target)
            c_amps = ga.amp_ratings(c)
            if t_amps and c_amps:
                t_val = list(t_amps)[0]
                c_val = list(c_amps)[0]
                if t_val == c_val:
                    score += 50
                elif abs(t_val - c_val) <= 30:
                    score += 20

        # Circuit Breakers: Keep 1-pole with 1-pole, 2-pole with 2-pole, match series and exact amp rating
        if "Circuit Breakers" in target_node:
            t_series = ga.breaker_series(target)
            c_series = ga.breaker_series(c)
            if t_series and c_series:
                if t_series == c_series:
                    score += 80
                else:
                    score -= 80

            # Match poles (1-pole vs 2-pole)
            t_p = re.search(r"\b([123])\s*(?:p|pole)\b|hom([123])\d|qo([123])\d|br([123])\d|ch([123])\d", target_text, re.I)
            c_p = re.search(r"\b([123])\s*(?:p|pole)\b|hom([123])\d|qo([123])\d|br([123])\d|ch([123])\d", c_text, re.I)
            if t_p and c_p:
                t_pole = next(g for g in t_p.groups() if g is not None)
                c_pole = next(g for g in c_p.groups() if g is not None)
                if t_pole == c_pole:
                    score += 60
                else:
                    score -= 40

            # Match amperage
            t_amps = ga.amp_ratings(target)
            c_amps = ga.amp_ratings(c)
            if t_amps and c_amps:
                t_val = list(t_amps)[0]
                c_val = list(c_amps)[0]
                if t_val == c_val:
                    score += 50
                elif abs(t_val - c_val) <= 10:
                    score += 20

        # Cable Pulling: Keep pulling heads with other pulling heads, and pulling rope/mule tape with rope/tape
        if "Cable Pulling" in target_node:
            t_is_head = bool(re.search(r"pulling head|head\b.*(?:inch|awg|kcmil)", target_text, re.I))
            c_is_head = bool(re.search(r"pulling head|head\b.*(?:inch|awg|kcmil)", c_text, re.I))
            if t_is_head and c_is_head:
                score += 150
                # Match conductor size
                t_g = ga.gauge_range(target)
                c_g = ga.gauge_range(c)
                if t_g and c_g and abs(t_g[0] - c_g[0]) < 1e-4:
                    score += 80
                # Match head length (e.g. 6in, 12in, 18in, 24in, 30in)
                t_hl = re.search(r"(\d{1,2})\s*(?:inch|in\b|\")", target_text, re.I)
                c_hl = re.search(r"(\d{1,2})\s*(?:inch|in\b|\")", c_text, re.I)
                if t_hl and c_hl and t_hl.group(1) == c_hl.group(1):
                    score += 40
            elif t_is_head != c_is_head:
                score -= 80

        # Order volume tie-breaker
        score += min(c_orders, 500) / 10.0

        scored.append((score, c_orders, c[ID_COL]))

    scored.sort(key=lambda x: (-x[0], -x[1], x[2]))
    return [item[2] for item in scored[:10]]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default="enriched_products.csv")
    parser.add_argument("--output", default="similar_products.csv")
    parser.add_argument("--taxonomy", default=ga.TAXONOMY_PATH)
    parser.add_argument("--rank", default=ga.RANK_PATH)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    logger.info("Loading catalog from %s", args.input)
    source = pd.read_csv(args.input, dtype=str, keep_default_na=False)
    source = ga.attach_taxonomy(source, args.taxonomy)
    source = ga.attach_orders(source, args.rank)

    records = source.to_dict("records")
    source_map = {r[ID_COL]: r for r in records}

    by_node = defaultdict(list)
    for r in records:
        node = r.get(ga.TAX_COL, "")
        if node:
            by_node[node].append(r)

    logger.info("Generating similar products for %d catalog items...", len(records))
    results = []
    for r in records:
        sim_ids = find_similar_products(r, by_node, source_map)
        row = {
            OUTPUT_ID_COL: r[ID_COL],
            **dict(zip(RELATION_COLUMNS, sim_ids + [""] * (10 - len(sim_ids)))),
        }
        results.append(row)

    df_out = pd.DataFrame(results)
    ga.save_atomic(df_out, args.output)
    logger.info("Wrote %s with %d rows.", args.output, len(df_out))


if __name__ == "__main__":
    main()
