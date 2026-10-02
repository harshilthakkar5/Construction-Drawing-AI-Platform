"""Column OCCURRENCES measured against the grid (`column_locate`) and the C03
gate built on them (`rfi_review.occurrence_check`), plus where the package
clouds them.

The synthetic sheet below is LABELLED SYNTHETIC: its off-grid column is a
fixture made to be off grid, not a claim about any real drawing. The client
sheets are used only where the answer was checked by eye on the drawing
(C-12 at D/4.7 and C-17 at B1.6/4.6 stand on their crossings; C-8 at C.1/5
stands below grid line 5), and those tests skip without the client PDF.

These tests prove what the code does with the geometry it is given. They say
nothing about how often a real review model raises a correct C03 finding.
"""

import datetime
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import fitz  # noqa: E402
import pytest  # noqa: E402

import column_locate as cl  # noqa: E402
import rfi_package as rp  # noqa: E402
import rfi_review  # noqa: E402
from rfi_review import Evidence, OccurrencePage  # noqa: E402

CLIENT_PDF = Path(os.environ.get("RFI_REVIEW_TEST_PDF") or "/nonexistent")
PTFT = 9.0  # 1/8" = 1'-0"
IN = PTFT / 12  # one drawn inch in points

# SYNTHETIC grid, in DISPLAY space: what a person sees whatever the /Rotate.
COLUMNS = {"A": 800.0, "B": 1100.0, "C": 1400.0}
ROWS = {"1": 500.0, "2": 800.0}


def _sheet(rotation: int = 0) -> tuple[fitz.Document, list[tuple[str, str]]]:
    """A synthetic sheet with five column occurrences, each built in display
    space and drawn through the derotation matrix so it lands where the test
    says at any rotation. Returns the doc and (mark, what it is) pairs."""
    doc = fitz.open()
    page = doc.new_page(width=2592, height=1728)
    page.set_rotation(rotation)
    to_unrot = page.derotation_matrix
    page.insert_text(fitz.Point(2000, 1600) * to_unrot, 'PLAN  1/8" = 1\'-0"', fontsize=10)

    def column(cx: float, cy: float, mark: str, *, body: bool = True, w_in: float = 14, h_in: float = 30):
        shown = fitz.Rect(cx - w_in * IN / 2, cy - h_in * IN / 2, cx + w_in * IN / 2, cy + h_in * IN / 2)
        if body:
            page.draw_rect(fitz.Rect(shown * to_unrot).normalize(), color=(0, 0, 0), fill=(0.75, 0.75, 0.75), width=1.2)
        # Labels are written horizontally in the page's own text frame, as
        # CAD exports them, beside the column.
        spot = fitz.Rect(shown * to_unrot).normalize()
        page.insert_text(fitz.Point(spot.x1 + 6, spot.y1 + 4), mark, fontsize=9)
        page.insert_text(fitz.Point(spot.x1 + 6, spot.y1 + 14), f"({w_in:g} x {h_in:g})", fontsize=6)

    column(COLUMNS["A"], ROWS["1"], "C-1")  # centred both ways
    column(COLUMNS["C"], ROWS["2"] + 30 * IN, "C-1")  # same mark, 30in below row 2: off grid
    column(COLUMNS["B"], ROWS["1"] + 20 * IN, "C-2")  # centred on B only
    column(COLUMNS["B"] + 4 * IN, ROWS["2"], "C-3")  # 4in off B: inside neither band
    column(COLUMNS["A"], ROWS["2"], "C-4", body=False)  # a label with no body
    return doc, [("C-1", "centred"), ("C-1", "off"), ("C-2", "one axis"), ("C-3", "uncertain"), ("C-4", "no body")]


def _locate(rotation: int = 0):
    doc, _ = _sheet(rotation)
    occ, note = cl.locate(doc[0], grid_lines=(COLUMNS, ROWS), pt_per_ft=PTFT)
    return doc, occ, note


def _by(occ, mark, crossing=None):
    found = [o for o in occ if o.mark == mark and (crossing is None or o.crossing == crossing)]
    assert found, (mark, crossing, [(o.mark, o.crossing) for o in occ])
    return found


