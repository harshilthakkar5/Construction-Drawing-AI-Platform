"""A schedule row and a plan that point at each other (schedule_loops.py), on
a drawing made in the test."""
import sys
from pathlib import Path

import fitz

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import rfi_checks  # noqa: E402
import schedule_loops  # noqa: E402


def _sheets():
    doc = fitz.open()
    sched = doc.new_page(width=1200, height=800)
    sched.insert_text((300, 100), "FOOTING SCHEDULE", fontsize=14)
    sched.insert_text((100, 140), "MARK", fontsize=9)
    sched.insert_text((250, 140), "SIZE", fontsize=9)
    sched.insert_text((400, 140), "TOP OF FOOTING", fontsize=9)
    for y, mark, size, top in ((170, "F1", "4'-0\" x 4'-0\"", "-2'-0\""),
                              (195, "F2", "5'-0\" x 5'-0\"", "SEE PLAN"),
                              (220, "F3", "6'-0\" x 6'-0\"", "SEE PLAN")):
        sched.insert_text((100, y), mark, fontsize=9)
        sched.insert_text((250, y), size, fontsize=9)
        sched.insert_text((400, y), top, fontsize=9)
    # Not a schedule row: a note that starts with a mark, under no heading.
    sched.insert_text((100, 700), "F2 SEE PLAN FOR STEPS", fontsize=9)
    plan = doc.new_page(width=1200, height=800)
    plan.insert_text((300, 300), "F2", fontsize=9)
    plan.insert_text((300, 315), "T.O.F. SEE SCHEDULE", fontsize=7)  # points back: the loop
    plan.insert_text((700, 300), "F3", fontsize=9)
    plan.insert_text((700, 315), "T.O.F. -2'-6\"", fontsize=7)  # the value the schedule deferred
    return doc


def _words(page):
    return [tuple(w[:5]) for w in page.get_text("words")]


def test_rows_that_say_see_plan_under_a_schedule_heading():
    rows = schedule_loops.deferring_rows(_words(_sheets()[0]))
    assert [r["mark"] for r in rows] == ["F2", "F3"]
    assert rows[0]["title"] == "FOOTING SCHEDULE"
    assert rows[0]["header"] == "TOP OF FOOTING"


def test_two_schedules_titled_on_one_line_keep_their_own_titles():
    doc = fitz.open()
    page = doc.new_page(width=3400, height=800)
    page.insert_text((700, 100), "POST-TENSIONED BEAM SCHEDULE", fontsize=14)
    page.insert_text((2200, 100), "POST-TENSIONED BEAM SCHEDULE", fontsize=14)
    for x, y, mark in ((150, 150, "1PB1"), (150, 175, "1PB2"), (1700, 150, "2PB1"), (1700, 175, "2PB2")):
        page.insert_text((x, y), mark, fontsize=9)
        page.insert_text((x + 200, y), "SEE PLAN", fontsize=9)
    heads = schedule_loops.headings(_words(page))
    assert [h["title"] for h in heads] == ["POST-TENSIONED BEAM SCHEDULE"] * 2
    assert heads[0]["box"][2] < heads[1]["box"][0]
    # Leland's level-prefixed beams (1PB1) are marks of the PB family, and each
    # side's row stops at the next table's mark.
    rows = schedule_loops.deferring_rows(_words(page))
    assert sorted(r["mark"] for r in rows) == ["1PB1", "1PB2", "2PB1", "2PB2"]


def test_a_loop_is_a_finding_and_a_value_on_the_plan_is_not():
    doc = _sheets()
    pages = [rfi_checks.Page(id=f"p{i}", document_id="doc", page_number=i + 1, combined_page_number=i + 1,
                             sheet_number=s) for i, s in enumerate(("S0.1", "S1.1"))]
    chunks = [rfi_checks.Chunk(id=f"c{i}", page_id=f"p{i}", text=doc[i].get_text(), bbox=None) for i in range(2)]
    found, notes = schedule_loops.findings(pages, chunks, lambda _d, n: doc[n - 1])
    assert notes == []
    assert [f.facts["mark"] for f in found] == ["F2"]  # F3's plan gives the value
    f = found[0]
    assert f.check_type == "schedule_plan_loop" and f.confidence == "high"
    assert f.facts["planSheets"] == ["S1.1"] and f.facts["column"] == "TOP OF FOOTING"
    assert "SEE PLAN" in f.question and "SEE SCHEDULE" in f.question and "S0.1" in f.question
    assert [e["sheetNumber"] for e in f.evidence] == ["S0.1", "S1.1"]
    # The same loop seen again is the same finding.
    again, _ = schedule_loops.findings(pages, chunks, lambda _d, n: doc[n - 1])
    assert again[0].fingerprint == f.fingerprint


def test_a_review_finds_the_plan_side_on_a_sheet_it_did_not_review():
    doc = _sheets()
    pages = [rfi_checks.Page(id=f"p{i}", document_id="doc", page_number=i + 1, combined_page_number=i + 1,
                             sheet_number=s) for i, s in enumerate(("S0.1", "S1.1"))]
    chunks = [rfi_checks.Chunk(id=f"c{i}", page_id=f"p{i}", text=doc[i].get_text(), bbox=None) for i in range(2)]
    found, _ = schedule_loops.findings(pages[:1], chunks, lambda _d, n: doc[n - 1], every=pages)
    assert [f.facts["mark"] for f in found] == ["F2"]
    found, _ = schedule_loops.findings(pages[:1], chunks, lambda _d, n: doc[n - 1])
    assert found == []  # without the plan in view there is no loop to see


def test_registered_like_every_pdf_check():
    assert "schedule_plan_loop" in rfi_checks.CHECK_TYPES and "schedule_plan_loop" in rfi_checks.PDF_CHECK_TYPES
