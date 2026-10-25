"""A "By element" review: library questions asked in the client's words, and
the code checks those questions (and the catalogue questions) link to."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402
import rfi_review as rr  # noqa: E402

SCOPE = {
    "library": {
        "element": {"id": "CE-03-01", "name": "Drilled piers", "variants": "Straight shaft, belled",
                    "attributes": "Diameter, bell size, tip elevation", "triggers": "Pier mark not in schedule"},
        "questions": [
            {"id": "CE-03-01-Q01", "text": "Does every pier mark on the plan appear in the pier schedule?",
             "category": "Documentation / Missing Info", "source": "library", "codeChecks": ["unscheduled_mark", "unmarked_element"]},
            {"id": "c-1", "text": "Does every pier mark … ? → Which mark is the pier at A1/BK?", "category": None,
             "source": "custom", "codeChecks": []},
        ],
    }
}


def test_library_questions_become_review_questions_in_their_own_words():
    lib = rr.library_checks(SCOPE)
    assert list(lib) == ["CE-03-01-Q01", "c-1"]
    q = lib["CE-03-01-Q01"]
    assert q["originalQuestion"] == "Does every pier mark on the plan appear in the pier schedule?"
    assert q["requiredObservations"] == ["Diameter", "bell size", "tip elevation"]
    assert "Pier mark not in schedule" in q["candidateRules"]
    assert lib["c-1"]["source"] == "custom"
    assert rr.library_checks({}) == {}


def test_prompts_carry_catalogue_and_library_questions():
    lib = rr.library_checks(SCOPE)
    catalogue = {**rr.CHECKS, **lib}
    system = rr.discovery_system(["F04", "CE-03-01-Q01", "c-1"], catalogue)
    assert "F04" in system and "Which mark is the pier at A1/BK?" in system
    assert "Pier mark not in schedule" in rr.reasoning_system(["CE-03-01-Q01"], catalogue)
    # The default catalogue is untouched for every other caller.
    assert "CE-03-01-Q01" not in rr.discovery_system(["F04"])


def test_code_checks_credit_every_question_they_answer():
    lib = rr.library_checks(SCOPE)
    linked = rr.code_checks_for(["F04", "G01", "CE-03-01-Q01", "c-1"], lib)
    assert linked["unmarked_element"] == ["F04", "CE-03-01-Q01"]
    assert linked["unscheduled_mark"] == ["CE-03-01-Q01"]
    assert "grid_mismatch" in linked and linked["grid_mismatch"] == ["G01"]
    assert rr.code_checks_for(["c-1"], lib) == {}


def test_outcomes_cover_library_questions():
    lib = rr.library_checks(SCOPE)
    asked = ["F04", *lib]
    results = rr.check_results(list(rr.CHECKS) + list(lib), asked, {}, [], [], [], {"CE-03-01-Q01": 1})
    assert results["CE-03-01-Q01"]["outcome"] == "candidate_found"
    assert results["c-1"]["outcome"] == "insufficient_evidence"
    assert results["G02"]["outcome"] == "not_selected"


def test_a_mark_finding_answers_only_its_own_elements_library_questions():
    pile = rr.rfi_checks.Finding("unmarked_element", "fp", "high", "Element with no type mark", "?", [],
                                 facts={"examples": ["P30-12", "P24-10"]})
    angle = rr.rfi_checks.Finding("unscheduled_mark", "fp2", "medium", "A8 not scheduled", "?", [], facts={"mark": "A8"})
    ref = rr.rfi_checks.Finding("dangling_reference", "fp3", "high", "S-501 missing", "?", [])
    linked = {"unmarked_element": ["F04", "CE-12-01-U-01"], "unscheduled_mark": ["CE-12-01-U-01"],
              "dangling_reference": ["CE-12-01-U-07"]}
    bolts = {"name": "Anchor bolts", "variants": "Column anchor bolts"}
    piers = {"name": "Drilled piers / caissons", "variants": "Straight shaft"}
    assert rr.credited(pile, linked, bolts) == ["F04"]  # the catalogue question keeps its link
    assert rr.credited(pile, linked, piers) == ["F04", "CE-12-01-U-01"]
    assert rr.credited(angle, linked, piers) == []  # unknown prefix: no library question
    assert rr.credited(ref, linked, bolts) == ["CE-12-01-U-07"]  # not a mark check
    assert rr.credited(pile, linked, {"name": "Pile caps", "variants": "Single, 2-pile caps"}) == ["F04"]


def test_the_ai_is_told_what_the_code_checked_found_or_not():
    pile = rr.rfi_checks.Finding("unmarked_element", "fp", "high", "Element with no type mark", "?", [])
    block = rr.checked_by_code_block(["unmarked_element", "unscheduled_mark", "grid_mismatch"], [pile], ["S2.20", "S2.30-B"])
    assert "(S2.20, S2.30-B)" in block
    assert "an element drawn with no type mark: 1 found" in block
    assert "a mark with no row in its schedule: nothing found" in block
    assert "grid_mismatch" not in block and "not proof" in block
    assert rr.checked_by_code_block([], [], ["S2.20"]) == ""


def test_parse_length_reads_one_printed_length_and_nothing_else():
    assert rr.parse_length('8"') == 8
    assert rr.parse_length("1'-4\"") == 16
    assert rr.parse_length("1' - 4 1/2\" thick") == 16.5
    assert rr.parse_length("-23'-0\"") == -276
    assert rr.parse_length('8" (concrete)') == 8
    for not_one in ['#5 @ 10" EA WAY', "verify with mfr", '8" or 10"', "", None, "18 x 24"]:
        assert rr.parse_length(not_one) is None, not_one


def _row(source, value, ev, *, entity="Elevator pit slab", location="BH/B7", field="thickness", check="CE-05-01-U-03"):
    return {"checkId": check, "entity": entity, "location": location, "source": source,
            "fields": [{"name": field, "value": value, "state": "supported", "evidenceIds": ev, "derived": False}]}


def test_two_sources_printing_different_lengths_make_one_conflict_leland_rfi_007():
    inv = [_row("detail 3/S4.00", '8"', ["ev3"]), _row("detail 4/S4.00", "1'-4\"", ["ev4"]),
           _row("detail 5/S4.00", "1'-4\"", ["ev5"])]
    [c] = rr.value_conflicts(inv)
    assert c["kind"] == "conflict" and c["fromValues"] and set(c["evidenceIds"]) == {"ev3", "ev4"}
    assert "detail 3/S4.00 gives 8\"" in c["issue"] and "detail 4/S4.00 gives 1'-4\"" in c["issue"]


def test_no_conflict_when_values_agree_sources_overlap_or_elements_differ():
    assert rr.value_conflicts([_row("detail 4", "1'-4\"", ["ev4"]), _row("detail 5", '16"', ["ev5"])]) == []
    assert rr.value_conflicts([_row("detail 3", '8"', ["ev3"]), _row("detail 4", "1'-4\"", ["ev3"])]) == []
    assert rr.value_conflicts([_row("detail 3", '8"', ["ev3"], location="A1"), _row("detail 4", "1'-4\"", ["ev4"], location="B7")]) == []
    assert rr.value_conflicts([_row("detail 3", '8"', ["ev3"], field="slab"), _row("detail 4", "1'-4\"", ["ev4"], field="wall")]) == []
    # a value that is not one length is never compared
    assert rr.value_conflicts([_row("d3", '#5 @ 10"', ["ev3"]), _row("d4", '#6 @ 12"', ["ev4"])]) == []


def test_a_location_that_only_names_the_detail_is_not_a_place():
    # The real Sonnet run wrote each row's location as its own detail: still one pit.
    inv = [_row("detail 3/S4.00", '8"', ["ev3"], location="S4.00 detail 3"),
           _row("detail 4/S4.00", "1'-4\"", ["ev4"], location="S4.00 detail 4")]
    assert len(rr.value_conflicts(inv)) == 1
    assert rr._place("detail 3 at BH/B7") == rr._place("B7-BH") == "b7 bh"
    assert rr._place("3/S4.00") == rr._place("Sheet A0.31") == ""
    # real places still keep two rows apart
    assert rr.value_conflicts([_row("d3", '8"', ["ev3"], location="S4.00 detail 3 at A1"),
                               _row("d4", "1'-4\"", ["ev4"], location="S4.00 detail 4 at B7")]) == []


def test_the_guard_lets_the_codes_two_detail_conflict_stand_on_one_sheet_only():
    ev = {e: rr.Evidence(id=e, kind="crop", document_id="d", page_number=73, combined_page_number=73,
                         sheet_number="S4.00", side=None, bbox={"x": 1, "y": 1, "width": 9, "height": 9})
          for e in ("ev3", "ev4")}
    [c] = rr.value_conflicts([_row("detail 3/S4.00", '8"', ["ev3"]), _row("detail 4/S4.00", "1'-4\"", ["ev4"])])
    decision = {"subject": "Elevator pit slab thickness 8\" vs 1'-4\"", "question": "Which governs at BH/B7: 8\" or 1'-4\"?"}
    assert rr.guard(c, decision, ev, []) is None  # the printed values ground the numbers
    model_made = dict(c, fromValues=False)
    assert rr.guard(model_made, decision, ev, []) == "a conflict needs two sources, and it cites one page"


def _detail_sheet(rotation=0):
    """A detail sheet as a person SEES it: titles under their details in display
    space. A rotated CAD sheet stores its content sideways so it displays
    upright, so text is placed through the derotation and turned with the page."""
    import fitz
    doc = fitz.open()
    page = doc.new_page(width=1600, height=1200) if rotation in (0, 180) else doc.new_page(width=1200, height=1600)
    page.set_rotation(rotation)

    def put(x, y, text, size):
        page.insert_text(fitz.Point(x, y) * page.derotation_matrix, text, fontsize=size, rotate=rotation)

    for x, title, inside in ((100, "ELEVATOR PIT DETAIL", "SLABA8IN"), (600, "SUMP PIT DETAIL", "SUMPX"), (1100, "ELEVATOR PIT DETAIL", "SLABB16IN")):
        put(x + 40, 300, inside, 12)       # drawn above its title
        put(x, 560, title, 14)
        put(x, 580, 'SCALE: 3/4" = 1\'-0"', 9)
    put(100, 900, "SEE ELEVATOR PIT", 10)
    put(100, 920, 'SCALE: 1/8" = 1\'-0"', 9)  # a note over a scale is still not a title
    return doc, page


def test_close_ups_aim_at_the_details_whose_titles_name_the_element():
    assert rr.element_title_words("Elevator pits") == [["ELEVATOR", "PIT"]]
    assert rr.element_title_words("Drilled piers / caissons (CIDH)") == [
        ["DRILLED", "PIER"], ["CAISSON"], ["DRILLED", "FOOTING"], ["DRILLED", "SHAFT"]]
    # Drafters' names for an element count too (Leland: PIER CAP, BASEMENT WALL).
    assert ["PIER", "CAP"] in rr.element_title_words("Pile caps")
    assert ["BASEMENT", "WALL"] in rr.element_title_words("Retaining walls (building)")
    for rotation in (0, 90):
        doc, page = _detail_sheet(rotation)
        regions = rr.detail_regions(page, [["ELEVATOR", "PIT"]], 3)
        inside = [" ".join(w[4] for w in page.get_text("words", clip=r)) for r in regions]
        assert len(regions) == 2, (rotation, inside)
        assert any("SLABA8IN" in t for t in inside) and any("SLABB16IN" in t for t in inside), (rotation, inside)
        assert not any("SUMPX" in t for t in inside), (rotation, inside)
        assert rr.detail_regions(page, [["ELEVATOR", "PIT"]], 1) == regions[:1]
        assert rr.detail_regions(page, [], 3) == [] and rr.detail_regions(page, [["PILE", "CAP"]], 3) == []


def test_a_title_touching_its_scale_line_is_still_a_title_and_its_cell_reaches_the_next_column():
    import fitz
    doc = fitz.open()
    page = doc.new_page(width=1600, height=1200)
    page.insert_text((150, 300), "CAPA", fontsize=12)
    page.insert_text((700, 300), "CAPB", fontsize=12)              # still this detail's cell
    page.insert_text((100, 580), "24/S0.03 COLUMN PIER CAP PLANS", fontsize=19)
    page.insert_text((190, 584), 'SCALE: 3/4" = 1\'-0"', fontsize=9)  # overlaps the title, starts right of it
    page.insert_text((1000, 580), "OTHER DETAIL", fontsize=19)
    page.insert_text((1000, 600), 'SCALE: 3/4" = 1\'-0"', fontsize=9)
    [region] = rr.detail_regions(page, rr.element_title_words("Pile caps"), 3)
    words = " ".join(w[4] for w in page.get_text("words", clip=region))
    assert "CAPA" in words and "CAPB" in words and "OTHER" not in words


def test_a_set_aside_proposal_keeps_what_keep_as_rfi_needs_and_only_grounded_wording():
    ev = {
        "E1": rr.Evidence(id="E1", kind="text", document_id="d", page_number=4, combined_page_number=4,
                          sheet_number="S4.00", side=None, bbox={"x": 1, "y": 2, "width": 3, "height": 4},
                          text="PIER P-3 BOTTOM ELEVATION SEE CIVIL"),
        "E2": rr.Evidence(id="E2", kind="description", document_id="d", page_number=4, combined_page_number=4,
                          sheet_number="S4.00", side=None, bbox=None, text="a pier"),
    }
    candidate = {"checkId": "F04", "kind": "missing", "element": "P-3", "location": "S4.00",
                 "issue": "Bottom elevation of P-3 is not given.", "evidenceIds": ["E1", "E2"]}
    ok = {"subject": "P-3 bottom elevation", "question": "What is the bottom elevation of P-3?"}
    entry = rr.set_aside_entry(candidate, "the detail refers to civil", "model", ok, ev, [])
    assert entry["subject"] == ok["subject"] and entry["questionSource"] == "model"
    assert [e["evidenceId"] for e in entry["evidence"]] == ["E1"]  # a description pins nothing
    assert entry["fingerprint"] == rr.fingerprint(candidate, ev, [])
    # Wording that names something the evidence lacks falls back to the template.
    invented = {"subject": "P-3 at 12 ft", "question": "Is P-3 at 12 ft below grade?"}
    entry = rr.set_aside_entry(candidate, "rejected", "model", invented, ev, [])
    assert entry["questionSource"] == "template" and "12" not in entry["question"]
    # A guard rejection never reuses the model's wording, however grounded.
    assert rr.set_aside_entry(candidate, "x", "guard", ok, ev, [])["questionSource"] == "template"


def _tagged_plan(rotation: int):
    """A pier plan as a person SEES it: 40 typical tags with their elevation
    printed under each, and a core of four unusual ones near the top left."""
    import fitz
    doc = fitz.open()
    page = doc.new_page(width=3456, height=2592) if rotation in (0, 180) else doc.new_page(width=2592, height=3456)
    page.set_rotation(rotation)

    def put(x, y, text):
        page.insert_text(fitz.Point(x, y) * page.derotation_matrix, text, fontsize=8, rotate=rotation)

    for i in range(40):
        x, y = 1500 + (i % 8) * 200, 1200 + (i // 8) * 250
        put(x, y, "P30-18")
        put(x, y + 10, "(-26'-2\")")
    for x, y, tag, value in ((700, 700, "P42-10", "(-29'-10\")"), (780, 760, "P24-10", "(-29'-10\")"),
                             (860, 700, "GB4", "(-29'-10\")"), (760, 820, "P36-30", "(-26'-2\")")):
        put(x, y, tag)
        put(x, y + 10, value)
    return doc, page


def test_a_plans_close_up_goes_where_it_departs_from_its_own_pattern():
    """Leland RFIs 002 and 004 are in S2.30-B's shear-wall core, where its piers
    stop being typical; the review's one close-up was the whole sheet again."""
    import fitz
    for rotation in (0, 90):
        doc, page = _tagged_plan(rotation)
        [region] = rr.plan_regions(page, 1)
        shown = fitz.Rect(region * page.rotation_matrix).normalize()
        for word in page.get_text("words"):
            if word[4] in ("P42-10", "P24-10", "GB4", "P36-30"):
                centre = (fitz.Rect(word[:4]).tl + fitz.Rect(word[:4]).br) / 2
                assert region.contains(centre), (rotation, word[4])  # unrotated space, as stored
        assert shown.width < page.rect.width / 2 and shown.height < page.rect.height / 2  # a close-up, not the sheet
        assert rr.plan_regions(page, 0) == []