# --- pure rules ------------------------------------------------------------------------------


def test_tolerances_come_from_the_printed_scale_with_a_floor():
    centred, off = cl.tolerances(9.0)  # 1/8": 3in is 2.25pt, 6in is 4.5pt
    assert (centred, off) == (2.25, 4.5)
    centred, off = cl.tolerances(1.5)  # 1/64": the floor wins, not 0.4pt
    assert centred == cl.REGISTRATION_PT and off == 2 * cl.REGISTRATION_PT


def test_centred_on_one_axis_settles_only_that_axis():
    lines = {"B": 100.0}
    assert cl.classify_axis("x", 100.5, 5, lines, PTFT).state == cl.CENTRED
    assert cl.classify_axis("x", 115.0, 5, lines, PTFT).state == cl.OFFSET
    assert cl.classify_axis("x", 103.5, 30, lines, PTFT).state == cl.UNCERTAIN
    assert cl.classify_axis("x", 105.0, 5, lines, PTFT).state == cl.FACE
    assert cl.classify_axis("x", 200.0, 5, lines, PTFT).state == cl.NO_GRID


def test_a_line_with_two_names_keeps_both():
    axis = cl.classify_axis("y", 300.2, 5, {"2.3": 300.0, "2.4": 300.0, "3": 390.0}, PTFT)
    assert axis.state == cl.CENTRED and {axis.line, *axis.also_named} == {"2.3", "2.4"}


def test_a_label_between_two_bodies_belongs_to_neither():
    label = fitz.Rect(100, 100, 120, 110)
    a, b = fitz.Rect(80, 120, 90, 140), fitz.Rect(125, 121, 135, 141)
    body, why = cl.associate(label, [a, b], None, PTFT)
    assert body is None and "between two" in why


def test_a_body_that_does_not_match_the_printed_size_is_not_used():
    label = fitz.Rect(100, 100, 120, 110)
    body, why = cl.associate(label, [fitz.Rect(80, 112, 120, 120)], (14, 30), PTFT)
    assert body is None and "size" in why


def test_printed_sizes_are_read():
    assert cl.printed_size("(14 x 48)") == (14, 48)
    assert cl.printed_size('(22" DIA)') == (22, 22)
    assert cl.printed_size("SR-7") is None


# --- the synthetic sheet ------------------------------------------------------------------------


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_each_occurrence_is_measured_on_its_own_at_every_rotation(rotation):
    doc, occ, note = _locate(rotation)
    assert note is None
    # Case 1 (synthetic): centred on two grid lines -> located, no offset needed.
    centred = _by(occ, "C-1", "A/1")[0]
    assert centred.status == cl.LOCATED
    # Case 5: the SAME mark elsewhere is judged separately.
    off = _by(occ, "C-1", "C/2")[0]
    assert off.status == cl.OFF_GRID and off.unresolved() == ["2"]
    # Case 3: centred on B says nothing about the other direction.
    one = _by(occ, "C-2")[0]
    assert one.x.state == cl.CENTRED and one.y.state == cl.OFFSET and one.status == cl.OFF_GRID
    # Inside neither band: uncertain, never forced to a side.
    assert _by(occ, "C-3")[0].status == cl.UNKNOWN
    # Case 6: no body beside the label -> unknown, with the reason.
    nobody = _by(occ, "C-4")[0]
    assert nobody.status == cl.UNKNOWN and "no column body" in nobody.reason
    # The stored box is in UNROTATED space and holds the label and the body:
    # clip the page's own words out of it rather than trusting the matrix.
    words = {w[4] for w in doc[0].get_text("words", clip=off.unrotated)}
    assert "C-1" in words
    assert off.unrotated.width * off.unrotated.height < 0.01 * 2592 * 1728


def test_an_unknown_scale_measures_nothing():
    page = fitz.open()
    p = page.new_page(width=600, height=400)
    p.insert_text((100, 100), "C-9", fontsize=9)
    occ, note = cl.locate(p, grid_lines=({"A": 100.0}, {"1": 100.0}))
    assert note == "no drawing scale printed" and all(o.status == cl.UNKNOWN for o in occ)


