"""Where a plan calls out a detail, and which details defer a value to the
plan — read off the words, no model. Synthetic pages."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import fitz  # noqa: E402
import pytest  # noqa: E402

import callouts  # noqa: E402
import review_aids  # noqa: E402


def _w(x0, y0, x1, y1, text):
    return (x0, y0, x1, y1, text, (x0, y0, x1, y1))


def test_an_inline_callout_is_read_like_a_stacked_one_leland_rfi_009():
    # S2.31-A: "10/" then "S4.10" on one line beside the section triangle.
    words = [_w(1297, 535, 1310, 545, "10/"), _w(1314, 535, 1337, 545, "S4.10"),
             _w(1411, 415, 1417, 425, "13"), _w(1405, 429, 1428, 439, "S4.10"),
             _w(1500, 535, 1510, 545, "7/"), _w(1600, 535, 1623, 545, "S4.10")]  # "7/" too far from its sheet
    got = sorted((c["detail"], c["form"]) for c in callouts.section_callouts(words, "S4.10"))
    assert got == [("10", "inline"), ("13", "stacked")]
    assert [c["detail"] for c in callouts.section_callouts([_w(0, 0, 40, 10, "10/S4.10")], "S4.10")] == ["10"]
    assert callouts.section_callouts(words, "S4.01") == []


def test_any_callouts_finds_every_sheet_a_page_points_at():
    words = [_w(10, 0, 16, 10, "9"), _w(2, 14, 25, 24, "S4.00"),
             _w(100, 14, 113, 24, "6/"), _w(116, 14, 139, 24, "S4.03"),
             _w(200, 14, 240, 24, "NOTES")]
    assert sorted((c["detail"], c["sheet"]) for c in callouts.any_callouts(words)) == [("6", "S4.03"), ("9", "S4.00")]


def _detail_sheet():
    doc = fitz.open()
    page = doc.new_page(width=1200, height=800)
    # Detail 10, titled only by its own reference, deferring T.O.C. to the plan.
    page.insert_text((300, 100), "T.O.C.", fontsize=8)
    page.insert_text((300, 110), "SEE PLAN", fontsize=8)
    page.insert_text((60, 300), "10", fontsize=14)
    page.insert_text((90, 296), "10/S4.10", fontsize=10)
    page.insert_text((90, 308), "SCALE: 1/4\" = 1'-0\"", fontsize=7)
    # Detail 11, beside it, deferring nothing.
    page.insert_text((800, 100), "T.O.C. = -4'-0\"", fontsize=8)
    page.insert_text((660, 300), "11", fontsize=14)
    page.insert_text((690, 296), "BASEMENT WALL DETAIL", fontsize=10)
    page.insert_text((690, 308), "SCALE: 1/4\" = 1'-0\"", fontsize=7)
    return doc, page


def test_a_reference_only_title_is_a_detail_title_and_its_deferral_is_found():
    doc, page = _detail_sheet()
    titles = {n: t for n, t, _ in review_aids.detail_titles(review_aids._display_lines(page))}
    assert titles == {"10": "10/S4.10", "11": "BASEMENT WALL DETAIL"}
    assert review_aids.deferred_details(page, None) == {"10"}
    # Only inside the cited box.
    assert review_aids.deferred_details(page, {"x": 250, "y": 80, "width": 200, "height": 50}) == {"10"}
    assert review_aids.deferred_details(page, {"x": 700, "y": 80, "width": 200, "height": 50}) == set()
