"""Two columns on one pier cap with no detail called out (Leland RFI 004).
Synthetic PDFs: no model, no database."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import fitz  # noqa: E402
import pytest  # noqa: E402

import pier_caps as pc  # noqa: E402

PPF = 6.75  # 3/32" = 1'-0", Leland's foundation plans
GREY = (0.754, 0.754, 0.754)


def _fill(x0, y0, w, h, grey=GREY):
    return {"type": "f", "fill": grey, "rect": fitz.Rect(x0, y0, x0 + w, y0 + h)}


def test_two_touching_columns_are_one_pair_and_lone_or_unlike_ones_are_not():
    fills = pc.column_fills([
        _fill(100, 100, 15, 13), _fill(100, 113.5, 15, 13),   # stacked, 0.5pt apart: a pair
        _fill(300, 100, 15, 13),                             # alone
        _fill(500, 100, 15, 13), _fill(500, 120, 15, 13),     # 7pt apart: two caps
        _fill(700, 100, 15, 13), _fill(715.5, 100, 6, 6),     # touching but a third the size: not a column
        _fill(900, 100, 3, 13),                              # a wall stroke, too thin
        _fill(1100, 100, 60, 13),                            # a wall, too long
        _fill(1300, 100, 15, 13, grey=(0, 0, 0)), _fill(1300, 113.5, 15, 13, grey=(0, 0, 0)),  # black: not a column fill
    ], PPF)
    pairs = pc.twins(fills)
    assert [(round(r.x0), round(r.y0)) for r in pairs] == [(100, 100)]


def test_a_pair_drawn_twice_is_one_pair():
    fills = [fitz.Rect(100, 100, 115, 113), fitz.Rect(100, 113.5, 115, 126.5),
             fitz.Rect(100.5, 100.2, 115, 113), fitz.Rect(100.5, 113.6, 115, 126.5)]
    assert len(pc.twins(fills)) == 1


def test_a_pair_with_a_detail_symbol_near_it_is_left_alone():
    pair = fitz.Rect(100, 100, 115, 126)
    assert pc.uncalled([pair], [(160, 110)], PPF) == []          # 45pt = 6.7 ft away
    assert pc.uncalled([pair], [(200, 110)], PPF) == [pair]      # 85pt = 12.6 ft away


def _plan(title="BASEMENT 2 - FOUNDATION PLAN", symbol_at=None, rotation=0):
    doc = fitz.open()
    page = doc.new_page(width=1200, height=800)
    page.set_rotation(rotation)
    for y in (300, 313.5):
        page.draw_rect(fitz.Rect(400, y, 415, y + 13), color=None, fill=GREY)
    page.insert_text(fitz.Point(80, 700) * page.derotation_matrix, title, fontsize=24, rotate=rotation)
    page.insert_text(fitz.Point(80, 730) * page.derotation_matrix, "SCALE: 3/32\" = 1'-0\"", fontsize=8, rotate=rotation)
    page.insert_text(fitz.Point(80, 760) * page.derotation_matrix, "GENERAL NOTES APPLY TO ALL WORK", fontsize=6, rotate=rotation)
    if symbol_at:  # beside the pair in STORED space (where the drawings are), read upright on screen
        x, y = fitz.Point(symbol_at) * page.rotation_matrix
        page.insert_text(fitz.Point(x + 9, y - 6) * page.derotation_matrix, "9", fontsize=8, rotate=rotation)
        page.insert_text(fitz.Point(x, y + 8) * page.derotation_matrix, "S4.00", fontsize=8, rotate=rotation)
    return doc, page


@pytest.mark.parametrize("rotation", [0, 90])
def test_a_foundation_plan_with_an_uncalled_pair_is_reported(rotation):
    doc, page = _plan(rotation=rotation)
    assert pc.is_foundation(None, page)
    found, note = pc.on_page(page)
    assert note is None and len(found) == 1
    # A detail symbol beside it settles where its cap is drawn.
    doc, page = _plan(symbol_at=(440, 315), rotation=rotation)
    assert pc.on_page(page)[0] == []


def test_a_framing_plan_is_not_a_foundation_plan():
    doc, page = _plan(title="BASEMENT 1 - FRAMING PLAN")
    assert not pc.is_foundation("SHEET TITLE S2.31-A BASEMENT 1 - FRAMING PLAN", page)
    assert pc.is_foundation("SHEET TITLE S2.30-B BASEMENT 2 - FOUNDATION PLAN - AREA B", page)


class _Row:
    def __init__(self, region):
        self.id, self.document_id, self.page_number, self.combined_page_number = "p", "d", 25, 25
        self.sheet_number, self.region_text, self.sheet_kind = "S2.30-B", region, "plan"


def test_findings_name_the_cap_ask_which_detail_and_cloud_each_pair():
    doc, page = _plan()
    found, _ = pc.findings([_Row("SHEET TITLE S2.30-B BASEMENT 2 - FOUNDATION PLAN")], [], lambda d, n: page)
    [f] = found
    assert f.check_type == "twin_column_cap" and f.confidence == "medium"
    assert "two columns" in f.question and "typical pier cap detail" in f.question and "Which detail" in f.question
    [ev] = f.evidence
    assert ev["sheetNumber"] == "S2.30-B" and ev["role"] == "finding"
    box = fitz.Rect(ev["bbox"]["x"], ev["bbox"]["y"], ev["bbox"]["x"] + ev["bbox"]["width"], ev["bbox"]["y"] + ev["bbox"]["height"])
    assert box.contains(fitz.Rect(400, 300, 415, 326.5))
    # The same drawing gives the same fingerprint (a re-scan is idempotent).
    again, _ = pc.findings([_Row("FOUNDATION PLAN")], [], lambda d, n: page)
    assert again[0].fingerprint == f.fingerprint