def test_the_scale_is_read_off_the_sheet():
    doc, _ = _sheet()
    occ, note = cl.locate(doc[0], grid_lines=(COLUMNS, ROWS))
    assert note is None and _by(occ, "C-1", "A/1")[0].status == cl.LOCATED


# --- the C03 gate ------------------------------------------------------------------------------


def _measured(occ):
    page = {"documentId": "d", "pageNumber": 1, "combinedPageNumber": 7, "sheetNumber": "S9.999"}
    return {("d", 1): OccurrencePage(page, occ)}


def _ev(eid, kind="crop", text="", page=1):
    return Evidence(id=eid, kind=kind, document_id="d", page_number=page, combined_page_number=page,
                    sheet_number="S9.999" if page == 1 else "S9.500", side=None,
                    bbox={"x": 0, "y": 0, "width": 2592, "height": 1728} if kind == "page" else {"x": 10, "y": 10, "width": 50, "height": 20},
                    text=text)


def _claim(element, location="", evidence=("ev1",)):
    return {"checkId": "C03", "kind": "missing", "element": element, "location": location,
            "issue": "no offset dimension from grid", "evidenceIds": list(evidence), "searched": [], "why": ""}


def test_a_claim_about_centred_occurrences_is_rejected_by_measurement():
    _, occ, _ = _locate()
    v = rfi_review.occurrence_check(_claim("column C-1", "A/1"), None, _measured(occ), {"ev1": _ev("ev1")})
    assert v.rejected and "centred on a grid line in both directions" in v.reason


def test_a_genuine_off_grid_occurrence_survives_and_is_named_exactly():
    """Case 2 — SYNTHETIC fixture: the off-grid C-1 at C/2 is built off grid."""
    _, occ, _ = _locate()
    v = rfi_review.occurrence_check(_claim("column C-1", "C/2"), None, _measured(occ), {"ev1": _ev("ev1")})
    assert not v.rejected and [o.crossing for _, o in v.supported] == ["C/2"]
    subject, question = rfi_review.occurrence_wording(v)
    assert "C-1 near C/2" in question and "grid line 2" in question and "S9.999" in subject
    # No measured distance is stated as if the drawing printed it.
    assert '"' not in question and "'" not in question.replace("'s", "")


def test_the_same_mark_gets_two_outcomes_and_only_the_off_one_is_marked():
    """Case 5: a claim naming C-1 without a crossing judges each occurrence."""
    _, occ, _ = _locate()
    v = rfi_review.occurrence_check(_claim("column C-1"), None, _measured(occ), {"ev1": _ev("ev1")})
    assert [o.crossing for _, o in v.supported] == ["C/2"] and [o.crossing for _, o in v.resolved] == ["A/1"]
    items = rfi_review.occurrence_evidence(v)
    assert [(i["role"], i["occurrence"]["crossing"]) for i in items] == [("finding", "C/2")]
    assert "Removed (centred on grid lines both ways): C-1 near A/1" in rfi_review.occurrence_reasoning(v)


def test_a_general_centring_note_does_not_overrule_a_measured_offset():
    """Case 4: "centred on grid lines U.N.O." in the cited evidence changes
    nothing — neither rejecting the off-grid one nor confirming the rest."""
    _, occ, _ = _locate()
    evidence = {"ev1": _ev("ev1"), "ev2": _ev("ev2", "text", "COLUMNS SHOWN ON PLAN ARE CENTERED ON GRID LINES U.N.O.", page=2)}
    v = rfi_review.occurrence_check(_claim("column C-2", evidence=("ev1", "ev2")), None, _measured(occ), evidence)
    assert [o.mark for _, o in v.supported] == ["C-2"] and not v.rejected


def test_unreadable_geometry_is_uncertainty_not_an_omission():
    """Case 6: no body, or a body in the uncertain band, never becomes a
    finding and never a clean pass: it is a gap for a person."""
    _, occ, _ = _locate()
    v = rfi_review.occurrence_check(_claim("columns C-3 and C-4"), None, _measured(occ), {"ev1": _ev("ev1")})
    assert v.unresolved_only and not v.supported and not v.rejected
    gaps = rfi_review.occurrence_gaps(v.unknown)
    assert len(gaps) == 2 and all(g["reason"].startswith("needs manual verification") for g in gaps)
    # A claim naming a mark the sheet does not print cannot be located at all.
    v = rfi_review.occurrence_check(_claim("column C-77"), None, _measured(occ), {"ev1": _ev("ev1")})
    assert v.unresolved_only and "names no column mark" in v.reason


