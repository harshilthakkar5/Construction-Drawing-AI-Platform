"""Where a sheet's grid lines are, read from the PDF's own geometry.

This is not a guess and not a model's opinion. A grid bubble is a drawn circle
with exactly one grid-shaped word inside it, and its centre is a coordinate the
drawing itself carries. Everything that needs to talk about "grid 7/C" — the
eval generator deriving an answer, the vision pass deciding what to crop —
should be looking at the same bubbles, which is why this lives here rather than
in either caller.

It moved OUT of benchmarks/drawing_truth.py, and the move has a cost worth
stating plainly. While the generator was the only reader, the eval could
falsify anything the pipeline believed about the grid. Once the vision pass
reads the same module, a misdetected grid is wrong in BOTH places at once: the
crop labelled 4/B and the expected answer for 4/B would agree with each other
and disagree with the drawing, and no run would notice. The alternative —
detecting the grid twice, once per side — is worse, because this repo has
already paid for duplicated rules that drifted (the identifier regex, the
combined-numbering rule, the storage switch). One definition plus a stated
blind spot beats two definitions and a silent divergence. The blind spot is
covered by `drawing_truth.py --explain`, read by a person once per sheet.

The trap this encodes is the reason the whole file exists. Asked by hand for
the footing at grid 7/C, the answer given was F10 — measured against the
drawing frame's zone markers, the evenly spaced letters and numbers printed in
the border. The right answer is F12. Zone markers and grid bubbles are
indistinguishable in a text dump; only the geometry separates them, and only
the circle does it reliably.
"""

from __future__ import annotations

import re

import fitz

# A grid bubble's circle, in points. Detail callouts share these dimensions —
# they are told apart by how many words sit inside, not by size.
BUBBLE_MIN_PT = 30.0
BUBBLE_MAX_PT = 45.0
BUBBLE_ASPECT = (0.85, 1.18)

# A grid label: "7", "4.6", "11.3", "B", "DD". A detail callout's second token
# ("S-301.0") fails this, and so does anything from the drawing body.
GRID_LABEL = re.compile(r"\d+(?:\.\d+)?|[A-Z]{1,2}")

# Bubbles are on an axis if their cross-coordinate agrees within this. Drawn
# grid lines are exact; the tolerance absorbs only the bubble's own centring.
AXIS_TOL_PT = 6.0

# An axis needs this many bubbles. Two points define a line through any pair of
# strays; three is the smallest number that has to be deliberate.
MIN_AXIS = 3


