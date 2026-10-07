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


def _cross_sheet(scale_s=(), scale_a=(), bubbles_a=None):
    pages = [page("s", "S1.102", "structural", 1), page("a", "A3.36", "architectural", 2)]
    systems = [
        GridSystem("s", "blue 27pt", COLUMNS, STRUCT_ROWS, scales=scale_s),
        GridSystem(
            "a", "grey 18pt", shifted(COLUMNS, -146), shifted(ARCH_ROWS, -30),
            bubbles=bubbles_a or {}, scales=scale_a,
        ),
    ]
    return grid_mismatches(pages, systems)


def test_sheets_printing_different_scales_are_never_compared():
    """The client's S1.102 (overall plan, 1/8") against A3.36 (two enlarged
    1/4" details): four lines of one fell on four of the other by translation
    and the check reported grid 4 = grid 1, on two sheets that name every line
    the same. A shared printed scale is now required."""
    findings, notes = _cross_sheet(scale_s=(9.0,), scale_a=(18.0,))
    assert findings == []
    assert any("different drawing scales" in n and "S1.102" in n and "A3.36" in n for n in notes)


def test_sheets_sharing_a_scale_are_still_compared():
    # The same pair at one scale is RFI 002's shape and must stay a finding;
    # a page that also prints a detail scale shares the plan scale.
    assert len(_cross_sheet(scale_s=(9.0,), scale_a=(9.0, 36.0))[0]) == 1
    # No scale read on one side compares as before.
    assert len(_cross_sheet(scale_s=(9.0,), scale_a=())[0]) == 1


def test_a_sheet_showing_one_label_in_two_places_is_several_views_not_one_grid():
    """Two enlarged details side by side each bubble their own A: the label
    sits at two positions, and positions on that sheet are paper-space."""
    two_views = {"A": [[100.0, 50.0, 118.0, 68.0], [900.0, 50.0, 918.0, 68.0]]}
    findings, notes = _cross_sheet(bubbles_a=two_views)
    assert findings == []
    assert any("A3.36" in n and "two places" in n for n in notes)


def test_the_two_ends_of_one_kinked_line_are_still_one_line():
    # S2.105's H is bubbled 18pt apart at its two ends.
    kinked = {"A": [[100.0, 50.0, 118.0, 68.0], [118.0, 1600.0, 136.0, 1618.0]]}
    assert len(_cross_sheet(bubbles_a=kinked)[0]) == 1


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


# Architectural column letters for the same lines the structural sheets letter
# A..E — a second axis of the same naming dispute.
ARCH_COLUMNS = {"A": 421.0, "B": 601.0, "C.4": 871.0, "D": 1141.0, "G": 1410.0}


def test_one_naming_dispute_between_two_disciplines_is_one_rfi():
    """The client's set produced FIVE grid findings for one dispute — S2.105 vs
    A3.01 numbered and lettered, A3.01 vs S2.107 numbered and twice lettered —
    where the team wrote ONE RFI, "Confirm the grid layout". Findings between
    the same two disciplines whose renamings agree are one question."""
    pages = [
        page("s1", "S2.105", "structural", 1),
        page("a", "A3.01", "architectural", 2),
        page("s2", "S2.107", "structural", 3),
    ]
    other_rows = dict(STRUCT_ROWS, **{"7": 1600.0})  # a second structural sheet, a different extent
    systems = [
        GridSystem("s1", "blue 27pt", COLUMNS, STRUCT_ROWS),
        GridSystem("a", "grey 18pt", shifted(ARCH_COLUMNS, -146), shifted(ARCH_ROWS, -30)),
        GridSystem("s2", "blue 27pt", shifted(COLUMNS, 12), shifted(other_rows, 12)),
    ]
    findings, _ = grid_mismatches(pages, systems)
    assert len(findings) == 1, [f.subject for f in findings]
    f = findings[0]
    assert "architectural" in f.subject and "structural" in f.subject
    assert "numbered and lettered" in f.subject
    assert "(architectural = structural)" in f.question
    assert "9 = 6" in f.question and "C.4 = C" in f.question and "G = E" in f.question
    assert set(f.facts["sheets"]) == {"S2.105", "A3.01", "S2.107"}


def test_a_renaming_that_contradicts_the_merged_one_is_its_own_rfi():
    """If S2.107 calls the line architectural 9 by a DIFFERENT name than S2.105
    does, that is a second dispute, not more evidence for the first."""
    pages = [
        page("s1", "S2.105", "structural", 1),
        page("a", "A3.01", "architectural", 2),
        page("s2", "S2.107", "structural", 3),
    ]
    renamed = {("1" if k == "6" else k): v for k, v in STRUCT_ROWS.items()}  # struct "1" where S2.105 has "6"
    renamed["6"] = 1473.0
    systems = [
        GridSystem("s1", "blue 27pt", {}, STRUCT_ROWS),
        GridSystem("a", "grey 18pt", {}, shifted(ARCH_ROWS, -30)),
        GridSystem("s2", "blue 27pt", {}, shifted(renamed, 12)),
    ]
    findings, _ = grid_mismatches(pages, systems)
    assert len(findings) >= 2