def test_the_gate_leaves_other_checks_and_unmeasured_pages_alone():
    _, occ, _ = _locate()
    conflict = dict(_claim("column C-1", "A/1"), kind="conflict")
    assert not rfi_review.occurrence_check(conflict, None, _measured(occ), {"ev1": _ev("ev1")}).applies
    other = dict(_claim("column C-1", "A/1"), checkId="C01")
    assert not rfi_review.occurrence_check(other, None, _measured(occ), {"ev1": _ev("ev1")}).applies
    elsewhere = {"ev1": _ev("ev1", page=2)}
    assert not rfi_review.occurrence_check(_claim("column C-1", "A/1"), None, _measured(occ), elsewhere).applies


def test_an_unsettled_question_is_not_reported_as_no_issue():
    """Case 8: occurrences left for a person keep C03 out of
    complete_no_issue, whatever else the run saw."""
    _, occ, _ = _locate()
    gaps = rfi_review.uncovered_occurrences(_measured(occ), covered=set())
    observations = [{"checkId": "C03", "statement": "columns seen", "evidenceIds": ["ev1"]}]
    results = rfi_review.check_results(["C03"], ["C03"], {}, observations, gaps, [], {})
    assert results["C03"]["outcome"] == "insufficient_evidence"
    assert results["C03"]["gaps"] and len(gaps) == 4  # every occurrence but the centred C-1


# --- the package -------------------------------------------------------------------------------


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_the_cloud_is_on_the_occurrence_and_never_the_whole_sheet(rotation):
    """Case 7: one tight cloud per verified occurrence at every rotation; a
    whole-sheet picture is not clouded, and an unverified occurrence is
    outlined as context, not clouded."""
    doc, occ, _ = _locate(rotation)
    measured = _measured(occ)
    evidence = {"ev1": _ev("ev1", "page")}
    v = rfi_review.occurrence_check(_claim("columns C-1, C-3", evidence=("ev1",)), None, measured, evidence)
    items = rfi_review.occurrence_evidence(v) + [dict(evidence["ev1"].as_candidate_evidence(None), role="context")]
    subject, question = rfi_review.occurrence_wording(v)
    item = rp.Item("candidate", "x", None, subject, question, "structural", datetime.datetime(2026, 10, 2),
                   "RFI review", None, "Synthetic", draft=True, marks=rp._marks_from_evidence(items),
                   reasoning=rfi_review.occurrence_reasoning(v))
    out, notes = rp.render([item], lambda d: doc)
    sheet = out[1]
    clouds = [a for a in sheet.annots() if a.type[1] == "Polygon"]
    assert len(clouds) == 1
    area = clouds[0].rect.width * clouds[0].rect.height
    assert area < rp.PRECISE_SHARE * 2592 * 1728
    assert "C-1" in {w[4] for w in sheet.get_text("words", clip=clouds[0].rect)}
    outlines = [a for a in sheet.annots() if a.type[1] == "Square"]
    assert outlines and all("NOT VERIFIED" in a.info["content"] for a in outlines)
    assert "C-3" in {w[4] for w in sheet.get_text("words", clip=outlines[0].rect)}


def test_a_whole_sheet_finding_is_flagged_not_clouded():
    doc, _ = _sheet()
    whole = {"x": 0, "y": 0, "width": 2592, "height": 1728}
    item = rp.Item("candidate", "x", None, "S", "Q?", None, None, None, None, "P", draft=True,
                   marks=[rp.Mark("d", 1, 1, "S9.999", whole, kind="page")])
    out, notes = rp.render([item], lambda d: doc)
    assert not [a for a in out[1].annots() if a.type[1] == "Polygon"]
    assert notes and "NOT pinpointed" in notes[0] and "NOT pinpointed" in out[0].get_text()


