"""geometry_checks: the code's own comparison of the sheet pairs the scan lines up.

The cases that matter most are the ones that must NOT become findings or must
NOT be settled: a grid naming dispute dressed as a spacing difference (the
client's RFI 002 sheets did exactly that on the first real run), and a window
settled while a wall in it was drawn somewhere else."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import fitz  # noqa: E402
import pytest  # noqa: E402

import fullscan_plan as fp  # noqa: E402
import geometry_checks as gc  # noqa: E402
import wall_match  # noqa: E402
from rfi_checks import Finding, Page  # noqa: E402
from sheet_facts import PageFacts  # noqa: E402

PTFT = 9.0  # 1/8" = 1'-0"


# --- Grid spacing -------------------------------------------------------------------

A = {"A": 100.0, "B": 280.0, "C": 460.0, "D": 640.0, "E": 820.0}


def test_two_sheets_that_agree_report_nothing():
    b = {k: v + 50 for k, v in A.items()}
    assert gc.spacing_differences(A, b, 1.0, 50.0, PTFT) == []


def test_one_bay_drawn_longer_is_reported_and_only_that_bay():
    # C-D is 2 ft longer on B: every line after it moves 18pt, the shape of a
    # real dimension change (and one the line-up's majority rides through).
    b = {"A": 150.0, "B": 330.0, "C": 510.0, "D": 708.0, "E": 888.0}
    diffs = gc.spacing_differences(A, b, 1.0, 50.0, PTFT)
    assert [(d["from"], d["to"]) for d in diffs] == [("C", "D")]
    assert diffs[0]["diff"] == pytest.approx(18.0)


def test_a_line_that_lands_on_another_named_line_is_a_naming_dispute_not_a_distance():
    # RFI 002: A's G sits exactly where B draws F.7. Read by name, F-G and
    # G-H "swap" sizes — the first real run reported that as a spacing RFI.
    a = {"C": 0.0, "D": 200.0, "E": 400.0, "F": 670.0, "G": 859.6, "H": 1119.3}
    b = {"C": 0.0, "D": 200.0, "E": 400.0, "F": 670.0, "F.7": 859.6, "G": 929.4, "H": 1119.3}
    assert gc.spacing_differences(a, b, 1.0, 0.0, PTFT) is None
    # The same shape with no line under A's G is a line genuinely drawn in
    # another place, and is reported.
    del b["F.7"]
    assert [(d["from"], d["to"]) for d in gc.spacing_differences(a, b, 1.0, 0.0, PTFT)] == [("F", "G"), ("G", "H")]


def test_names_shifted_by_a_whole_bay_are_refused():
    b = {"B": 100.0, "C": 280.0, "D": 460.0, "E": 640.0}  # B's "B" is A's "A"
    assert gc.spacing_differences(A, b, 1.0, 0.0, PTFT) is None


def test_too_few_shared_lines_or_too_many_differences_are_refused():
    assert gc.spacing_differences({"A": 0.0, "B": 100.0}, {"A": 0.0, "B": 130.0}, 1.0, 0.0, PTFT) is None
    wobbly = {"A": 100.0, "B": 290.0, "C": 450.0, "D": 655.0, "E": 815.0}
    assert gc.spacing_differences(A, wobbly, 1.0, 0.0, PTFT) is None


def test_a_difference_under_three_inches_is_not_a_finding():
    b = dict(A, D=641.5, E=821.5)  # 1.5pt at 1/8" is 2 inches
    assert gc.spacing_differences(A, b, 1.0, 0.0, PTFT) == []


def _facts(pid, sheet, discipline, x, y, scales=(PTFT,)):
    return PageFacts(page_id=pid, document_id="d", page_number=int(pid[1:]), combined_page_number=int(pid[1:]),
                     sheet_number=sheet, discipline=discipline, kind="plan", level="LEVEL 2", scales=list(scales),
                     grid={"x": x, "y": y, "size": [1200, 900], "repeats": False})


def _page(pid, sheet):
    return Page(pid, "d", int(pid[1:]), int(pid[1:]), sheet)


def test_a_grid_lined_pair_with_one_longer_bay_becomes_one_medium_finding_on_both_sheets():
    y = {"1": 100.0, "2": 300.0, "3": 500.0}
    b_x = {"A": 150.0, "B": 330.0, "C": 510.0, "D": 708.0, "E": 888.0}
    pair = fp.Pair("same_level", _facts("p1", "A1.01", "architectural", A, y),
                   _facts("p2", "S1.01", "structural", b_x, {k: v + 20 for k, v in y.items()}), "Level 2")
    pair.transform = fp.Transform(1.0, 50.0, 20.0)
    doc = fitz.open()
    doc.new_page(width=1200, height=900)
    found, notes = gc.grid_spacing_findings([pair], {"p1": _page("p1", "A1.01"), "p2": _page("p2", "S1.01")},
                                            lambda d, n: doc[0])
    [finding] = found
    assert finding.check_type == gc.GRID_SPACING and finding.confidence == "medium"
    assert "C to D is 20'-0\" on A1.01 and 22'-0\" on S1.01" in finding.question
    assert {e["sheetNumber"] for e in finding.evidence} == {"A1.01", "S1.01"}
    assert all(e["bbox"] and e["bbox"]["width"] > 0 for e in finding.evidence)
    assert notes


def test_a_pair_lined_up_by_walls_or_printing_two_scales_is_not_measured():
    pair = fp.Pair("same_level", _facts("p1", "A1.01", "architectural", A, {"1": 1.0, "2": 2.0}),
                   _facts("p2", "E1.01", "electrical", A, {"1": 1.0, "2": 2.0}), "Level 2")
    pair.transform = fp.Transform(1.0, 0.0, 0.0)
    pair.wall_shift = object()
    assert gc.grid_spacing_findings([pair], {}, None) == ([], [])
    pair.wall_shift = None
    pair.a.scales = [9.0, 18.0]
    pair.b.scales = [9.0, 18.0]
    assert gc._common_scale(pair) is None


def test_columns_are_compared_only_where_a_column_must_agree():
    arch = _facts("p1", "A1.01", "architectural", A, {})
    struct = _facts("p2", "S1.01", "structural", A, {})
    elec = _facts("p3", "E1.01", "electrical", A, {})
    assert gc._column_pair(fp.Pair("same_level", arch, struct, ""))
    assert not gc._column_pair(fp.Pair("same_level", arch, elec, ""))
    assert gc._column_pair(fp.Pair("enlarged", elec, _facts("p4", "E2.01", "electrical", A, {}), ""))


# --- Triage -------------------------------------------------------------------------


def walls(h=(), v=()):
    return wall_match.Walls([wall_match.Segment(*s) for s in h], [wall_match.Segment(*s) for s in v])


ROOM = [(100.0 + 20 * i, 50.0, 400.0) for i in range(30)]  # thirty horizontal wall lines


def test_a_copied_background_is_settled():
    a = walls(h=ROOM)
    b = walls(h=[(p + 20, lo + 10, hi + 10) for p, lo, hi in ROOM])
    lines_a, lines_b, ma, mb, near = gc.compare_lines(a, b, [0, 0, 600, 800], [0, 0, 600, 800], 1.0, 10, 20, PTFT)
    view = gc.TileView(0, 0, None, lines_a, lines_b, ma, mb, near)
    assert (ma, mb, near) == (30, 30, [])
    assert "copies the other" in view.settled()


def test_a_wall_drawn_six_inches_off_is_never_settled_and_is_handed_to_the_ai():
    moved = list(ROOM)
    moved[5] = (moved[5][0] + 4.5, moved[5][1], moved[5][2])  # 6 in at 1/8"
    *_, near = gc.compare_lines(walls(h=ROOM), walls(h=moved), [0, 0, 600, 800], [0, 0, 600, 800], 1.0, 0, 0, PTFT)
    assert len(near) == 1 and near[0]["offset"] == pytest.approx(4.5)
    view = gc.TileView(0, 0, None, 30, 30, 29, 29, near)
    assert view.settled() is None


def test_a_pen_width_and_a_wall_s_other_face_are_not_a_moved_wall():
    # 1 inch at 1/8" is 0.75pt: the same line. And a second line 6 in away
    # whose own partner exists is the wall's other face.
    a = walls(h=[(100.0, 0, 300), (104.5, 0, 300)])
    b = walls(h=[(100.75, 0, 300), (104.5, 0, 300)])
    *_, ma, mb, near = gc.compare_lines(a, b, [0, 0, 600, 800], [0, 0, 600, 800], 1.0, 0, 0, PTFT)
    assert (ma, near) == (2, [])


def test_settling_needs_positive_evidence_not_silence():
    assert gc.TileView(0, 0, None, 10, 10, 10, 10, []).settled() is None  # too few lines to say
    assert gc.TileView(0, 0, None, 400, 400, 120, 120, []).settled() is None  # neither side a copy
    assert gc.TileView(0, 0, "B").settled() == "sheet B draws nothing in this area"


def _two_page_pdf(draw_b):
    doc = fitz.open()
    for draw in (lambda p: [p.draw_line((50, 100 + 20 * i), (400, 100 + 20 * i)) for i in range(30)], draw_b):
        page = doc.new_page(width=600, height=800)
        draw(page)
    return doc


def _pair():
    a = _facts("p1", "A1.01", "architectural", {}, {}, scales=(PTFT,))
    b = _facts("p2", "E1.01", "electrical", {}, {}, scales=(PTFT,))
    b.page_number = 2
    pair = fp.Pair("same_level", a, b, "Level 1")
    pair.transform = fp.Transform(1.0, 0.0, 0.0)
    pair.windows = [([0, 0, 600, 800], [0, 0, 600, 800])]
    return pair


def test_triage_settles_a_copy_and_says_why():
    doc = _two_page_pdf(lambda p: [p.draw_line((50, 100 + 20 * i), (400, 100 + 20 * i)) for i in range(30)])
    out = gc.triage_tiles([_pair()], lambda d, n: doc[n - 1])
    assert list(out.reasons) == [(0, 0)] and "copies the other" in out.reasons[(0, 0)]


def test_triage_sends_a_blank_sheet_away_and_a_known_finding_back_to_the_ai():
    doc = _two_page_pdf(lambda p: None)
    out = gc.triage_tiles([_pair()], lambda d, n: doc[n - 1])
    assert out.reasons == {(0, 0): "sheet B draws nothing in this area"}

    # B draws one wall 6 in off: not settled, and the AI is told both what
    # the code measured and what the text checks already reported there.
    doc = _two_page_pdf(lambda p: [p.draw_line((50, 100 + 20 * i + (4.5 if i == 5 else 0)),
                                               (400, 100 + 20 * i + (4.5 if i == 5 else 0))) for i in range(30)])
    known = Finding("open_item_note", "fp", "high", "TBD on A1.01", "q",
                    [{"documentId": "d", "pageNumber": 1, "bbox": {"x": 60, "y": 90, "width": 30, "height": 20}}])
    out = gc.triage_tiles([_pair()], lambda d, n: doc[n - 1], findings=[known])
    assert out.reasons == {}
    hint = out.hints[(0, 0)]
    assert hint["known"] == ["TBD on A1.01"]
    assert hint["measured"] == ['a horizontal wall line about 38% from the left and 26% from the top '
                                'is drawn 0\'-6" apart on the two sheets']
    # ...and WHERE on sheet B, so an AI finding there can be matched to it.
    [at] = hint["measuredAt"]
    assert at["rect"] == pytest.approx([50.0, 200.0, 400.0, 204.5]) and at["note"] == hint["measured"][0]