def test_a_lone_dispute_keeps_the_fingerprint_it_always_had():
    # An RFI already accepted from it must not be proposed again under a new id.
    pages = [page("s", "S2.105", "structural", 1), page("a", "A3.01", "architectural", 2)]
    systems = [
        GridSystem("s", "blue 27pt", {}, STRUCT_ROWS),
        GridSystem("a", "grey 18pt", {}, shifted(ARCH_ROWS, -30)),
    ]
    (f,), _ = grid_mismatches(pages, systems)
    # The formula from before merging existed: the axis word and the unordered
    # renamed pairs.
    pairs = sorted("~".join(sorted(line.split(" = "))) for line in f.facts["renamedLines"])
    assert f.fingerprint == rfi_grid.fingerprint(rfi_grid.CHECK_TYPE, "numbered", *pairs)


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


@pytest.mark.parametrize("rotate", [0, 90, 180, 270])
def test_the_evidence_box_is_stored_where_its_labels_are_printed(rotate):
    """Evidence boxes are stored in the page's UNROTATED space, like chunk
    boxes and PDF annotations. Checked independently of the matrix code: the
    words `get_text` clips out of the stored box (get_text is unrotated too)
    must be the renamed labels. A display-space box on a rotated sheet clipped
    the title block instead, and the marked-up RFI clouded that."""
    pdf_page = _sheet(rotate)[0]
    systems = [
        GridSystem("s", s["style"], s["along_x"], s["along_y"], s["bubbles"], tuple(pdf_page.derotation_matrix))
        for s in grid.styled_systems(pdf_page)
    ]
    findings, _ = grid_mismatches([page("s", "S2.105", "structural")], systems)
    box = findings[0].evidence[0]["bbox"]
    clip = fitz.Rect(box["x"], box["y"], box["x"] + box["width"], box["y"] + box["height"])
    words = {w[4] for w in pdf_page.get_text("words", clip=clip)}
    assert words and words <= set(STRUCT_ROWS) | set(ARCH_ROWS), words


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


# --- bubbles on kinked leaders, and lines with two names -------------------------------

import os  # noqa: E402

CLIENT_PDF = Path(os.environ.get("RFI_REVIEW_TEST_PDF") or "/nonexistent")
needs_client_pdf = pytest.mark.skipif(not CLIENT_PDF.exists(), reason="set RFI_REVIEW_TEST_PDF to the S2.105/A3.01 set")


BUBBLE = fitz.Rect(90, 490, 110, 510)  # display space, a 20pt bubble at (100, 500)


def test_a_kinked_leader_moves_the_bubble_onto_its_line():
    # Stub up out of the bubble, a diagonal, then up the grid line at x=118.
    segs = [(100, 490, 100, 480), (100, 480, 118, 462), (118, 462, 118, 400)]
    assert grid.leader_target(BUBBLE, segs) == (118, None)
    # Drawn in the other direction, piece by piece, it is the same leader.
    segs = [(100, 480, 100, 490), (118, 462, 100, 480), (118, 400, 118, 462)]
    assert grid.leader_target(BUBBLE, segs) == (118, None)


def test_a_row_bubble_leader_moves_only_its_y():
    bubble = fitz.Rect(10, 290, 30, 310)
    segs = [(30, 300, 40, 300), (40, 300, 55, 315), (55, 315, 300, 315)]
    assert grid.leader_target(bubble, segs) == (None, 315)


@pytest.mark.parametrize(
    "segs",
    [
        [(100, 490, 100, 300)],  # a straight stub: already on its line
        [(100, 490, 100, 480), (100, 480, 118, 462), (118, 462, 400, 462)],  # turns sideways: not a leader
        [(100, 490, 100, 480), (100, 480, 118, 462), (118, 462, 118, 470)],  # doubles back
        [(100, 490, 100, 480), (100, 480, 160, 420), (160, 420, 160, 300)],  # too far: 3 bubble widths
        [(140, 490, 140, 480), (140, 480, 158, 462), (158, 462, 158, 400)],  # never touches the bubble
    ],
)
def test_anything_but_a_leader_leaves_the_bubble_where_it_is(segs):
    assert grid.leader_target(BUBBLE, segs) == (None, None)


