"""column_schedule: a column the schedule lists but never sizes.

Built from Leland BIM RFI 005 (S8.02's column for B6-BA, B6-BB, B7-BA, B7-BB
prints no size at any level). On the real S8.02 the check finds that column
and B3(2' - 4 3/4")-BB, and nothing on the 152-sheet UT Law structural set.
"""

import os
import sys

import fitz

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import column_schedule as cs  # noqa: E402
from rfi_checks import Chunk, Page  # noqa: E402


def schedule(columns: list[tuple[str, bool]]) -> fitz.Page:
    """A Revit graphical column schedule: one table column per location group,
    "Column Locations" along the bottom, sizes in the body above."""
    doc = fitz.open()
    page = doc.new_page(width=200 + 140 * len(columns), height=800)
    page.insert_text((60, 600), "Column", fontsize=8)
    page.insert_text((60, 612), "Locations", fontsize=8)
    for i, (locations, sized) in enumerate(columns):
        x = 150 + 140 * i
        page.draw_line((x, 50), (x, 700), width=0.5)
        page.insert_text((x + 6, 620), locations, fontsize=7)
        if sized:
            page.insert_text((x + 30, 400), '18" X 24"', fontsize=7)
            page.insert_text((x + 30, 410), "(12) #7", fontsize=7)
    page.draw_line((150 + 140 * len(columns), 50), (150 + 140 * len(columns), 700), width=0.5)
    return page


def test_finds_the_column_with_locations_and_no_size():
    page = schedule([("A1-AG, A1-AH", True), ("B6-BA, B6-BB", False), ("B5-BC", True), ("B7-BF", True)])
    found, notes = cs.on_page(page)
    assert [f["locations"] for f in found] == [["B6-BA", "B6-BB"]]
    assert notes == []


def test_an_offset_location_is_kept_as_printed():
    page = schedule([("A1-AG", True), ("B3(2' - 4 3/4\")-BB", False), ("B5-BC", True), ("B7-BF", True)])
    assert cs.on_page(page)[0][0]["locations"] == ["B3(2' - 4 3/4\")-BB"]


def test_a_fully_sized_schedule_finds_nothing():
    assert cs.on_page(schedule([("A1-AG", True), ("B5-BC", True), ("B7-BF", True)])) == ([], [])


def test_a_table_mostly_empty_was_not_read_and_says_so():
    found, notes = cs.on_page(schedule([("A1-AG", True), ("B5-BC", True), ("B7-BF", True),
                                        ("B6-BA", False), ("B6-BB", False)]))
    assert found == [] and notes and "not read reliably" in notes[0]


def test_scan_findings_cite_the_schedule_column():
    page = schedule([("A1-AG", True), ("B6-BA, B6-BB", False), ("B5-BC", True), ("B7-BF", True)])
    row = Page("p", "doc", 1, 93, "S8.02")
    chunks = [Chunk("c", "p", page.get_text(), None)]
    found, _ = cs.findings([row], chunks, lambda d, n: page)
    assert len(found) == 1
    f = found[0]
    assert f.check_type == "unsized_column" and "B6-BA, B6-BB" in f.question and "S8.02" in f.question
    box = f.evidence[0]["bbox"]
    assert box["x"] <= 290 + 6 <= box["x"] + box["width"]  # the empty column's own cell
    assert cs.findings([row], [Chunk("c", "p", "GENERAL NOTES", None)], lambda d, n: page) == ([], [])


def test_the_question_reads_in_the_plural_for_a_group_and_singular_for_one():
    page = schedule([("A1-AG", True), ("B6-BA, B6-BB", False), ("B5-BC", True), ("B3-BB", False), ("B7-BF", True),
                     ("B2-BC", True), ("B4-BK", True)])
    row = Page("p", "doc", 1, 93, "S8.02")
    found, _ = cs.findings([row], [Chunk("c", "p", page.get_text(), None)], lambda d, n: page)
    by = {f.facts["locations"][0]: f.question for f in found}
    assert "lists the columns at B6-BA, B6-BB" in by["B6-BA"] and "for them at any level" in by["B6-BA"]
    assert "column sizes and reinforcement" in by["B6-BA"]
    assert "lists the column at B3-BB" in by["B3-BB"] and "for it at any level" in by["B3-BB"]


def test_sizes_columns_only_for_a_schedule_read_reliably():
    assert cs.sizes_columns(schedule([("A1-AG", True), ("B5-BC", True), ("B7-BF", True), ("B6-BA", False)]))
    # mostly empty: not read as a schedule, so it answers nothing
    assert not cs.sizes_columns(schedule([("A1-AG", True), ("B6-BA", False), ("B6-BB", False)]))
