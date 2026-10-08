"""agreement: when the code and the AI report one problem at one place.

The rules that matter are the refusals: a code finding of another KIND never
confirms an AI claim, a finding on only one of the two sheets is not the same
problem, and a box covering a quarter of its window agrees with everything and
so with nothing."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import agreement as ag  # noqa: E402
import geometry_checks as gc  # noqa: E402

WINDOWS = {"a": {"documentId": "d", "pageNumber": 1, "rect": [0, 0, 600, 600]},
           "b": {"documentId": "d", "pageNumber": 2, "rect": [0, 0, 600, 600]}}


def _ev(page, x, y, w=20, h=20):
    return {"documentId": "d", "pageNumber": page, "bbox": {"x": x, "y": y, "width": w, "height": h}}


AI = [_ev(1, 100, 100), _ev(2, 100, 100)]
WALL = {"checkId": "C01", "element": "corridor wall", "whatA": "wall on the grid line", "whatB": "wall 6 in north"}


def _code(check="column_mismatch", pages=(1, 2), x=105):
    return {"id": "c1", "fingerprint": "column_mismatch:x", "checkType": check, "status": "pending",
            "subject": "Column missing", "evidence": [_ev(p, x, 100) for p in pages]}


def test_an_ai_only_finding_is_never_high():
    assert ag.cap_ai_only("high") == "medium"
    assert ag.cap_ai_only("medium") == "medium" and ag.cap_ai_only("low") == "low"


def test_the_ai_confirms_a_code_finding_of_the_same_kind_at_the_same_place():
    assert ag.code_finding_agreement("C01", AI, [_code()], WINDOWS, 9.0)["id"] == "c1"
    # One foot of drawing apart is still the same place.
    assert ag.code_finding_agreement("C01", AI, [_code(x=125)], WINDOWS, 9.0) is not None


def test_another_kind_another_place_or_one_sheet_is_not_agreement():
    assert ag.code_finding_agreement("G01", AI, [_code()], WINDOWS, 9.0) is None  # a grid claim, a column finding
    assert ag.code_finding_agreement("C01", AI, [_code(x=300)], WINDOWS, 9.0) is None  # 20 ft away
    assert ag.code_finding_agreement("C01", AI, [_code(pages=(1,))], WINDOWS, 9.0) is None  # only one sheet
    assert ag.code_finding_agreement("C01", AI, [_code(check="open_item_note")], WINDOWS, 9.0) is None


def test_a_box_covering_the_window_agrees_with_nothing():
    huge = [_ev(1, 0, 0, 400, 400), _ev(2, 0, 0, 400, 400)]
    assert ag.code_finding_agreement("C01", huge, [_code()], WINDOWS, 9.0) is None
    assert ag.measured_agreement(WALL, [0, 0, 400, 400], [0, 0, 600, 600],
                                 [{"rect": [100, 100, 300, 104], "note": "x", "ptPerFt": 9.0}]) is None


def test_a_wall_claim_where_the_code_measured_a_wall_drawn_apart_is_agreement():
    at = [{"rect": [50, 200, 350, 204.5], "note": "a horizontal wall line is drawn 0'-6\" apart", "ptPerFt": 9.0}]
    assert "drawn 0'-6\" apart" in ag.measured_agreement(WALL, [150, 190, 190, 215], [0, 0, 600, 600], at)
    assert ag.measured_agreement(WALL, [150, 400, 190, 430], [0, 0, 600, 600], at) is None
    assert ag.measured_agreement(WALL, [150, 190, 190, 215], [0, 0, 600, 600], None) is None
    # A claim about something else at the same place is a different problem.
    column = {"checkId": "C01", "element": "column C-6", "whatA": "a column", "whatB": "nothing"}
    assert ag.measured_agreement(column, [150, 190, 190, 215], [0, 0, 600, 600], at) is None
    assert ag.measured_agreement({"checkId": "FL01", "element": "slab", "whatA": "", "whatB": ""},
                                 [150, 190, 190, 215], [0, 0, 600, 600], at) is not None


def test_a_near_miss_rect_spans_the_wall_and_its_twin_on_sheet_b():
    horizontal = {"horizontal": True, "posA": 100.0, "posOnB": 120.0, "posB": 124.5, "lo": 10.0, "hi": 300.0, "offset": 4.5}
    assert gc.near_miss_rect(horizontal) == [10.0, 120.0, 300.0, 124.5]
    vertical = {"horizontal": False, "posA": 50.0, "posOnB": 60.0, "posB": 55.5, "lo": 0.0, "hi": 90.0, "offset": -4.5}
    assert gc.near_miss_rect(vertical) == [55.5, 0.0, 60.0, 90.0]
