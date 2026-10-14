"""plan_series: a level missing from a series of plans (the client's RFI 007).

The finding exists only when a series clearly runs through the level, another
series of the same discipline shows that level, and no title for it is printed
anywhere in the set — every one of those is a refusal tested below."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import plan_series as ps  # noqa: E402
import rfi_checks  # noqa: E402
from rfi_checks import Chunk, Page  # noqa: E402


def _set(titles: dict[str, tuple[str, str]]):
    """{sheet: (discipline, title text)} -> (pages, chunks)."""
    pages, chunks = [], []
    for n, (sheet, (discipline, text)) in enumerate(titles.items(), start=1):
        pages.append(Page(id=sheet, document_id="d", page_number=n, combined_page_number=n,
                          sheet_number=sheet, discipline=discipline))
        chunks.append(Chunk(id=f"c-{sheet}", page_id=sheet, text=text,
                            bbox={"x": 0, "y": 0, "width": 10, "height": 10}, identifiers=frozenset()))
    return pages, chunks


# The UT Law structural set, as its titles read: a Forming Plan for every level
# but 6, and PT and framing series that do have one.
UT_LAW = {
    "S2.102": ("structural", "Forming Plan - Level 2"),
    "S2.103": ("structural", "Forming Plan - Level 3-4"),
    "S2.105": ("structural", "Forming Plan - Level 5"),
    "S2.107": ("structural", "Forming Plan - Level 7-13"),
    "S2.114": ("structural", "Forming Plan - Level 14"),
    "S2.115": ("structural", "Forming Plan - Roof"),
    "S2.302": ("structural", "PT Plan - Level 2"),
    "S2.303": ("structural", "PT Plan - Level 3-4"),
    "S2.305": ("structural", "PT Plan - Level 5"),
    "S2.306": ("structural", "PT Plan - Level 6"),
    "S2.307": ("structural", "PT Plan - Level 7-13"),
    "S2.314": ("structural", "PT Plan - Level 14"),
    "S2.201": ("structural", "Enlarged Forming Plan - Stair 2 - Level 2\nEnlarged Forming Plan - Stair 2 - Level 14"),
    "S1.101": ("structural", "Foundation Plan - Level 1"),
}


def test_names_read_as_printed():
    assert ps.display("FORMING PLAN") == "Forming Plan" and ps.display("PT PLAN") == "PT Plan"


def test_titles_are_read_as_a_series_and_its_levels():
    assert ps.read_title("Forming Plan - Level 7-13") == ("FORMING PLAN", list(range(7, 14)), "FORMING PLAN - LEVEL 7-13")
    assert ps.read_title("PT PLANS – LEVELS 2") == ("PT PLAN", [2], "PT PLANS – LEVELS 2")
    # Never a series: enlarged, qualified, a note, too long.
    assert ps.read_title("Enlarged Forming Plan - Stair 2 - Level 2") is None
    assert ps.read_title("ENLARGED FORMING PLAN - LEVEL 2") is None
    assert ps.read_title("SEE FORMING PLAN - LEVEL 2 FOR ALL SLAB EDGE DIMENSIONS NOT SHOWN ON THIS SHEET") is None
    assert ps.read_title("Forming Plan - Roof") is None


def test_the_level_6_forming_plan_rfi_is_found():
    pages, chunks = _set(UT_LAW)
    found, _ = ps.missing_plan_levels(pages, chunks)
    assert [(f.facts["series"], f.facts["missingLevel"]) for f in found] == [("Forming Plan", 6)]
    f = found[0]
    assert f.check_type == "missing_plan_level" and f.confidence == "medium"
    assert "cover Levels 2-5 and 7-14" in f.question and "PT Plan - Level 6 (S2.306)" in f.question
    # Evidence: the two neighbours of the gap, and where Level 6 does appear.
    assert [(e["sheetNumber"], e["role"]) for e in f.evidence] == [
        ("S2.105", "finding"), ("S2.107", "finding"), ("S2.306", "context")]


# The project's drawing index, as UT Law prints it on A1.00 (number, then title
# on the next line) and on S0.000 (a table). Both list S2.106, which the issued
# set does not contain.
INDEX_LINES = "INDEX TO DRAWINGS\n" + "\n".join(
    f"{n}\nForming Plan - Level {lv}" for n, lv in
    (("S2.102", "2"), ("S2.103", "3-4"), ("S2.105", "5"), ("S2.106", "6"), ("S2.107", "7-13"), ("S2.114", "14")))
INDEX_TABLE = "| SHEET LIST |  |\n" + "\n".join(
    f"| {n} | Forming Plan - Level {lv} |" for n, lv in
    (("S2.102", "2"), ("S2.103", "3-4"), ("S2.105", "5"), ("S2.106", "6"), ("S2.107", "7-13"), ("S2.114", "14")))


def test_the_index_names_the_one_sheet_the_issued_set_lacks():
    """RFI 007 as the client wrote it: the index lists S2.106, the set has no S2.106."""
    for index in (INDEX_LINES, INDEX_TABLE):
        pages, chunks = _set({**UT_LAW, "A1.00": ("architectural", index)})
        found, notes = ps.missing_plan_levels(pages, chunks)
        assert [(f.facts["missingLevel"], f.facts.get("indexedSheet")) for f in found] == [(6, "S2.106")], index
        f = found[0]
        assert f.confidence == "high" and "S2.106 Forming Plan - Level 6" in f.subject
        assert "index on A1.00 lists S2.106" in f.question and "cover Levels 2-5 and 7-14" in f.question
        assert ("A1.00", "finding") in [(e["sheetNumber"], e["role"]) for e in f.evidence]
        assert notes == []


def test_an_index_entry_covers_a_level_the_sheet_title_omits():
    """S2.103's own view title reads "Level 3"; the index says 3-4. Level 4 is not missing."""
    titles = {**UT_LAW, "S2.103": ("structural", "Forming Plan - Level 3"), "A1.00": ("architectural", INDEX_LINES)}
    pages, chunks = _set(titles)
    assert [f.facts["missingLevel"] for f in ps.missing_plan_levels(pages, chunks)[0]] == [6]
    without_index = {k: v for k, v in titles.items() if k != "A1.00"}
    pages, chunks = _set(without_index)
    assert [f.facts["missingLevel"] for f in ps.missing_plan_levels(pages, chunks)[0]] == [4, 6]