def test_a_line_with_two_names_matches_the_name_both_drawings_use():
    """The client's structural sheets bubble one line "2.3" at one end and
    "2.4" at the other; the architectural sheets call it 2.3 at both. Nearest
    first paired 2.3 with 2.4 and the scan asked "2.3 = 2.4"."""
    arch = {"1": 40.0, "2": 100.0, "2.3": 190.0, "2.7": 255.0, "3": 370.0}
    struct = {"1": 47.0, "2": 107.0, "2.3": 197.0, "2.4": 197.0, "2.7": 262.0, "3": 377.0}
    found = align(arch, struct)
    assert found is not None
    assert [(x, y) for x, y in found.pairs if x != y] == []


@needs_client_pdf
def test_s2105_grid_lines_sit_where_their_leaders_land():
    """S2.105 pushes G.9, H and H.1 sideways on kinked leaders. Read at the
    bubble, G.9 and H were 18pt (2 ft) off their lines and a full scan
    reported "G.9 = H" against the architectural sheets."""
    page = grid.without_markup(fitz.open(CLIENT_PDF)[0])
    (system,) = [s for s in grid.styled_systems(page) if s["style"] == "blue 27pt"]
    assert system["along_x"]["G.9"] == pytest.approx(2109.24, abs=0.5)
    assert system["along_x"]["H"] == pytest.approx(2129.52, abs=0.5)
    # Lines bubbled straight on are untouched.
    assert system["along_x"]["G"] == pytest.approx(1939.56, abs=0.5)


def test_an_offset_just_across_a_bin_edge_still_lines_up():
    """JETRIGHT A1.01 against S2.01, positions as read: every lettered line is
    renamed one step (A1.01's U is S2.01's T) at -27.3pt. That offset sits just
    past the edge of the bin that won the vote, and one median over all three
    bins landed at -22.8, matched two lines and reported nothing (Astra's
    JR-004 on that set)."""
    a = {"U": 214.06, "T": 286.06, "S": 290.57, "R": 470.57, "Q": 650.57, "P": 830.57, "N": 1010.57,
         "M": 1190.57, "L": 1370.57, "K": 1550.57, "J": 1730.57, "H": 1910.57, "G": 2090.57,
         "E": 2270.31, "F": 2270.57, "D": 2293.11, "C": 2346.89, "B": 2535.32, "A": 2694.32}
    b = {"T": 186.66, "S": 258.66, "R": 263.22, "Q": 443.22, "P": 623.22, "N": 803.22, "M": 983.22,
         "L": 1163.22, "K": 1343.22, "J": 1523.22, "H": 1703.22, "G": 1883.22, "F": 2063.22,
         "E": 2243.22, "D": 2247.66, "C": 2319.42, "B": 2507.94, "A": 2666.94}
    found = rfi_grid.align(a, b)
    assert found is not None and found.offset == pytest.approx(-27.35, abs=0.2)
    assert ("U", "T") in found.pairs and ("G", "F") in found.pairs and ("A", "A") in found.pairs


def test_a_consultant_sheet_on_the_architects_grid_is_listed_with_that_naming():
    """JETRIGHT: P1.03 (plumbing) draws the architect's grid, so it joined the
    architectural-structural dispute as evidence — and the merge, which only
    had an architectural and a structural list, raised KeyError: 'plumbing' and
    failed the whole scan."""
    pages = [
        page("s1", "S2.105", "structural", 1),
        page("a", "A3.01", "architectural", 2),
        page("s2", "S2.107", "structural", 3),
        page("p", "P1.03", "plumbing", 4),
    ]
    arch = (shifted(ARCH_COLUMNS, -146), shifted(ARCH_ROWS, -30))
    systems = [
        GridSystem("s1", "blue 27pt", COLUMNS, STRUCT_ROWS),
        GridSystem("a", "grey 18pt", *arch),
        GridSystem("s2", "blue 27pt", shifted(COLUMNS, 12), shifted(dict(STRUCT_ROWS, **{"7": 1600.0}), 12)),
        GridSystem("p", "grey 18pt", shifted(arch[0], 40), shifted(arch[1], 40)),
    ]
    findings, _ = grid_mismatches(pages, systems)
    merged = [f for f in findings if "architectural" in f.subject and "structural" in f.subject]
    assert len(merged) == 1
    q = merged[0].question
    assert q.startswith("The drawings using the architectural grid naming (A3.01 and P1.03)"), q
    assert all(not k.startswith("_") for f in findings for e in f.evidence for k in e)


def test_a_line_bubbled_with_two_names_matches_the_one_the_other_sheet_uses():
    """JETRIGHT's A1.01 bubbles one line both E and F; M1.02 calls it F. Taken
    in position order, E claimed M1.02's F first and the scan said "E = F"."""
    a = {"D": 2100.0, "E": 2270.31, "F": 2270.57, "G": 2450.0, "H": 2630.0, "J": 2810.0}
    b = {"D": 2100.0, "F": 2270.4, "G": 2450.0, "H": 2630.0, "J": 2810.0}
    found = align(a, b)
    assert found is not None and rfi_grid._renamed(found) == []