def test_a_broad_finding_is_outlined_and_a_far_pair_is_never_one_cloud():
    page = fitz.Rect(0, 0, 2592, 1728)
    far = rp.merge_rects([fitz.Rect(0, 0, 900, 900), fitz.Rect(905, 0, 1800, 900)], gap=10,
                         max_area=rp.PRECISE_SHARE * page.width * page.height)
    assert len(far) == 2
    doc, _ = _sheet()
    broad = {"x": 100, "y": 100, "width": 900, "height": 700}
    item = rp.Item("candidate", "x", None, "S", "Q?", None, None, None, None, "P", draft=True,
                   marks=[rp.Mark("d", 1, 1, "S9.999", broad)])
    out, notes = rp.render([item], lambda d: doc)
    kinds = [a.type[1] for a in out[1].annots()]
    assert "Polygon" not in kinds and "Square" in kinds and "NOT pinpointed" in notes[0]


def test_a_context_only_sheet_says_so_instead_of_asking_the_question():
    doc, _ = _sheet()
    note = {"x": 100, "y": 100, "width": 200, "height": 60}
    item = rp.Item("candidate", "x", None, "S", "Where is C-1?", None, None, None, None, "P", draft=True,
                   marks=[rp.Mark("d", 1, 1, "S5.101", note, role="context")])
    out, _ = rp.render([item], lambda d: doc)
    callout = next(a for a in out[1].annots() if a.type[1] == "FreeText" and "DRAFT" in a.info["content"])
    assert "CONTEXT ONLY" in callout.info["content"] and "Where is C-1?" not in callout.info["content"]


def test_the_cover_says_the_revision_is_unknown_and_never_cuts_the_audit_short():
    """Case 8 in the export, and the "Checked: ..., S2.107," truncation."""
    doc, _ = _sheet()
    sheets = ", ".join(f"S{n}.{n:03d}" for n in range(1, 120))
    item = rp.Item("candidate", "x", None, "S", "Q?", None, None, None, None, "P", draft=True,
                   marks=[rp.Mark("d", 1, 1, "S9.999", {"x": 100, "y": 100, "width": 40, "height": 20})],
                   reasoning=f"Not verified - a person must check: C-3. Sheets read in this review (119): {sheets}.")
    out, _ = rp.render([item], lambda d: doc)
    text = "".join(out[i].get_text() for i in range(out.page_count) if "DRAFT RFI" in out[i].get_text())
    assert "Unknown - not read from the title block" in text
    flat = " ".join(text.split())
    assert "S119.119." in flat and "Not verified - a person must check: C-3." in flat


# --- the client's sheet ----------------------------------------------------------------------


@pytest.mark.skipif(not CLIENT_PDF.exists(), reason="set RFI_REVIEW_TEST_PDF to the client's S2.105/A3.01 set")
def test_the_client_sheet_c12_and_c17_are_centred_and_c8_is_not():
    """Case 1 on the real drawing, checked by eye on the sheet: C-12 stands on
    D/4.7 and C-17 on B1.6/4.6 (the forming-plan RFI named both as missing
    offsets); C-8 stands below grid line 5. S2.105 is the level under S2.106
    and draws these the same way."""
    import grid

    page = grid.without_markup(fitz.open(CLIENT_PDF)[0])
    occ, note = cl.locate(page, {"C12", "C17", "C8"})
    status = {(o.mark, o.crossing): o.status for o in occ}
    assert status[("C-12", "D/4.7")] == cl.LOCATED
    assert status[("C-17", "B1.6/4.6")] == cl.LOCATED
    assert status[("C-8", "C.1/5")] == cl.OFF_GRID
    measured = {("d", 1): OccurrencePage({"documentId": "d", "pageNumber": 1, "sheetNumber": "S2.105"}, occ)}
    grouped = _claim("columns C-8, C-12, C-17", "Level 5 forming plan")
    v = rfi_review.occurrence_check(grouped, None, measured, {"ev1": _ev("ev1")})
    assert [o.mark for _, o in v.supported] == ["C-8"]
    assert sorted(o.mark for _, o in v.resolved) == ["C-12", "C-17"]