def test_the_index_page_is_not_read_as_drawings():
    # Every series of the index alone would be one sheet carrying every level.
    pages, chunks = _set({"A1.00": ("architectural", INDEX_LINES), "A3.01": ("architectural", "Floor Plan - Level 1")})
    assert ps.missing_plan_levels(pages, chunks) == ([], [])


def test_a_partial_upload_is_a_note_not_a_finding():
    """Several indexed sheets of the series absent: the upload is partial."""
    partial = {k: v for k, v in UT_LAW.items() if k != "S2.114"}
    pages, chunks = _set({**partial, "A1.00": ("architectural", INDEX_LINES)})
    found, notes = ps.missing_plan_levels(pages, chunks)
    assert found == [] and len(notes) == 1 and "S2.106" in notes[0] and "1 other" in notes[0]


def test_no_gap_without_another_series_showing_the_level():
    no_pt6 = {k: v for k, v in UT_LAW.items() if k != "S2.306"}
    pages, chunks = _set(no_pt6)
    assert ps.missing_plan_levels(pages, chunks)[0] == []


def test_another_disciplines_level_does_not_count():
    moved = {**UT_LAW, "S2.306": ("architectural", "PT Plan - Level 6")}
    pages, chunks = _set(moved)
    assert ps.missing_plan_levels(pages, chunks)[0] == []


def test_a_short_series_and_levels_outside_its_range_are_not_gaps():
    pages, chunks = _set({
        "S1": ("structural", "Foundation Plan - Level 1"),
        "S2": ("structural", "Framing Plan - Level 2"),
        "S3": ("structural", "Framing Plan - Level 4"),
        "S4": ("structural", "PT Plan - Level 2"),
        "S5": ("structural", "PT Plan - Level 3"),
        "S6": ("structural", "PT Plan - Level 4"),
        "S7": ("structural", "PT Plan - Level 5"),
    })
    # Framing has only two levels; PT runs past Foundation's single level.
    assert ps.missing_plan_levels(pages, chunks)[0] == []


def test_the_check_runs_with_the_project_checks():
    pages, chunks = _set(UT_LAW)
    found, _ = rfi_checks.run_all(pages, chunks, grids=None, only={"missing_plan_level"})
    assert [f.check_type for f in found] == ["missing_plan_level"]
    assert "missing_plan_level" in rfi_checks.CHECK_TYPES
