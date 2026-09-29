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


class TestThePageGrid:
    """`page_grid` — the ONE reading both the vision pass and the eval use.

    It exists because `bubbles` was blind on the sets this product is actually
    given: a client's 36x24 structural sheet draws its bubbles at 27pt, under
    the 30pt floor, so crop mode found no grid there and quietly ran the
    whole-sheet pass on every page.
    """

    @staticmethod
    def _grid(page, columns, rows, radius, colour=(0, 0, 0), x0=300, y0=400):
        for i, label in enumerate(columns):
            x = x0 + 130 * i
            page.draw_circle(fitz.Point(x, 200), radius, color=colour)
            page.insert_text((x - 5, 204), label, fontsize=8)
        for j, label in enumerate(rows):
            y = y0 + 150 * j
            page.draw_circle(fitz.Point(200, y), radius, color=colour)
            page.insert_text((195, y + 4), label, fontsize=8)

    def test_a_27pt_bubble_grid_is_found(self):
        doc = fitz.open()
        page = doc.new_page(width=36 * 72, height=24 * 72)
        self._grid(page, ["1", "2", "3", "4"], ["A", "B", "C"], radius=13.5)
        assert grid.axes(grid.bubbles(page)) == ({}, {}), "the old window really was blind"
        columns, rows = grid.page_grid(page)
        assert sorted(columns) == ["1", "2", "3", "4"] and sorted(rows) == ["A", "B", "C"]
        doc.close()

    def test_secondary_lines_are_read(self):
        doc = fitz.open()
        page = doc.new_page(width=36 * 72, height=24 * 72)
        self._grid(page, ["1", "1.5", "2", "3"], ["A", "C.1", "B1.6"], radius=13.5)
        columns, rows = grid.page_grid(page)
        assert "1.5" in columns and {"C.1", "B1.6"} <= set(rows)
        assert grid.is_secondary("C.1") and grid.is_secondary("4.6")
        assert not grid.is_secondary("C") and not grid.is_secondary("12")
        doc.close()

    def test_two_grid_styles_are_never_pooled(self):
        """A structural grid over an architectural background, both labelling
        a line "2" at different places. Pooled, one position silently wins."""
        doc = fitz.open()
        page = doc.new_page(width=36 * 72, height=24 * 72)
        self._grid(page, ["1", "2", "3", "4"], ["A", "B", "C"], radius=13.5, colour=(0, 0, 1))
        self._grid(page, ["1", "2", "3"], ["A", "B", "C"], radius=9, x0=365, y0=475)
        columns, rows = grid.page_grid(page)
        assert sorted(columns) == ["1", "2", "3", "4"]
        assert all(abs(columns[k] - x) < 1 for k, x in {"1": 300, "2": 430, "3": 560, "4": 690}.items())
        assert abs(rows["B"] - 550) < 1, "the larger grid, whole — not a mix of both"
        doc.close()

    def test_an_ordinary_arch_e1_grid_reads_as_it_always_did(self):
        doc, page = sheet()
        assert grid.page_grid(page) == grid.axes(grid.bubbles(page))
        doc.close()


class TestOneLineTwoNames:
    """The client's S2.105 bubbles one horizontal line "2.3" at its left end
    and "2.4" at its right end. Read as two lines, every crossing on it existed
    twice at one point and every mark there came out "between" its own names."""

    PAIRS = [
        ("1", "C", 300.0, 700.0),
        ("1", "D", 300.0, 700.0),
        ("4", "C", 900.0, 700.0),
        ("4", "D", 900.0, 700.0),
        ("1", "A", 300.0, 300.0),
    ]
    CENTRES = {"C": [(150.0, 700.0)], "D": [(1100.0, 700.0)], "A": [(150.0, 300.0)]}

    def test_labels_at_one_position_are_one_line(self):
        assert grid.shared_lines({"A": 300.0, "C": 700.0, "D": 700.6, "E": 900.0}) == [["C", "D"]]

    def test_labels_a_real_gap_apart_are_two_lines(self):
        # 3.3 and 3.4 on S2.105 are 7pt apart: close, but two lines.
        assert grid.shared_lines({"3.3": 837.0, "3.4": 844.0}) == []

    def test_each_crossing_takes_the_name_printed_nearer_to_it(self):
        named = grid.one_name_per_crossing(self.PAIRS, [["C", "D"]], self.CENTRES)
        assert named == [
            ("1", "C", 300.0, 700.0),
            ("4", "D", 900.0, 700.0),
            ("1", "A", 300.0, 300.0),
        ]

    def test_a_grid_with_no_shared_line_is_untouched(self):
        assert grid.one_name_per_crossing(self.PAIRS, [], self.CENTRES) == self.PAIRS
