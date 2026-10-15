"""section_pairs: two sections one plan cuts through the same element.

Built from Leland BIM RFI 007: S8.13 calls out 3/S4.00 and 4/S4.00 through one
elevator pit, both titled ELEVATOR PIT DETAIL, and they disagree on the pit
slab. On the real set the code pairs exactly 3 and 4 — not S4.00's two
"GRADE BEAM AT CONCRETE WALL" details, which are one condition at two walls.
"""

import os
import sys

import fitz

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import fullscan_run  # noqa: E402
import section_pairs as sp  # noqa: E402
from sheet_facts import PageFacts  # noqa: E402


def detail_lines(number: str, title: str, x: float, y: float) -> list:
    """A detail's title block as text lines: number bubble, title, SCALE line."""
    return [
        ((x - 40, y - 10, x - 25, y + 8), number),
        ((x, y - 22, x + 200, y - 4), title),
        ((x, y, x + 120, y + 10), 'SCALE: 3/4" = 1\'-0"'),
    ]


def test_reads_number_and_title_above_each_scale_line():
    lines = detail_lines("3", "ELEVATOR PIT DETAIL", 100, 500) + detail_lines("21", "GRADE BEAM AT WALL", 700, 500)
    assert [(d.number, d.title) for d in sp.detail_titles(lines)] == [("3", "ELEVATOR PIT DETAIL"), ("21", "GRADE BEAM AT WALL")]


def test_a_title_on_two_lines_is_one_title():
    lines = detail_lines("17", "GRADE BEAM AT CMU WALL", 100, 500) + [((100, 455, 180, 473), "INTERIOR")]
    assert sp.detail_titles(lines)[0].title == "INTERIOR GRADE BEAM AT CMU WALL"


def test_cells_reach_up_to_the_title_above_and_across_to_the_next():
    details = sp.detail_titles(
        detail_lines("5", "PIT DETAIL", 100, 300) + detail_lines("4", "PIT DETAIL", 100, 800) + detail_lines("9", "WALL DETAIL", 600, 800))
    c = sp.cells(details, (0, 0, 1200, 1000))
    assert c["4"][1] == 310 + 10  # just below 5's scale line
    assert c["4"][2] == 600 - 40 - 20  # stops before 9's number bubble
    assert c["5"][1] == 20  # top row: the page top


def test_a_callout_is_a_number_over_the_sheet_number():
    words = [((100, 90, 108, 100), "3"), ((92, 104, 120, 112), "S4.00"), ((300, 90, 308, 100), "3")]
    assert sp.callouts(words, "S4.00") == [("3", 104.0, 95.0)]


def _details():
    return sp.detail_titles(detail_lines("3", "ELEVATOR PIT DETAIL", 100, 500)
                            + detail_lines("4", "ELEVATOR PIT DETAIL", 600, 500)
                            + detail_lines("21", "GRADE BEAM AT WALL", 100, 900)
                            + detail_lines("23", "GRADE BEAM AT WALL", 600, 900))


def test_only_same_title_details_cut_at_one_place_pair():
    calls = [("3", 500, 400), ("4", 500, 480),      # one pit, two cuts 80pt apart
             ("21", 100, 100), ("23", 900, 900)]    # one condition at two walls
    assert [(a.number, b.number) for a, b in sp.same_cuts(_details(), calls, 120)] == [("3", "4")]


def test_different_titles_never_pair_however_close():
    calls = [("3", 500, 400), ("21", 500, 420)]
    assert sp.same_cuts(_details(), calls, 120) == []


def _sheet(doc, sheet: str, draw):
    page = doc.new_page(width=1200, height=1000)
    draw(page)
    page.insert_text((1100, 980), sheet, fontsize=10)


def _facts(n: int, sheet: str) -> PageFacts:
    return PageFacts(f"p{n}", "doc", n, n, sheet, "structural")


def test_find_pairs_the_two_cuts_from_the_pdf():
    doc = fitz.open()

    def details(page):
        for number, title, x, y in (("3", "ELEVATOR PIT DETAIL", 120, 900), ("4", "ELEVATOR PIT DETAIL", 620, 900),
                                    ("21", "GRADE BEAM AT WALL", 120, 450), ("23", "GRADE BEAM AT WALL", 620, 450)):
            page.insert_text((x - 40, y), number, fontsize=14)
            page.insert_text((x, y - 8), title, fontsize=14)
            page.insert_text((x, y + 10), 'SCALE: 3/4" = 1\'-0"', fontsize=8)
            page.draw_rect(fitz.Rect(x, y - 300, x + 300, y - 40))

    def plan(page):
        page.insert_text((100, 950), 'PLAN  SCALE: 3/8" = 1\'-0"', fontsize=10)  # 27pt a foot: 5 ft is 135pt
        for number, x, y in (("3", 500, 400), ("4", 500, 470), ("21", 100, 100), ("23", 900, 800)):
            page.insert_text((x, y), number, fontsize=8)
            page.insert_text((x - 12, y + 10), "S4.00", fontsize=8)

    _sheet(doc, "S4.00", details)
    _sheet(doc, "S8.13", plan)
    facts = [_facts(1, "S4.00"), _facts(2, "S8.13")]
    texts = {"p1": doc[0].get_text(), "p2": doc[1].get_text()}
    pairs = sp.find(facts, texts, lambda d, n: doc[n - 1])
    assert len(pairs) == 1
    pair = pairs[0]
    assert pair.kind == "sections" and pair.a is pair.b
    assert pair.labels == ("section 3/S4.00", "section 4/S4.00")
    assert "S8.13" in pair.reason
    (wa, wb), = pair.windows
    assert wa[0] < 120 < wa[2] and wb[0] < 620 < wb[2]  # each window is its own detail
    tile = pair.tiles()[0]
    assert tile["labels"] == ["section 3/S4.00", "section 4/S4.00"]


def test_section_tiles_are_labelled_by_section_not_as_the_same_area():
    pair = {"reason": "Sections 3/S4.00 and 4/S4.00 are cut through one element", "a": {"sheetNumber": "S4.00"},
            "b": {"sheetNumber": "S4.00"}, "kind": "sections"}
    tile = {"tile": 0, "windows": {"labels": ["section 3/S4.00", "section 4/S4.00"]}}
    _, labels = fullscan_run.tile_prompt(pair, tile, "", "")
    assert labels == ["Image A — section 3/S4.00", "Image B — section 4/S4.00"]
    assert fullscan_run.is_sections(pair)
    assert "SAME AREA" not in fullscan_run.section_discovery_system()
    assert "SEE PLAN" in fullscan_run.section_verify_system()
