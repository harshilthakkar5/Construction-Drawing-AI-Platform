"""What counts as a grid line.

The whole module exists because of one measured mistake: asked by hand for the
footing at grid 7/C, the answer given was F10, measured against the drawing
frame's zone markers rather than the building grid. The right answer is F12.
Zone markers and grid bubbles are indistinguishable in a text dump — a letter
is a letter — so every test here is about the GEOMETRY that separates them.

From Phase A this module has two readers: the eval generator deriving an
answer, and (later) the vision pass deciding what to crop. That is the point —
one definition rather than two that drift — and it is also why these tests
matter more than they did when only the generator used them: a bubble wrongly
found here would be wrong on both sides at once, and the two would agree.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import fitz  # noqa: E402

import grid  # noqa: E402


def bubble(page, x, y, label, radius=18):
    page.draw_circle(fitz.Point(x, y), radius)
    page.insert_text((x - 5, y + 4), label, fontsize=10)


def sheet(rotation=0):
    doc = fitz.open()
    page = doc.new_page(width=42 * 72, height=30 * 72)
    for i, dx in enumerate((0, 130, 260, 390)):
        bubble(page, 300 + dx, 200, str(i + 1))
    for j, dy in enumerate((0, 200, 400)):
        bubble(page, 200, 400 + dy, "BCD"[j])
    if rotation:
        page.set_rotation(rotation)
    return doc, page


class TestWhatIsABubble:
    def test_a_circled_single_word_label_is_one(self):
        doc, page = sheet()
        found = grid.bubbles(page)
        assert sorted(label for label, _, _ in found) == ["1", "2", "3", "4", "B", "C", "D"]
        doc.close()

    def test_a_detail_callout_is_not(self):
        """Same circle, same size — two words inside. "6" over "S-301.0" is a
        reference to another sheet, not a grid line."""
        doc, page = sheet()
        page.draw_circle(fitz.Point(1200, 900), 18)
        page.insert_text((1188, 904), "6", fontsize=8)
        page.insert_text((1188, 912), "S-301.0", fontsize=8)
        assert len(grid.bubbles(page)) == 7
        doc.close()

    def test_a_frame_zone_marker_is_not(self):
        """The trap that put F10 at 7/C. An uncircled letter in the border is
        for finding things on a printed sheet; it is not a grid line, and a
        text dump cannot tell the difference."""
        doc, page = sheet()
        page.insert_text((60, 700), "H", fontsize=10)
        page.insert_text((60, 900), "J", fontsize=10)
        assert [label for label, _, _ in grid.bubbles(page)].count("H") == 0
        assert len(grid.bubbles(page)) == 7
        doc.close()

    def test_a_circle_of_the_wrong_size_is_not(self):
        doc, page = sheet()
        bubble(page, 1500, 900, "9", radius=60)   # too big
        bubble(page, 1700, 900, "8", radius=6)    # too small
        assert len(grid.bubbles(page)) == 7
        doc.close()


class TestAxes:
    def test_bubbles_sharing_a_y_are_the_column_grid(self):
        doc, page = sheet()
        columns, rows = grid.axes(grid.bubbles(page))
        assert sorted(columns) == ["1", "2", "3", "4"]
        assert sorted(rows) == ["B", "C", "D"]
        doc.close()

    def test_an_axis_needs_more_than_a_pair(self):
        """Two points define a line through any two strays; MIN_AXIS is 3
        because three has to be deliberate."""
        doc = fitz.open()
        page = doc.new_page(width=42 * 72, height=30 * 72)
        bubble(page, 300, 200, "1")
        bubble(page, 430, 200, "2")
        assert grid.axes(grid.bubbles(page)) == ({}, {})
        doc.close()

    def test_the_grid_is_read_in_display_space(self):
        """`get_text` and `get_drawings` report UNROTATED coordinates while the
        renderer and the reader both see the rotated page, so a /Rotate 90
        sheet's columns are its unrotated rows. `axes` is deliberately left
        that way — it reports the GEOMETRY — and the two dicts hold different
        coordinates (an x per column, a y per row), so the naming cannot be
        fixed by swapping them. `intersections` does it, correctly."""
        doc, page = sheet(rotation=90)
        columns, rows = grid.axes(grid.bubbles(page))
        assert sorted(columns) == ["B", "C", "D"]
        assert sorted(rows) == ["1", "2", "3", "4"]
        assert grid.transposed(columns, rows)
        doc.close()

    def test_a_rotated_sheet_still_names_its_intersections_the_same_way(self):
        """The Phase B requirement. A crop's label is handed to the model as
        the thing it does NOT have to work out, so "B/2" for an intersection
        the drawing calls 2/B would be our own error — agreed on by both
        readers of this module, and invisible to every check."""
        for rotation in (0, 90, 180, 270):
            doc, page = sheet(rotation=rotation)
            found = grid.intersections(*grid.axes(grid.bubbles(page)))
            assert len(found) == 12, f"rotation {rotation}"
            # Numbers are column lines at every rotation; letters are rows.
            assert sorted(c for c, _, _, _ in found) == sorted("1234" * 3)
            assert sorted(r for _, r, _, _ in found) == sorted("BCD" * 4)
            # Each point lands on the page as displayed, which is what
            # get_pixmap(clip=) will be handed.
            assert all((x, y) in page.rect for _, _, x, y in found)
            doc.close()

    def test_the_convention_only_breaks_a_tie_it_can_read(self):
        """It fires only when one axis is entirely numeric and the other
        entirely alphabetic. Anything else and the geometry stands: a rule that
        guessed on an ambiguous sheet would be worse than the swap it fixes."""
        assert grid.transposed({"A": 1.0}, {"2": 2.0})
        assert not grid.transposed({"2": 1.0}, {"B": 2.0})
        assert not grid.transposed({"A": 1.0, "3": 2.0}, {"2": 3.0})
        assert not grid.transposed({}, {"2": 1.0})

    def test_a_transposed_sheet_assembles_the_point_the_other_way_round(self):
        """columns hold an x and rows a y, so a transposed grid takes its x
        from the lettered axis and its y from the numbered one. Swapping the
        dicts instead reflects the whole grid about its diagonal."""
        columns = {"B": 100.0, "C": 200.0}  # lettered, so these are x values
        rows = {"1": 700.0, "2": 800.0}  # numbered, so these are y values
        assert grid.intersections(columns, rows) == [
            ("1", "B", 100.0, 700.0),
            ("1", "C", 200.0, 700.0),
            ("2", "B", 100.0, 800.0),
            ("2", "C", 200.0, 800.0),
        ]

    def test_every_rotation_finds_the_same_seven_bubbles(self):
        for rotation in (0, 90, 180, 270):
            doc, page = sheet(rotation=rotation)
            assert len(grid.bubbles(page)) == 7, f"rotation {rotation}"
            doc.close()
