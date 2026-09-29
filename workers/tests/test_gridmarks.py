"""Which mark is printed at which grid crossing — the geometric reader.

Built after the vision crop pass placed the right mark at about 7 of 19 primary
crossings on the client's S2.105, where every mark and every grid bubble has an
exact position in the PDF. The failures this reader exists to avoid are the
ones that sheet showed: a size taken from the NEIGHBOUR's label, a mark printed
between two crossings handed to one of them, and a mark drawn twice counted
twice.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import fitz  # noqa: E402
import pytest  # noqa: E402

import gridmarks  # noqa: E402

COLUMNS = {"1": 300, "2": 500, "3": 700, "3.5": 760, "4": 900}  # x of each numbered line
ROWS = {"A": 300, "B": 500, "C": 700}  # y of each lettered line (an axis needs 3)


def sheet(rotation=0, marks=True):
    doc = fitz.open()
    page = doc.new_page(width=36 * 72, height=24 * 72)
    # 27pt bubbles — the client's size, which the old 30-45pt window missed.
    for label, x in COLUMNS.items():
        page.draw_circle(fitz.Point(x, 150), 13.5)
        page.insert_text((x - 3, 153), label, fontsize=8)
    for label, y in ROWS.items():
        page.draw_circle(fitz.Point(150, y), 13.5)
        page.insert_text((146, y + 3), label, fontsize=8)
    if marks:
        # At 1/A: a mark with its size printed directly under it...
        page.insert_text((310, 290), "C-6", fontsize=8)
        page.insert_text((310, 300), "(14 x 30)", fontsize=6)
        # ...and the same mark drawn a second time, a little higher, where the
        # size is no longer directly under it — the real sheet's pattern.
        page.insert_text((310, 280), "C-6", fontsize=8)
        # At 1/B: a mark with NO size under it. The only size line in its
        # column is C-6's, 200pt above — which it must not borrow.
        page.insert_text((310, 490), "C-4", fontsize=8)
        # At 2/B: two marks, the second with its own different size. A size
        # line sits just under C-6 at 1/A too, so a reader that took "the next
        # line" rather than "the line under THIS mark" would mix them up.
        page.insert_text((510, 490), "C-13", fontsize=8)
        page.insert_text((510, 500), "(22 x 22)", fontsize=6)
        page.insert_text((470, 520), "SR-8", fontsize=8)
        # Printed exactly between 3/A and the secondary 3.5/A — the real
        # sheet's case, where secondary lines sit a fraction of a bay apart.
        page.insert_text((725, 296), "C-9", fontsize=8)
        # A schedule row far from every crossing: not a grid mark at all.
        page.insert_text((1800, 1400), "C-99", fontsize=8)
    if rotation:
        page.set_rotation(rotation)
    return doc, page


def test_each_mark_is_placed_at_the_crossing_it_is_printed_at():
    doc, page = sheet()
    marks = gridmarks.read(page)
    assert marks.at == {
        "1/A": ["C-6 (14 x 30)"],
        "1/B": ["C-4"],
        "2/B": ["C-13 (22 x 22)", "SR-8"],
    }
    doc.close()


def test_a_mark_drawn_twice_is_one_mark_and_keeps_its_size():
    doc, page = sheet()
    assert gridmarks.read(page).at["1/A"] == ["C-6 (14 x 30)"]
    doc.close()


def test_a_size_is_the_one_printed_under_the_mark_not_the_next_line():
    """The first version read "the next line of the block", and the client's
    sheet numbers lines in no visual order: C-13 came out with C-6's size."""
    doc, page = sheet()
    at = gridmarks.read(page).at
    assert at["2/B"][0] == "C-13 (22 x 22)"
    assert "SR-8" in at["2/B"], "a mark with no size under it gets none"
    assert at["1/B"] == ["C-4"], "a size in the same column but far above is not this mark's"
    doc.close()


def test_a_mark_between_two_crossings_is_assigned_to_neither():
    doc, page = sheet()
    marks = gridmarks.read(page)
    assert ("C-9", "3/A", "3.5/A") in marks.between
    assert not any("C-9" in m for ms in marks.at.values() for m in ms)
    doc.close()


def test_a_mark_far_from_every_crossing_is_not_a_grid_mark():
    doc, page = sheet()
    marks = gridmarks.read(page)
    everything = [m for ms in marks.at.values() for m in ms] + [b[0] for b in marks.between]
    assert "C-99" not in everything
    doc.close()


def test_a_grid_with_no_marks_stores_nothing_and_is_not_even_scanned(monkeypatch):
    """The grid read is the costly part (2.3s on an architectural sheet), and
    a page with no mark-shaped text can produce nothing, so it is skipped."""
    doc, page = sheet(marks=False)
    monkeypatch.setattr(gridmarks.grid, "page_grid", lambda p: pytest.fail("scanned"))
    assert gridmarks.read(page) is None
    assert gridmarks.chunks_for(page) == []
    doc.close()


def test_a_page_with_no_grid_gives_nothing():
    doc = fitz.open()
    page = doc.new_page(width=36 * 72, height=24 * 72)
    page.insert_text((300, 300), "C-6", fontsize=8)
    assert gridmarks.read(page) is None
    assert gridmarks.chunks_for(page) == []
    doc.close()


@pytest.mark.parametrize("rotation", [90, 180, 270])
def test_a_rotated_sheet_places_the_same_marks(rotation):
    """Display coordinates for the grid, the page's own frame for text: the
    trap region.py was written for. Turning the page changes nothing drawn."""
    doc, page = sheet()
    upright = gridmarks.read(page)
    doc.close()
    doc, page = sheet(rotation)
    turned = gridmarks.read(page)
    assert sorted(m for ms in turned.at.values() for m in ms) == sorted(
        m for ms in upright.at.values() for m in ms
    )
    doc.close()


def test_the_reading_says_what_it_is_and_which_way_the_lines_run():
    doc, page = sheet()
    text = gridmarks.describe(gridmarks.read(page))
    assert "not a vision model" in text
    assert "At 1/A: C-6 (14 x 30)." in text
    assert "At 2/B: C-13 (22 x 22), SR-8." in text
    assert "C-9 is printed between 3/A and 3.5/A" in text
    assert "Drawn vertically on the sheet, left to right: 1, 2, 3, 3.5, 4 (the column lines)." in text
    assert "Drawn horizontally on the sheet, top to bottom: A, B, C (the row lines)." in text
    doc.close()


def test_the_chunk_is_its_own_kind_and_names_its_reader():
    doc, page = sheet()
    chunks = gridmarks.chunks_for(page)
    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.kind == "gridmarks"
    assert chunk.source_model == gridmarks.SOURCE
    # Highlights the grid, not the whole sheet.
    assert chunk.bbox == {"x": 300, "y": 300, "width": 600, "height": 400}
    doc.close()


def test_every_piece_of_a_long_reading_carries_the_explanation(monkeypatch):
    """A piece that is only "At 4.3/B1.5: C-9." lines reads as an
    unexplained list, and the chat would not know it was measured."""
    doc, page = sheet()
    monkeypatch.setattr(gridmarks.chunker, "MAX_TOKENS", 60)
    chunks = gridmarks.chunks_for(page)
    assert len(chunks) > 1
    assert all(c.text.startswith("Grid marks, read from where") for c in chunks)
    doc.close()

