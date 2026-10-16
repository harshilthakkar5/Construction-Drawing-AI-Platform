"""element_marks: an element drawn like the marked ones, with no mark.

Built from Leland BIM RFI 001 ("pile type mark is not indicated at grid
A1/BK"): on the real S2.30-B the check finds that pier and nothing else on
the 99-sheet structural set. These tests draw the same situation from scratch.
"""

import math
import os
import sys

import fitz

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import element_marks as em  # noqa: E402
from rfi_checks import Chunk, Page  # noqa: E402

PIER = 0.36  # the symbol's stroke width
GRID = 0.24


def pier(page: fitz.Page, x: float, y: float) -> None:
    """A pier symbol as a CAD export draws it: a circle of 24 separate short
    strokes plus four corner brackets, every piece its own path."""
    r = 9.0
    for i in range(24):
        a, b = 2 * math.pi * i / 24, 2 * math.pi * (i + 1) / 24
        page.draw_line((x + r * math.cos(a), y + r * math.sin(a)), (x + r * math.cos(b), y + r * math.sin(b)),
                       width=PIER)
    for sx in (-1, 1):
        for sy in (-1, 1):
            cx, cy = x + sx * 11, y + sy * 11
            page.draw_line((cx, cy), (cx - sx * 4, cy), width=PIER)
            page.draw_line((cx, cy), (cx, cy - sy * 4), width=PIER)


def sheet(marked: int, bare: list[tuple[float, float]], label_inside: bool = False) -> fitz.Page:
    doc = fitz.open()
    page = doc.new_page(width=1600, height=1000)
    for x in range(100, 1500, 50):  # grid linework in its own style
        page.draw_line((x, 50), (x, 950), width=GRID, dashes="[6 3] 0")
    placed = 0
    for row in range(4):
        for col in range(6):
            if placed == marked:
                break
            x, y = 150 + col * 200, 150 + row * 200
            pier(page, x, y)
            if label_inside:
                page.insert_text((x - 6, y + 3), f"B{placed + 1}", fontsize=7)
            else:
                page.insert_text((x + 14, y - 12), f"P30-{12 + placed % 3 * 6}", fontsize=7)
            placed += 1
    for x, y in bare:
        pier(page, x, y)
    return page


def test_finds_the_one_pier_nobody_marked():
    page = sheet(12, [(1450, 850)])
    found, notes = em.unmarked_on_page(page)
    assert [(u.family, round(em.centre(u.box)[0]), round(em.centre(u.box)[1])) for u in found] == [("P##-##", 1450, 850)]
    assert found[0].examples == ("P30-12", "P30-18", "P30-24")
    assert notes == []


def test_a_fully_marked_sheet_finds_nothing():
    assert em.unmarked_on_page(sheet(12, [])) == ([], [])


def test_too_few_marks_teach_nothing():
    assert em.unmarked_on_page(sheet(em.MIN_MARKS - 1, [(1450, 850)])) == ([], [])


def test_a_label_inside_its_symbol_is_a_bubble_not_a_mark():
    # Grid bubbles and callouts are drawn with their label: an empty one is not an omission.
    assert em.unmarked_on_page(sheet(12, [(1450, 850)], label_inside=True)) == ([], [])


def test_many_bare_copies_mean_the_wrong_symbol_was_learned():
    bare = [(1450, 150 + 100 * i) for i in range(em.MAX_UNMARKED + 1)]
    found, notes = em.unmarked_on_page(sheet(24, bare))
    assert found == []
    assert notes and "too many to be omissions" in notes[0]


def test_a_symbol_more_bare_than_marked_is_not_the_elements_symbol():
    found, _ = em.unmarked_on_page(sheet(8, [(1450, 150 + 100 * i) for i in range(4)]))
    assert found == []  # 8 marked of 12: under MIN_SHAPE_SHARE


def test_symbols_merge_strokes_by_proximity():
    syms = em.symbols([(0, 0, 2, 2), (3, 0, 5, 2), (50, 50, 52, 52)], gap=1.5)
    assert sorted(n for _, n in syms) == [1, 2]


def test_family_is_the_mark_shape():
    assert em.family("P30-12") == "P##-##"
    assert em.family("C-6") == "C-#"
    assert em.family("F.7") is None  # a grid line
    assert em.family("S4.00") is None  # a sheet


def test_only_pages_whose_text_repeats_a_mark_are_opened():
    pages = [Page("a", "d", 1, 1, "S1"), Page("b", "d", 2, 2, "S2")]
    chunks = [Chunk("c1", "a", " ".join(f"P30-{n}" for n in range(12)), None), Chunk("c2", "b", "GENERAL NOTES P30-12", None)]
    assert em.candidate_page_ids(pages, chunks) == {"a"}


def test_scan_findings_cite_the_symbol(tmp_path):
    page = sheet(12, [(1450, 850)])
    row = Page("p", "doc", 1, 7, "S2.30-B")
    chunks = [Chunk("c", "p", page.get_text(), None)]
    found, notes = em.findings([row], chunks, lambda d, n: page)
    assert len(found) == 1
    f = found[0]
    assert f.check_type == "unmarked_element" and f.confidence == "medium"
    assert "S2.30-B" in f.subject and "P30-12" in f.question
    box = f.evidence[0]["bbox"]
    assert abs(box["x"] + box["width"] / 2 - 1450) < 2 and abs(box["y"] + box["height"] / 2 - 850) < 2
    # Same drawing, same fingerprint: a re-scan does not propose it again.
    assert em.findings([row], chunks, lambda d, n: page)[0][0].fingerprint == f.fingerprint


def test_pieces_of_one_element_count_once():
    # Leland S2.31-A: a pile's circle is cut in two by the wall line over it, so
    # each unmarked pile was two symbols and three piles were "6, too many".
    found = em._merge([em.Unmarked("P##-##", (100, 100, 103, 111), ("P30-12",), 9),
                       em.Unmarked("P##-##", (111, 100, 114, 111), ("P30-12",), 9),
                       em.Unmarked("P##-##", (400, 100, 403, 111), ("P30-12",), 9)], within=20)
    assert sorted(round(u.box[0]) for u in found) == [100, 400]
    assert em._merge([], within=20) == []


def test_unmarked_elements_on_one_sheet_are_one_finding_with_a_box_each():
    page = sheet(14, [(1450, 750), (1450, 850)])
    row = Page("p", "doc", 1, 7, "S2.31-A")
    found, _ = em.findings([row], [Chunk("c", "p", page.get_text(), None)], lambda d, n: page)
    assert len(found) == 1
    assert found[0].facts["count"] == 2 and len(found[0].evidence) == 2
    assert "2 elements" in found[0].subject and "their type mark" in found[0].question


def test_an_architectural_sheet_is_not_read():
    # Its repeated marks are tags (wall types, doors), not element type marks.
    page = sheet(12, [(1450, 850)])
    row = Page("p", "doc", 1, 7, "A2.30", discipline="architectural")
    assert em.findings([row], [Chunk("c", "p", page.get_text(), None)], lambda d, n: page)[0] == []