def test_a_sheet_without_enough_tags_or_without_rare_ones_gets_no_plan_close_up():
    import fitz
    doc = fitz.open()
    page = doc.new_page(width=3456, height=2592)
    for i in range(40):
        page.insert_text((1500 + (i % 8) * 200, 1200 + (i // 8) * 250), "P30-18", fontsize=8)
        page.insert_text((1500 + (i % 8) * 200, 1210 + (i // 8) * 250), "(-26'-2\")", fontsize=8)
    assert rr.plan_regions(page, 2) == []  # every tag typical
    assert rr.plan_regions(fitz.open().new_page(width=3456, height=2592), 2) == []


# --- Accuracy pass (cleaned up from an outside patch) -------------------------


def test_parse_length_reads_datums_unicode_quotes_and_hyphen_fractions():
    assert rr.parse_length('EL. 104\'-6"') == 1254
    assert rr.parse_length('T.O.S. -23\'-0"') == -276
    assert rr.parse_length("1′-4-1/2″") == 16.5
    assert rr.parse_length('+12\'-4"') == 148
    assert rr.parse_length('8" min') == 8 and rr.parse_length('24" dia.') == 24
    assert rr.parse_length('1/0"') is None  # a zero denominator is not a length


def test_value_conflicts_matches_field_names_and_grid_pairs_written_differently():
    """Leland RFI 007's shape: one detail says "pit slab thickness" at "Grid
    BH/B7", another "thickness" at "B7-BH". Same element, same field."""
    inv = [
        _row("detail 3/S4.00", '8"', ["ev3"], field="pit slab thickness", location="Grid BH/B7"),
        _row("detail 4/S4.00", "1'-4\"", ["ev4"], field="thickness", location="B7-BH"),
    ]
    [c] = rr.value_conflicts(inv)
    assert set(c["evidenceIds"]) == {"ev3", "ev4"}
    # the plural/abbreviated spellings are one field too
    assert rr.same_field("Footing", "thk", "thickness") and rr.same_field("Footing", "thicknesses", "thickness")


@pytest.mark.parametrize("a, b, entity, field_a, field_b", [
    # two DIFFERENT measurements of a two-part element — the stripped names agree, the measurements do not
    ('12"', '8"', "Elevator pit wall and slab", "wall thickness", "slab thickness"),
    ('12"', '8"', "Elevator pit wall and slab", "wall thickness", "thickness"),
    # a minimum and a maximum of one field
    ('8" min', '12" max', "Slab", "thickness", "thickness"),
    # a top and a bottom elevation filed under one field name
    ('T.O.F. -2\'-0"', 'B.O.F. -4\'-0"', "Footing", "elevation", "elevation"),
])
def test_different_measurements_are_never_a_conflict(a, b, entity, field_a, field_b):
    inv = [_row("detail 1", a, ["e1"], entity=entity, field=field_a), _row("detail 2", b, ["e2"], entity=entity, field=field_b)]
    assert rr.value_conflicts(inv) == []


def test_a_bare_elevation_and_an_el_prefixed_one_still_compare():
    inv = [_row("plan", 'EL. 100\'-0"', ["e1"], entity="Footing", field="elevation"),
           _row("detail", '98\'-0"', ["e2"], entity="Footing", field="elevation")]
    assert len(rr.value_conflicts(inv)) == 1


def test_title_synonyms_are_keyed_on_the_librarys_own_names():
    assert ["PAD", "FOOTING"] in rr.element_title_words("Isolated / spread footings")
    assert ["TIE", "BEAM"] in rr.element_title_words("Grade beams")
    assert ["WELD", "PLATE"] in rr.element_title_words("Embed plates & weld plates")
    assert ["ANCHOR", "ROD"] in rr.element_title_words("Anchor bolts")


def test_a_bare_scale_ratio_or_nts_is_a_scale_line():
    for line in ['3/4" = 1\'-0"', '1 1/2" = 1\'-0"', "N.T.S.", "NOT TO SCALE", "SCALE: 1/4\" = 1'-0\""]:
        assert rr._SCALE_LINE.match(line), line
    assert not rr._SCALE_LINE.match('SLAB 8" THICK')


def test_mark_families_do_not_answer_the_wrong_element():
    linked = {"unscheduled_mark": ["Q"]}
    def mark(m):
        return rr.rfi_checks.Finding("unscheduled_mark", m, "high", m, "?", [], facts={"mark": m})
    wall_footings = {"name": "Continuous / strip / wall footings"}
    assert rr.credited(mark("WF-2"), linked, wall_footings) == ["Q"]
    assert rr.credited(mark("W-1"), linked, wall_footings) == []
    assert rr.credited(mark("C-4"), linked, {"name": "Columns"}) == ["Q"]
    assert rr.credited(mark("C-4"), linked, {"name": "Column blockouts / diamonds"}) == []
    assert rr.credited(mark("C-4"), linked, {"name": "Drop panels / shear caps / column capitals"}) == []


def test_merged_details_reads_a_source_naming_several_details():
    assert rr.merged_details("details 3 and 4/S4.00 ELEVATOR PIT DETAIL") == ["3", "4"]
    assert rr.merged_details("3 & 4/S4.00") == ["3", "4"]
    assert rr.merged_details("Sections 2, 5A/S5.10") == ["2", "5A"]
    for one in ["detail 3/S4.00", "S4.00 detail 5", "pier schedule on S2.30-A", "#4 @ 12 and 16", "", None]:
        assert rr.merged_details(one) == [], one


def _ev(eid, page, kind="crop"):
    return rr.Evidence(id=eid, kind=kind, document_id="d", page_number=page, combined_page_number=page,
                       sheet_number="S4.00", side=None, bbox={"x": 1, "y": 1, "width": 9, "height": 9})


def test_a_merged_source_is_asked_again_one_detail_at_a_time_leland_rfi_007():
    # What Sonnet 5.5 returned: details 3 and 4 as ONE entry, so only detail 3's 8" survived.
    inv = [_row("details 3 and 4/S4.00", '8"', ["ev3"]), _row("detail 5/S4.00", "1'-4\"", ["ev5"])]
    assert rr.value_conflicts([inv[0]]) == []  # merged, the disagreement inside it is invisible
    pool = [_ev("ev3", 73), _ev("ev4", 73), _ev("ev5", 73), _ev("ev7", 12), _ev("ev8", 73, kind="text")]
    asked = {}

    def ask(user, shown):
        asked["user"], asked["shown"] = user, [e.id for e in shown]
        return [_row("detail 3/S4.00", '8"', ["ev3"]), _row("detail 4/S4.00", "1'-4\"", ["ev4"])]

    out, note = rr.complete_inventory(inv, pool, ask)
    assert "details 3 and 4/S4.00" in asked["user"] and "ON ITS OWN" in asked["user"]
    assert asked["shown"] == ["ev3", "ev4", "ev5"]  # what it cited + that page's pictures, not other pages or text
    assert sorted(r["source"] for r in out) == ["detail 3/S4.00", "detail 4/S4.00", "detail 5/S4.00"]
    [c] = rr.value_conflicts(out)
    assert 'detail 3/S4.00 gives 8"' in c["issue"] and "gives 1'-4\"" in c["issue"]  # 4 or 5: both print 1'-4"
    assert "read again" in note and "Still merged" not in note


def test_an_unsplit_or_failed_answer_keeps_the_merged_row():
    inv = [_row("details 3 and 4/S4.00", '8"', ["ev3"])]
    pool = [_ev("ev3", 73)]
    same, note = rr.complete_inventory(inv, pool, lambda u, s: [_row("details 3 and 4/S4.00", '8"', ["ev3"])])
    assert same == inv and "Still merged" in note

    def fails(u, s):
        raise rr.StageFailed("discovery", "no JSON")
    same, note = rr.complete_inventory(inv, pool, fails)
    assert same == inv and "not read again" in note
    untouched = [_row("detail 3/S4.00", '8"', ["ev3"])]
    assert rr.complete_inventory(untouched, pool, fails) == (untouched, None)  # nothing merged: no call


def _lengthless(source, ev, entity="Elevator pit slab"):
    return {"checkId": "CE-05-01-Q01", "entity": entity, "location": "BH/B7", "source": source,
            "fields": [{"name": "depth", "value": None, "state": "unknown", "evidenceIds": [ev], "derived": False},
                       {"name": "waterproofing", "value": "WATERPROOFING MEMBRANE", "state": "supported",
                        "evidenceIds": [ev], "derived": False}]}


def test_lengthless_is_a_detail_entry_with_no_printed_length():
    assert rr.lengthless(_lengthless("detail 4/S4.00 ELEVATOR PIT DETAIL", "ev4"))
    assert not rr.lengthless(_row("detail 4/S4.00", "1'-4\"", ["ev4"]))
    # not a drawn detail: a schedule row or a note says nothing about missing thicknesses
    assert not rr.lengthless(_lengthless("pier schedule on S2.30-A", "ev4"))


def test_details_read_without_their_lengths_are_read_again_leland_rfi_007():
    # What the second paid run returned: details 4 and 5 with depth and waterproofing only, detail 3 not at all.
    inv = [_lengthless("detail 4/S4.00 ELEVATOR PIT DETAIL", "ev4"), _lengthless("detail 5/S4.00 ELEVATOR PIT DETAIL", "ev5")]
    assert rr.value_conflicts(inv) == []
    pool = [_ev("ev3", 73), _ev("ev4", 73), _ev("ev5", 73), _ev("ev9", 12)]
    asked = {}

    def ask(user, shown):
        asked["user"], asked["shown"] = user, [e.id for e in shown]
        return [_row("detail 3/S4.00", '8"', ["ev3"]), _row("detail 4/S4.00 ELEVATOR PIT DETAIL", "1'-4\"", ["ev4"])]

    out, note = rr.complete_inventory(inv, pool, ask)
    assert "record no printed length" in asked["user"] and "left out" in asked["user"]
    assert asked["shown"] == ["ev3", "ev4", "ev5"]
    sources = sorted(r["source"] for r in out)
    assert sources == ["detail 3/S4.00", "detail 4/S4.00 ELEVATOR PIT DETAIL", "detail 5/S4.00 ELEVATOR PIT DETAIL"]
    assert "1 entr(ies) added, 1 re-read" in note
    [c] = rr.value_conflicts(out)
    assert 'detail 3/S4.00 gives 8"' in c["issue"] and "gives 1'-4\"" in c["issue"]


def test_an_entry_with_lengths_is_never_replaced_by_a_re_read():
    inv = [_row("detail 4/S4.00", "1'-4\"", ["ev4"]), _lengthless("detail 5/S4.00", "ev5")]
    out, _ = rr.complete_inventory(inv, [_ev("ev4", 73), _ev("ev5", 73)],
                                   lambda u, s: [_row("detail 4/S4.00", '9"', ["ev4"])])
    assert [f["value"] for r in out for f in r["fields"] if r["source"] == "detail 4/S4.00"] == ["1'-4\""]


def test_element_title_words_match_the_shared_fixture_the_planner_reads():
    import json
    from pathlib import Path
    fixture = json.loads((Path(__file__).resolve().parents[2] / "packages/shared/fixtures/element-title-words.json").read_text())
    for case in fixture["cases"]:
        assert rr.element_title_words(case["name"]) == case["words"], case["name"]


def test_an_unescaped_inch_mark_does_not_cost_a_whole_retry():
    # The third paid run's discovery reply, valid but for one inch mark inside a string.
    raw = '{"observations":[{"statement":"Level B2 is -23\'-0" on the S2.30-B datum list, 5" THICK SLAB"}],' \
          '"inventory":[{"fields":[{"value":"1\'-4"","state":"supported"},{"value":"8"","state":"supported"}]}]}'
    d = rr.parse_json_object(raw)
    assert d["observations"][0]["statement"] == "Level B2 is -23'-0\" on the S2.30-B datum list, 5\" THICK SLAB"
    assert [f["value"] for f in d["inventory"][0]["fields"]] == ["1'-4\"", '8"']
    # valid JSON is read as is, and a reply with no object is still None
    assert rr.parse_json_object('{"value": "12", "n": 3}') == {"value": "12", "n": 3}
    assert rr.parse_json_object("no json here") is None
