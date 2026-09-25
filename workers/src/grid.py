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


# --- Every grid on a sheet, by drawing style ----------------------------------
#
# `bubbles` answers "where is THIS sheet's grid" for the vision pass and the
# eval, and its size window was set on one ARCH E1 sheet. The RFI grid check
# asks a different question — "do two grids disagree about a line's name" —
# and needs two things `bubbles` deliberately throws away.
#
# Size. The 36x24 structural sheet that motivated this draws its bubbles at
# 27pt and its architectural companion at 18pt. Both fall under BUBBLE_MIN_PT,
# so `bubbles` sees no grid at all on that set. The window here is wide; what
# keeps it honest is the axis rule (three bubbles on one line) and the
# comparison that follows.
#
# Style. A sheet can carry two grids — its own, and another discipline's drawn
# in as a background — on the SAME lines with DIFFERENT labels. Pooled into one
# axis, `axes` keys by label and cannot tell them apart. Grouped by (size,
# colour) first, each becomes its own system. Callers strip annotations first
# (`without_markup`): a grid pasted on as markup is not one the drawing has.

STYLED_MIN_PT = 12.0
STYLED_MAX_PT = 48.0
# Words on a sheet shaped like a grid label. A page with fewer cannot hold a
# grid on two axes, and skipping its vector scan is most of the cost saved on
# a set that is mostly details and schedules.
MIN_GRID_WORDS = 6
SIZE_TOL_PT = 2.0
# Wider than GRID_LABEL by one shape: a secondary line between two lettered
# ones ("C.1", "F.7", "B1.5"). `bubbles` refuses those on purpose — its callers
# ask about intersections of primary lines — but a secondary line sitting
# where the other drawing puts a primary one is exactly a naming mismatch.
STYLED_LABEL = re.compile(r"\d+(?:\.\d+)?|[A-Z]{1,2}(?:\d*\.\d+)?")


def _colour_name(rgb) -> str:
    if not rgb:
        return "black"
    r, g, b = (float(v) for v in rgb[:3])
    if max(r, g, b) - min(r, g, b) < 0.12:
        if max(r, g, b) < 0.25:
            return "black"
        return "grey"
    if b > r and b > g:
        return "blue"
    if r > g and r > b:
        return "red" if g < 0.5 else "orange"
    if g > r and g > b:
        return "green"
    return "coloured"


def without_markup(page: fitz.Page) -> fitz.Page:
    """The page with every annotation removed — IN MEMORY, on the caller's copy.

    PyMuPDF reads annotation appearances as though they were drawn on the
    sheet: `get_text` returns a FreeText note's words and `get_cdrawings` a
    stamp's circles. That is how a client's RFI markup became part of a grid:
    the structural sheet it came back on carried a pasted "Snapshot" stamp of
    the ARCHITECTURAL grid bubbles beside the structural ones, and the check
    compared the two as though the engineer had drawn both. A finding has to
    come from the drawings as issued, not from someone's markup of them —
    otherwise a marked-up set and a clean one give different answers, and the
    marked one answers questions the reviewer has already asked.

    Only for a document opened from a temporary download: this mutates the
    in-memory document, and saving it would strip the file.
    """
    for annot in list(page.annots()):
        page.delete_annot(annot)
    return page


def styled_systems(page: fitz.Page) -> list[dict]:
    """Every grid on the page, one per bubble STYLE, in display coordinates.

    Each is {"style": "blue 27pt", "along_x": {label: x}, "along_y": {label: y},
    "bubbles": {label: [[x0, y0, x1, y1], ...]}}. `along_x` holds lines whose bubbles
    share a y — their position is an x — and `along_y` the other way round.
    Deliberately NOT called columns and rows: which is which is a convention
    (`transposed`), and comparing two grids only needs the geometry.
    """
    words = page.get_text("words")
    if sum(1 for w in words if STYLED_LABEL.fullmatch(w[4])) < MIN_GRID_WORDS:
        return []
    to_display = ~page.derotation_matrix
    by_colour: dict[str, list[tuple[str, float, float, fitz.Rect, float]]] = {}
    for drawing in page.get_cdrawings():
        rect = fitz.Rect(drawing["rect"])
        if rect.width <= 0 or rect.height <= 0:
            continue
        if not STYLED_MIN_PT < rect.width < STYLED_MAX_PT:
            continue
        if not BUBBLE_ASPECT[0] < rect.width / rect.height < BUBBLE_ASPECT[1]:
            continue
        if not any(item[0] == "c" for item in drawing["items"]):
            continue
        inside = [
            w[4]
            for w in words
            if rect.x0 <= (w[0] + w[2]) / 2 <= rect.x1 and rect.y0 <= (w[1] + w[3]) / 2 <= rect.y1
        ]
        if len(inside) != 1 or not STYLED_LABEL.fullmatch(inside[0]):
            continue
        colour = _colour_name(drawing.get("color") or drawing.get("fill"))
        shown = rect * to_display
        by_colour.setdefault(colour, []).append((inside[0], *centre(shown), shown, rect.width))

    # Sizes are CLUSTERED per colour, never bucketed: one drafter's bubble is
    # one size give or take the stroke (26.9 beside 27.0), and a fixed bucket
    # edge between those splits one grid into two systems. Two grids a drafter
    # meant to tell apart differ by far more than SIZE_TOL_PT.
    groups: dict[str, list[tuple[str, float, float, fitz.Rect]]] = {}
    for colour, found in by_colour.items():
        found.sort(key=lambda item: item[4])
        clusters: list[list] = []
        for item in found:
            if clusters and item[4] - clusters[-1][-1][4] <= SIZE_TOL_PT:
                clusters[-1].append(item)
            else:
                clusters.append([item])
        for cluster in clusters:
            size = sorted(item[4] for item in cluster)[len(cluster) // 2]
            groups[f"{colour} {round(size)}pt"] = [item[:4] for item in cluster]

    systems = []
    for style, found in sorted(groups.items()):
        along_x, along_y = axes([(label, x, y) for label, x, y, _ in found])
        if not along_x and not along_y:
            continue
        # Every bubble of a label, not their union: a grid line is bubbled at
        # BOTH ends, and one box around both is a highlight the width of the
        # sheet. The caller picks one end.
        boxes: dict[str, list[list[float]]] = {}
        for label, _, _, shown in found:
            if label in along_x or label in along_y:
                boxes.setdefault(label, []).append([shown.x0, shown.y0, shown.x1, shown.y1])
        systems.append({"style": style, "along_x": along_x, "along_y": along_y, "bubbles": boxes})
    return systems