def centre(rect: fitz.Rect) -> tuple[float, float]:
    return ((rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2)


def bubbles(page: fitz.Page) -> list[tuple[str, float, float]]:
    """Circled grid labels, in the page's DISPLAYED coordinate space.

    Display space because that is where the user and the renderer both live;
    `get_text` and `get_drawings` report unrotated coordinates, so everything is
    mapped once, here, rather than at each comparison.
    """
    to_display = ~page.derotation_matrix
    words = page.get_text("words")
    out: list[tuple[str, float, float]] = []
    for drawing in page.get_drawings():
        rect = drawing["rect"]
        if rect.width <= 0 or rect.height <= 0:
            continue
        if not BUBBLE_MIN_PT < rect.width < BUBBLE_MAX_PT:
            continue
        if not BUBBLE_ASPECT[0] < rect.width / rect.height < BUBBLE_ASPECT[1]:
            continue
        if not any(item[0] == "c" for item in drawing["items"]):
            continue
        inside = [
            w
            for x0, y0, x1, y1, w, *_ in words
            if rect.x0 <= (x0 + x1) / 2 <= rect.x1 and rect.y0 <= (y0 + y1) / 2 <= rect.y1
        ]
        # Exactly one word: two means a detail callout ("6" + "S-301.0"), which
        # is a reference to another sheet and not a grid line at all.
        if len(inside) == 1 and GRID_LABEL.fullmatch(inside[0]):
            out.append((inside[0], *centre(rect * to_display)))
    return out


def axes(found: list[tuple[str, float, float]]) -> tuple[dict, dict]:
    """Split bubbles into the column grid (a shared y) and the row grid (a
    shared x). A sheet's angled wing has bubbles on neither and is skipped."""

    def cluster(index: int) -> list[list[tuple[str, float, float]]]:
        groups: list[list[tuple[str, float, float]]] = []
        for bubble in sorted(found, key=lambda b: b[index + 1]):
            value = bubble[index + 1]
            if groups and abs(groups[-1][-1][index + 1] - value) <= AXIS_TOL_PT:
                groups[-1].append(bubble)
            else:
                groups.append([bubble])
        return [g for g in groups if len(g) >= MIN_AXIS]

    # Columns share a y (index 1); rows share an x (index 0).
    col_groups = cluster(1)
    row_groups = cluster(0)
    columns = {b[0]: b[1] for g in col_groups for b in g}
    rows = {b[0]: b[2] for g in row_groups for b in g}
    return columns, rows


def transposed(columns: dict[str, float], rows: dict[str, float]) -> bool:
    """Whether the geometric axes carry each other's names.

    `bubbles` reports DISPLAY coordinates, so on a /Rotate 90 sheet the grid
    lines labelled 1, 2, 3 share a display x rather than a display y: geometry
    files the numbered axis as the rows and the lettered axis as the columns.
    The labels are never wrong, only the two axis NAMES swap, which is why a
    generated case survived it — it asked about "column line B" and derived the
    answer at the same point, self-consistently.

    It stops being survivable in Phase B. A crop's label is handed to the model
    as the one thing it does NOT have to work out, so "B/2" for an intersection
    the drawing calls 2/B is a wrong answer supplied by us, agreed on by both
    readers of this module at once, and invisible to every check — precisely
    the failure the docstring above warns that sharing makes possible.

    Geometry cannot break this tie, so the CONVENTION does: column lines are
    numbered, row lines are lettered. It fires only when that is unambiguous —
    every label on one axis numeric and every label on the other alphabetic —
    and otherwise nothing is reordered. A sheet that letters its columns would
    be read backwards; a rotated sheet is the far commoner case (it is the
    whole reason `region.py` exists), and `--crops` prints the labels for a
    person to check once per sheet.
    """
    if not columns or not rows:
        return False
    return all(label[:1].isalpha() for label in columns) and all(
        label[:1].isdigit() for label in rows
    )


def intersections(
    columns: dict[str, float], rows: dict[str, float]
) -> list[tuple[str, str, float, float]]:
    """Every (column, row) crossing, as (column label, row label, x, y).

    The one place that decides what "4/B" MEANS in points. Both readers ask
    here: the generator turns it into an expected answer, the vision pass turns
    it into a crop. Written twice they could disagree about the same name, and
    the disagreement would be invisible — the crop labelled 4/B and the truth
    for 4/B would each be internally consistent.

    Sorted by label so a dump is diffable between runs.

    `columns` holds an x per label and `rows` a y — DIFFERENT coordinates, which
    is why a sheet whose axes are transposed (see `transposed`) cannot be fixed
    by swapping the two dicts. There the numbered lines each sit at a constant
    y and the lettered ones at a constant x, so the point is assembled the
    other way round. Getting this wrong does not mislabel an intersection, it
    reflects the whole grid about its diagonal.
    """
    if transposed(columns, rows):
        return [
            (col, row, rx, cy)
            for col, cy in sorted(rows.items())
            for row, rx in sorted(columns.items())
        ]
    return [
        (col, row, cx, cy)
        for col, cx in sorted(columns.items())
        for row, cy in sorted(rows.items())
    ]


def spacing(values: list[float]) -> float:
    """The typical gap between adjacent grid lines on one axis — the "bay".

    MEDIAN, not minimum. A bay is the natural unit for sizing a crop, and the
    tempting definition is the smallest gap, which is what `drawing_eval.mjs`
    uses to annotate drift. It is wrong here: this sheet's columns are 130 to
    218pt apart, and sizing every crop off the 130 would cut the furthest
    footing label (83.7pt from its intersection) out of the crop that is
    supposed to contain it. The minimum is the right unit for "is this label
    one bay away"; the median is the right unit for "how much drawing belongs
    to one intersection".

    0.0 when an axis has fewer than two lines, which is a sheet this module
    should not be cropping at all.
    """
    ordered = sorted(values)
    gaps = [b - a for a, b in zip(ordered, ordered[1:])]
    if not gaps:
        return 0.0
    gaps.sort()
    middle = len(gaps) // 2
    return gaps[middle] if len(gaps) % 2 else (gaps[middle - 1] + gaps[middle]) / 2
