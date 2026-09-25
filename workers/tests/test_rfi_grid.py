"""The grid-mismatch check. Like every RFI check, most of these tests are "this
must NOT be a finding": a regular grid lines up with a shifted copy of itself,
and that shift renames every line — the one false finding this check could
produce by the hundred.

The positive cases are modelled on the RFI that motivated the check: a
structural plan whose own grid (blue) numbers its rows 1..6 while the
architectural background drawn on the same lines (grey) numbers them 1..9.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import fitz  # noqa: E402
import pytest  # noqa: E402

import grid  # noqa: E402
import rfi_grid  # noqa: E402
from rfi_checks import Page  # noqa: E402
from rfi_grid import GridSystem, align, grid_mismatches  # noqa: E402

# Irregular spacing, as a real grid has: a regular one is the hard case and has
# its own tests below.
STRUCT_ROWS = {"6": 215.0, "5": 393.0, "4.6": 512.0, "4": 664.0, "3.3": 844.0, "3": 934.0, "2": 1203.0, "1": 1473.0}
ARCH_ROWS = {"9": 215.0, "8": 406.0, "6.4": 512.0, "6": 664.0, "5": 844.0, "4": 934.0, "3": 1203.0, "2": 1473.0}
COLUMNS = {"A": 421.0, "B": 601.0, "C": 871.0, "D": 1141.0, "E": 1410.0}


def page(pid, sheet, discipline, n=1):
    return Page(pid, "doc-1", n, n, sheet, None, discipline)


def shifted(axis, by):
    return {label: pos + by for label, pos in axis.items()}


# --- alignment -----------------------------------------------------------------------


def test_two_drawings_of_one_grid_align_at_their_offset():
    alignment = align(STRUCT_ROWS, shifted(ARCH_ROWS, -30))
    assert alignment is not None
    assert alignment.offset == pytest.approx(-30, abs=1)
    assert ("6", "9") in alignment.pairs and ("4.6", "6.4") in alignment.pairs


def test_a_regular_grid_is_never_aligned_with_a_shifted_copy_of_itself():
    """Shift a 30ft grid one bay and every line lands on another line with a
    different name. The true alignment wins by one line — not enough."""
    a = {str(n): 100.0 + 240 * n for n in range(1, 9)}
    b = {str(n + 10): 100.0 + 240 * n for n in range(1, 9)}
    assert align(a, b) is None


def test_a_partial_plan_on_a_regular_grid_is_ambiguous():
    whole = {chr(65 + i): 100.0 + 270 * i for i in range(10)}
    part = {"X": 5000.0, "Y": 5270.0, "Z": 5540.0, "W": 5810.0}
    assert align(part, whole) is None


def test_drawings_at_different_scales_do_not_align():
    half = {label: pos / 2 for label, pos in ARCH_ROWS.items()}
    assert align(STRUCT_ROWS, half) is None


def test_two_grids_on_one_sheet_must_line_up_where_they_are_drawn():
    """Same sheet, same coordinates: only offset zero means anything."""
    assert align(STRUCT_ROWS, ARCH_ROWS, fixed_offset=0.0) is not None
    assert align(STRUCT_ROWS, shifted(ARCH_ROWS, 90), fixed_offset=0.0) is None


def test_too_few_lines_never_align():
    a = {"1": 100.0, "2": 300.0, "3": 450.0}
    assert align(a, {"7": 100.0, "8": 300.0, "9": 450.0}) is None


# --- findings ------------------------------------------------------------------------


def test_one_sheet_carrying_two_grids_that_disagree_is_a_high_finding():
    pages = [page("s", "S2.105", "structural")]
    systems = [
        GridSystem("s", "blue 27pt", COLUMNS, STRUCT_ROWS),
        GridSystem("s", "grey 18pt", {}, ARCH_ROWS),
    ]
    findings, notes = grid_mismatches(pages, systems)
    assert notes == []
    assert len(findings) == 1
    f = findings[0]
    assert f.check_type == "grid_mismatch"
    assert f.confidence == "high"
    assert "6 = 9" in f.question and "4.6 = 6.4" in f.question
    assert "(blue = grey)" in f.question
    assert f.facts["axis"] == "numbered"
    # Lines named alike on both are not the finding.
    assert "D" not in f.question


def test_an_architectural_and_a_structural_sheet_are_compared():
    pages = [page("s", "S2.105", "structural", 1), page("a", "A3.01", "architectural", 2)]
    systems = [
        GridSystem("s", "blue 27pt", COLUMNS, STRUCT_ROWS),
        GridSystem("a", "grey 18pt", shifted(COLUMNS, -146), shifted(ARCH_ROWS, -30)),
    ]
    findings, _ = grid_mismatches(pages, systems)
    assert [f.facts["sheets"] for f in findings] == [["S2.105", "A3.01"]]
    assert {e["sheetNumber"] for e in findings[0].evidence} == {"S2.105", "A3.01"}


def test_two_sheets_of_one_discipline_are_not_compared():
    """Two structural levels with different grids are usually two parts of a
    building, not a naming dispute."""
    pages = [page("s1", "S-101", "structural", 1), page("s2", "S-102", "structural", 2)]
    systems = [
        GridSystem("s1", "black 27pt", {}, STRUCT_ROWS),
        GridSystem("s2", "black 27pt", {}, ARCH_ROWS),
    ]
    assert grid_mismatches(pages, systems)[0] == []


def test_a_sheet_whose_number_was_not_read_is_still_compared():
    pages = [page("s", "S2.105", "structural", 1), page("x", None, None, 2)]
    systems = [
        GridSystem("s", "blue 27pt", {}, STRUCT_ROWS),
        GridSystem("x", "grey 18pt", {}, ARCH_ROWS),
    ]
    findings, _ = grid_mismatches(pages, systems)
    assert len(findings) == 1
    assert findings[0].facts["sheets"] == ["S2.105", "page 2"]


def test_grids_named_alike_are_not_a_finding():
    pages = [page("s", "S2.105", "structural", 1), page("a", "A3.01", "architectural", 2)]
    systems = [
        GridSystem("s", "blue 27pt", COLUMNS, STRUCT_ROWS),
        GridSystem("a", "grey 18pt", shifted(COLUMNS, 50), shifted(STRUCT_ROWS, 50)),
    ]
    assert grid_mismatches(pages, systems)[0] == []


def test_the_same_disagreement_seen_twice_is_one_finding():
    """The background grid on the structural sheet and the architectural sheet
    itself say the same thing; the finding is the disagreement, whichever
    order the sheets come in."""
    pages = [page("s", "S2.105", "structural", 1), page("a", "A3.01", "architectural", 2)]
    systems = [
        GridSystem("s", "blue 27pt", {}, STRUCT_ROWS),
        GridSystem("s", "grey 18pt", {}, ARCH_ROWS),
        GridSystem("a", "grey 18pt", {}, shifted(ARCH_ROWS, -30)),
    ]
    findings, _ = grid_mismatches(pages, systems)
    assert len(findings) == 1
    assert len(findings[0].evidence) == 3

    swapped, _ = grid_mismatches(pages, list(reversed(systems)))
    assert [f.fingerprint for f in swapped] == [findings[0].fingerprint]


def test_one_renamed_line_among_many_agreeing_is_medium():
    """One line off between two sheets can be two levels that genuinely
    differ; it is asked, not asserted."""
    arch = dict(COLUMNS, F=1680.0, G=1870.0)
    struct = dict(COLUMNS, F=1680.0, **{"F.7": 1870.0})
    pages = [page("s", "S2.105", "structural", 1), page("a", "A3.01", "architectural", 2)]
    systems = [GridSystem("s", "blue 27pt", struct, {}), GridSystem("a", "grey 18pt", arch, {})]
    findings, _ = grid_mismatches(pages, systems)
    assert [(f.confidence, f.facts["renamedLines"]) for f in findings] == [("medium", ["F.7 = G"])]
    assert findings[0].facts["axis"] == "lettered"


def test_the_highlight_is_one_end_of_the_lines_not_the_whole_sheet():
    bubbles = {label: [[20, y - 13, 47, y + 13], [2300, y - 13, 2327, y + 13]] for label, y in STRUCT_ROWS.items()}
    pages = [page("s", "S2.105", "structural")]
    systems = [
        GridSystem("s", "blue 27pt", {}, STRUCT_ROWS, bubbles),
        GridSystem("s", "grey 18pt", {}, ARCH_ROWS),
    ]
    findings, _ = grid_mismatches(pages, systems)
    box = findings[0].evidence[0]["bbox"]
    assert box["width"] == pytest.approx(27)
    assert box["y"] < 215 < box["y"] + box["height"]


def test_no_grids_anywhere_says_so():
    findings, notes = grid_mismatches([page("s", "S-101", "structural")], [])
    assert findings == [] and "found no grid bubbles" in notes[0]


def test_the_wording_guard_accepts_the_template_it_is_given():
    """The question quotes decimal labels and sheet numbers; the grounding
    guard must accept its own template or every grid finding would lose its
    AI wording for nothing."""
    import rfi_scan

    pages = [page("s", "S2.105", "structural", 1), page("a", "A3.01", "architectural", 2)]
    systems = [
        GridSystem("s", "blue 27pt", {}, STRUCT_ROWS),
        GridSystem("a", "grey 18pt", {}, shifted(ARCH_ROWS, -30)),
    ]
    f = grid_mismatches(pages, systems)[0][0]
    assert rfi_scan.wording_is_grounded(f.question, f)[0]
    ok, _ = rfi_scan.wording_is_grounded("Grid 4.6 on S2.105 is 7.2 on A3.01.", f)
    assert not ok


# --- reading the PDF -----------------------------------------------------------------


def _sheet(rotate=0):
    """A 36x24 sheet with a blue 27pt structural grid and a grey 18pt
    architectural grid on the same row lines, named differently."""
    doc = fitz.open()
    pg = doc.new_page(width=2592, height=1728)
    blue, grey = (0, 0, 1), (0.67, 0.67, 0.67)

    def bubble(x, y, label, diameter, colour):
        pg.draw_circle((x, y), diameter / 2, color=colour)
        size = 9 if diameter > 20 else 6
        pg.insert_text((x - len(label) * size * 0.28, y + size * 0.35), label, fontsize=size)

    for label, y in STRUCT_ROWS.items():
        # 26.9 at one end, 27.0 at the other: one drafter's bubble, which a
        # fixed size bucket would split into two grids.
        bubble(228, y, label, 26.9, blue)
        bubble(2338, y, label, 27.0, blue)
    for label, y in ARCH_ROWS.items():
        bubble(190, y, label, 18.0, grey)
    for label, x in COLUMNS.items():
        bubble(x, 62, label, 27.0, blue)
        bubble(x, 1656, label, 27.0, blue)
    if rotate:
        pg.set_rotation(rotate)
    return doc


def test_styled_systems_separates_two_grids_by_how_they_are_drawn():
    systems = {s["style"]: s for s in grid.styled_systems(_sheet()[0])}
    assert set(systems) == {"blue 27pt", "grey 18pt"}
    blue, grey = systems["blue 27pt"], systems["grey 18pt"]
    assert set(blue["along_y"]) == set(STRUCT_ROWS)
    assert set(blue["along_x"]) == set(COLUMNS)
    assert set(grey["along_y"]) == set(ARCH_ROWS) and grey["along_x"] == {}
    assert blue["along_y"]["4.6"] == pytest.approx(512, abs=1)
    assert len(blue["bubbles"]["4.6"]) == 2  # both ends of the line


@pytest.mark.parametrize("rotate", [90, 270])
def test_styled_systems_reads_a_rotated_sheet_in_display_space(rotate):
    """/Rotate 90 turns the row lines into lines placed along x on screen."""
    systems = {s["style"]: s for s in grid.styled_systems(_sheet(rotate)[0])}
    assert set(systems["grey 18pt"]["along_x"]) == set(ARCH_ROWS)
    assert set(systems["blue 27pt"]["along_x"]) == set(STRUCT_ROWS)


def test_the_whole_path_finds_the_mismatch_on_a_drawn_sheet():
    pdf_page = _sheet()[0]
    pages = [page("s", "S2.105", "structural")]
    systems = [
        GridSystem("s", s["style"], s["along_x"], s["along_y"], s["bubbles"])
        for s in grid.styled_systems(pdf_page)
    ]
    findings, _ = grid_mismatches(pages, systems)
    assert len(findings) == 1
    assert "6 = 9" in findings[0].question
    box = findings[0].evidence[0]["bbox"]
    assert box["width"] < 60  # one end, not the sheet


def test_a_page_without_enough_grid_words_is_not_scanned(monkeypatch):
    doc = fitz.open()
    pg = doc.new_page()
    pg.insert_text((72, 72), "GENERAL NOTES", fontsize=12)
    monkeypatch.setattr(fitz.Page, "get_cdrawings", lambda self: pytest.fail("scanned a notes page"))
    assert grid.styled_systems(pg) == []


def test_the_grid_cache_key_changes_with_the_reader():
    import rfi_scan

    assert f"v{rfi_scan.GRID_CACHE_VERSION}:" in rfi_scan._grid_key("doc", 3)
    assert rfi_grid.CHECK_TYPE == "grid_mismatch"


def test_a_handful_of_coincident_lines_is_not_an_alignment():
    """Two unrelated ten-line grids at one scale will share a few positions by
    chance. Four of ten lining up is coincidence, not one grid drawn twice."""
    a = {str(n): p for n, p in enumerate([100, 230, 390, 520, 700, 810, 1000, 1170, 1300, 1480], 1)}
    b = {chr(64 + n): p for n, p in enumerate([100, 230, 390, 520, 875, 1085, 1183, 1372, 1393, 1512], 1)}
    assert align(a, b) is None


def test_markup_is_not_read_as_part_of_the_drawing():
    """A client's markup must not become a grid the check compares. PyMuPDF
    reads annotation appearances as page content — a pasted snapshot of the
    architectural grid on a structural sheet read as a second grid drawn by the
    engineer — so the annotations go before anything is read."""
    doc = fitz.open()
    pg = doc.new_page(width=2592, height=1728)
    for y in ARCH_ROWS.values():
        pg.add_circle_annot(fitz.Rect(172, y - 9, 190, y + 9))
    for label, y in STRUCT_ROWS.items():
        pg.draw_circle((228, y), 13.5, color=(0, 0, 1))
        pg.insert_text((225, y + 3), label, fontsize=9)

    def circles(page):
        return sum(1 for d in page.get_cdrawings() if 17 < fitz.Rect(d["rect"]).width < 19)

    assert circles(pg) == len(ARCH_ROWS), "fixture: annotation circles read as drawings"
    cleaned = grid.without_markup(pg)
    assert circles(cleaned) == 0
    assert [s["style"] for s in grid.styled_systems(cleaned)] == ["blue 27pt"]
