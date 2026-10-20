"""A "By element" review: library questions asked in the client's words, and
the code checks those questions (and the catalogue questions) link to."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

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
    assert rr.element_title_words("Drilled piers / caissons (CIDH)") == [["DRILLED", "PIER"], ["CAISSON"]]
    for rotation in (0, 90):
        doc, page = _detail_sheet(rotation)
        regions = rr.detail_regions(page, [["ELEVATOR", "PIT"]], 3)
        inside = [" ".join(w[4] for w in page.get_text("words", clip=r)) for r in regions]
        assert len(regions) == 2, (rotation, inside)
        assert any("SLABA8IN" in t for t in inside) and any("SLABB16IN" in t for t in inside), (rotation, inside)
        assert not any("SUMPX" in t for t in inside), (rotation, inside)
        assert rr.detail_regions(page, [["ELEVATOR", "PIT"]], 1) == regions[:1]
        assert rr.detail_regions(page, [], 3) == [] and rr.detail_regions(page, [["PILE", "CAP"]], 3) == []
