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

import math
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


# --- Leaders: a bubble printed away from its line ---------------------------

# A leader endpoint touches the bubble within this of its circle, and two
# leader pieces join within LEADER_JOIN_PT.
LEADER_TOUCH_PT = 2.0
LEADER_JOIN_PT = 0.75
# A leader moves a bubble at most this many bubble diameters off its line.
LEADER_MAX_DIAMETERS = 2.0


def _segments(page: fitz.Page, to_display) -> list[tuple[float, float, float, float]]:
    """Every straight line piece on the page, in display space."""
    out = []
    for drawing in page.get_cdrawings():
        for item in drawing["items"]:
            if item[0] != "l":
                continue
            a, b = fitz.Point(item[1]) * to_display, fitz.Point(item[2]) * to_display
            out.append((a.x, a.y, b.x, b.y))
    return out


def leader_target(bubble: fitz.Rect, segments: list[tuple[float, float, float, float]]) -> tuple[float | None, float | None]:
    """Where a bubble's KINKED leader lands: (x of the line it runs to, or None;
    y of the line it runs to, or None). Pure, on display-space segments.

    Only the drafter's leader SHAPE counts: a straight stub out of the bubble,
    one diagonal, then a piece PARALLEL to the stub running on toward the
    drawing. Anything looser walks into whatever line happens to touch a
    crowded bubble — the first version snapped half of A3.01's bubbles onto
    the sheet border and hid the client's real RFI 002. The bubble then moves
    only ACROSS the stub (a vertical stub moves x, never y), by at most
    LEADER_MAX_DIAMETERS bubble widths.
    """
    cx, cy = (bubble.x0 + bubble.x1) / 2, (bubble.y0 + bubble.y1) / 2
    radius = bubble.width / 2

    def dist(pt) -> float:
        return ((pt[0] - cx) ** 2 + (pt[1] - cy) ** 2) ** 0.5

    def vertical(t) -> bool:
        return abs(t[2] - t[0]) <= 0.5 and abs(t[3] - t[1]) > 1

    def horizontal(t) -> bool:
        return abs(t[3] - t[1]) <= 0.5 and abs(t[2] - t[0]) > 1

    def joined(here, t):
        """The far end of `t` when one of its ends is at `here`, else None."""
        if abs(t[0] - here[0]) + abs(t[1] - here[1]) <= LEADER_JOIN_PT:
            return (t[2], t[3])
        if abs(t[2] - here[0]) + abs(t[3] - here[1]) <= LEADER_JOIN_PT:
            return (t[0], t[1])
        return None

    for stub in segments:
        if not (vertical(stub) or horizontal(stub)):
            continue
        for start, end in (((stub[0], stub[1]), (stub[2], stub[3])), ((stub[2], stub[3]), (stub[0], stub[1]))):
            if abs(dist(start) - radius) > LEADER_TOUCH_PT or dist(end) <= dist(start) + 1:
                continue
            diagonals = [(t, joined(end, t)) for t in segments if t is not stub]
            for diag, bend in diagonals:
                if bend is None or vertical(diag) or horizontal(diag):
                    continue
                for last in segments:
                    if last is stub or last is diag:
                        continue
                    tip = joined(bend, last)
                    if tip is None:
                        continue
                    if vertical(stub) and vertical(last) and (tip[1] - bend[1]) * (end[1] - start[1]) > 0:
                        x = last[0]
                        if abs(x - cx) <= LEADER_MAX_DIAMETERS * bubble.width:
                            return x, None
                    if horizontal(stub) and horizontal(last) and (tip[0] - bend[0]) * (end[0] - start[0]) > 0:
                        y = last[1]
                        if abs(y - cy) <= LEADER_MAX_DIAMETERS * bubble.width:
                            return None, y
    return None, None


def _line_at(label: str, printed: float, found, on_line, index: int) -> float:
    """The line position for the bubble `axes` placed at `printed`."""
    for other, x, y, _ in found:
        if other == label and (x, y)[index] == printed:
            return on_line.get((label, x, y), (x, y))[index]
    return printed


def _on_line(shown: fitz.Rect, x: float, y: float, segments) -> tuple[float, float]:
    lx, ly = leader_target(shown, segments)
    return (x if lx is None else lx), (y if ly is None else ly)


# A bubble drawn as a RING OF SHORT STRAIGHT PIECES rather than a curve. A
# client set (a hangar, 103 sheets) draws every grid bubble as 24 separate
# 3.5pt line segments about 13pt from the label, over a white square mask —
# no "c" item anywhere — so styled_systems found a grid on 6 of 103 sheets and
# the full scan could line up nothing. A ring is recognised only around ONE
# grid label, at one radius within RING_RADIUS_TOL, covering at least
# RING_MIN_BINS of 12 angular sectors: a hatch or a dimension tick does not
# surround a lone letter evenly.
RING_SEGMENT_MAX_PT = 8.0
RING_MIN_SEGMENTS = 12
RING_MIN_BINS = 10
RING_RADIUS_TOL = 2.5


def _short_segments(page: fitz.Page) -> list[tuple[float, float, str, float]]:
    """(mid x, mid y, colour, length) of every short straight piece on the
    page, in unrotated space — what a ring bubble is made of."""
    out = []
    for drawing in page.get_cdrawings():
        colour = _colour_name(drawing.get("color") or drawing.get("fill"))
        for item in drawing["items"]:
            if item[0] != "l":
                continue
            (ax, ay), (bx, by) = item[1], item[2]
            length = math.hypot(bx - ax, by - ay)
            if 0.3 < length < RING_SEGMENT_MAX_PT:
                out.append(((ax + bx) / 2, (ay + by) / 2, colour, length))
    return out


def ring_around(cx: float, cy: float, mids: list[tuple]) -> tuple[float, float, float, str] | None:
    """(centre x, centre y, radius, colour) of a ring of short pieces around
    a label at (cx, cy), or None. Pure: `mids` are (x, y, colour, length).

    A ring is a drawn bubble only when it is CLEAN: one radius (every piece
    within RING_RADIUS_TOL of it, measured from the ring's own centre), pieces
    of one length, the label at its centre, and nothing drawn inside it. The
    first version asked only for pieces at one distance all the way round, and
    on an electrical plan the light fixtures, switches and wiring around room
    tags passed at radii of 1 to 20pt."""
    near = sorted(
        (math.hypot(m[0] - cx, m[1] - cy), m) for m in mids
        if STYLED_MIN_PT / 2 <= math.hypot(m[0] - cx, m[1] - cy) <= STYLED_MAX_PT / 2
    )
    best = None
    for i in range(len(near)):
        group = [m for d, m in near[i:] if d - near[i][0] <= 2 * RING_RADIUS_TOL]
        if len(group) < RING_MIN_SEGMENTS:
            continue
        ox = sum(m[0] for m in group) / len(group)
        oy = sum(m[1] for m in group) / len(group)
        dists = [math.hypot(m[0] - ox, m[1] - oy) for m in group]
        radius = sum(dists) / len(dists)
        if not STYLED_MIN_PT / 2 <= radius <= STYLED_MAX_PT / 2:
            continue
        if max(abs(d - radius) for d in dists) > RING_RADIUS_TOL:
            continue
        if math.hypot(ox - cx, oy - cy) > max(3.0, 0.4 * radius):
            continue
        lengths = [m[3] for m in group]
        if max(lengths) > 1.6 * min(lengths):
            continue
        bins = {int(((math.degrees(math.atan2(m[1] - oy, m[0] - ox)) + 360) % 360) // 30) for m in group}
        if len(bins) < RING_MIN_BINS:
            continue
        if sum(1 for m in mids if math.hypot(m[0] - ox, m[1] - oy) < 0.6 * radius) > 2:
            continue  # a bubble is empty inside but for its label, which is text
        if best is None or len(group) > best[0]:
            colours = [m[2] for m in group]
            best = (len(group), ox, oy, radius, max(set(colours), key=colours.count))
    return None if best is None else best[1:]


def _ring_bubbles(page: fitz.Page, words) -> list[tuple[str, fitz.Rect, str]]:
    """(label, unrotated rect, colour) for each grid label inside a segment ring."""
    labels = [w for w in words if STYLED_LABEL.fullmatch(w[4])]
    if not labels:
        return []
    cell = STYLED_MAX_PT
    buckets: dict[tuple[int, int], list] = {}
    for mid in _short_segments(page):
        buckets.setdefault((int(mid[0] // cell), int(mid[1] // cell)), []).append(mid)
    if not buckets:
        return []
    out = []
    for w in labels:
        cx, cy = (w[0] + w[2]) / 2, (w[1] + w[3]) / 2
        gx, gy = int(cx // cell), int(cy // cell)
        mids = [m for dx in (-1, 0, 1) for dy in (-1, 0, 1) for m in buckets.get((gx + dx, gy + dy), ())]
        ring = ring_around(cx, cy, mids)
        if ring is None:
            continue
        ox, oy, r, colour = ring
        rect = fitz.Rect(ox - r, oy - r, ox + r, oy + r)
        inside = [v for v in words if rect.x0 <= (v[0] + v[2]) / 2 <= rect.x1 and rect.y0 <= (v[1] + v[3]) / 2 <= rect.y1]
        if len(inside) == 1:
            out.append((w[4], rect, colour))
    return out


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
    if not any(by_colour.values()):
        # Only when no curve bubble exists: a set draws its bubbles one way.
        for label, rect, colour in _ring_bubbles(page, words):
            shown = rect * to_display
            by_colour.setdefault(colour, []).append((label, *centre(shown), shown, rect.width))

    # A crowded grid end pushes its bubbles sideways on a kinked LEADER, so the
    # bubble no longer sits on its line. Read at the bubble, S2.106's G.9 and H
    # were 18pt (2 ft at 1/8") off the lines A3.25 draws them on, and the grid
    # check reported "G.9 = H" — a naming dispute the drawings do not have.
    # The bubbles are still GROUPED by where they are printed (a row of
    # bubbles shares a printed y however far each leader bends); only the
    # position reported for a line moves to where its leader lands. Grouped by
    # the corrected positions instead, A3.01's dense secondary lines chained
    # into false axes and hid the client's real RFI 002.
    on_line: dict[tuple[str, float, float], tuple[float, float]] = {}
    if any(by_colour.values()):
        segments = _segments(page, to_display)
        for found in by_colour.values():
            for label, x, y, shown, _ in found:
                on_line[(label, x, y)] = _on_line(shown, x, y, segments)

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
        along_x = {label: _line_at(label, at, found, on_line, 0) for label, at in along_x.items()}
        along_y = {label: _line_at(label, at, found, on_line, 1) for label, at in along_y.items()}
        # Every bubble of a label, not their union: a grid line is bubbled at
        # BOTH ends, and one box around both is a highlight the width of the
        # sheet. The caller picks one end.
        boxes: dict[str, list[list[float]]] = {}
        for label, _, _, shown in found:
            if label in along_x or label in along_y:
                boxes.setdefault(label, []).append([shown.x0, shown.y0, shown.x1, shown.y1])
        systems.append({"style": style, "along_x": along_x, "along_y": along_y, "bubbles": boxes})
    return systems


def page_grid(page: fitz.Page) -> tuple[dict[str, float], dict[str, float]]:
    """THE sheet's grid, as (columns {label: x}, rows {label: y}). See
    `page_grid_with_bubbles`, which also says where each label is printed."""
    columns, rows, _ = page_grid_with_bubbles(page)
    return columns, rows


def page_grid_with_bubbles(
    page: fitz.Page,
) -> tuple[dict[str, float], dict[str, float], dict[str, list[tuple[float, float]]]]:
    """THE sheet's grid, as (columns {label: x}, rows {label: y}) — the one
    definition the vision pass crops from and the eval generator derives from.

    Read with the styled reader rather than `bubbles`, because `bubbles` is
    blind on most of the sets this product is actually given: its 30-45pt
    window was set on one ARCH E1 sheet, and a client's 36x24 structural set
    draws its bubbles at 27pt — `VLM_CROP=intersections` found no grid on it,
    logged that at INFO, and ran the whole-sheet pass on every page. It also
    refused secondary lines (`C.1`, `B1.6`), so a column on one had no crop.

    When a sheet carries more than one grid style (its own grid plus another
    discipline's drawn in as a background), the one with the most
    intersections is taken, whole, rather than pooling them: pooled, `axes`
    keys by label and two grids naming different lines "6" collapse into one
    line at whichever position was read last. A sheet whose bubbles differ in
    style between its two axes has no single style with both, and falls back
    to the pooled `bubbles` reading it always had.

    The third value is the display-space centre of every bubble of each label
    (a line is usually bubbled at both ends), which `one_name_per_crossing`
    needs and the axes alone cannot carry.
    """
    systems = [s for s in styled_systems(page) if s["along_x"] and s["along_y"]]
    if systems:
        best = max(
            systems,
            key=lambda s: (len(s["along_x"]) * len(s["along_y"]), s["style"]),
        )
        centres = {
            label: [((b[0] + b[2]) / 2, (b[1] + b[3]) / 2) for b in boxes]
            for label, boxes in best["bubbles"].items()
        }
        return dict(best["along_x"]), dict(best["along_y"]), centres
    found = bubbles(page)
    columns, rows = axes(found)
    centres: dict[str, list[tuple[float, float]]] = {}
    for label, x, y in found:
        if label in columns or label in rows:
            centres.setdefault(label, []).append((x, y))
    return columns, rows, centres


# Two labels on one axis this close are ONE drawn line, not two.
COINCIDENT_PT = 2.0


def shared_lines(axis: dict[str, float]) -> list[list[str]]:
    """Groups of labels on one axis that sit at the same position.

    The client's S2.105 bubbles one horizontal line "2.3" at its left end and
    "2.4" at its right end (and "1.4"/"1.5" likewise); the line is drawn in
    two halves with a gap in the middle. Read as two lines, every crossing on
    it exists twice at one point, so a mark printed there is exactly as near
    one name as the other and was reported as BETWEEN them — 13 of that
    sheet's 29 "between" marks.
    """
    ordered = sorted(axis.items(), key=lambda kv: kv[1])
    groups: list[list[tuple[str, float]]] = []
    for label, at in ordered:
        if groups and at - groups[-1][-1][1] <= COINCIDENT_PT:
            groups[-1].append((label, at))
        else:
            groups.append([(label, at)])
    return [[label for label, _ in g] for g in groups if len(g) > 1]


def one_name_per_crossing(
    pairs: list[tuple[str, str, float, float]],
    shared: list[list[str]],
    centres: dict[str, list[tuple[float, float]]],
) -> list[tuple[str, str, float, float]]:
    """`pairs` with each point named once: where a line carries two names, a
    crossing takes the name whose bubble is NEARER to it.

    That is the drafter's own reading — the bubble at the left end names the
    left half — and it is the only one the geometry supports. It says nothing
    about whether two names for one line is intended; a caller that shows the
    result should say the line has two names. A label with no bubble recorded
    never wins a tie it is part of.
    """
    alias = {label: tuple(group) for group in shared for label in group}
    if not alias:
        return list(pairs)

    def reach(label: str, x: float, y: float) -> float:
        points = centres.get(label) or []
        return min(((px - x) ** 2 + (py - y) ** 2 for px, py in points), default=float("inf"))

    best: dict[tuple, tuple[float, int]] = {}
    for i, (col, row, x, y) in enumerate(pairs):
        key = (alias.get(col, (col,)), alias.get(row, (row,)))
        cost = (reach(col, x, y) if col in alias else 0.0) + (reach(row, x, y) if row in alias else 0.0)
        if key not in best or cost < best[key][0]:
            best[key] = (cost, i)
    keep = {i for _, i in best.values()}
    return [pair for i, pair in enumerate(pairs) if i in keep]


def is_secondary(label: str) -> bool:
    """A line between two others: `4.6`, `C.1`, `B1.5` — anything with a point.

    Real grid lines, and on a dense set most of them: the client's structural
    sheet carries 8 lettered and 6 numbered primary lines (48 intersections)
    and 360 once every secondary is counted.
    """
    return "." in label


def orientation(pairs) -> list[str]:
    """Which way each named set of grid lines runs on the sheet as displayed,
    in positional order. Empty when the geometry cannot say (one line only).

    `pairs` is `intersections(...)`. "Column lines" there is a naming
    CONVENTION (`transposed`: numbered = column), not the geometry — on the
    client's S2.105 the numbered lines run horizontally, and a chat told only
    "column lines: 1…6" called them vertical. This states what the viewer
    shows, from display coordinates, for every reader that names a grid."""
    xs: dict[str, list[float]] = {}
    ys: dict[str, list[float]] = {}
    for col, _, x, y in pairs:
        xs.setdefault(col, []).append(x)
        ys.setdefault(col, []).append(y)
    spread_x = max((max(v) - min(v) for v in xs.values() if len(v) > 1), default=0.0)
    spread_y = max((max(v) - min(v) for v in ys.values() if len(v) > 1), default=0.0)
    if spread_x == spread_y:
        return []
    # A column line whose crossings spread along x is drawn horizontally.
    columns_horizontal = spread_x > spread_y
    col_at = {c: (sum(ys[c]) if columns_horizontal else sum(xs[c])) / len(xs[c]) for c in xs}
    row_at: dict[str, list[float]] = {}
    for _, row, x, y in pairs:
        row_at.setdefault(row, []).append(x if columns_horizontal else y)
    cols_ordered = sorted(col_at, key=col_at.get)
    rows_ordered = sorted(row_at, key=lambda r: sum(row_at[r]) / len(row_at[r]))
    vertical, horizontal = (rows_ordered, cols_ordered) if columns_horizontal else (
        cols_ordered,
        rows_ordered,
    )
    v_name, h_name = ("row", "column") if columns_horizontal else ("column", "row")
    return [
        f"Drawn vertically on the sheet, left to right: {', '.join(vertical)} (the {v_name} lines).",
        f"Drawn horizontally on the sheet, top to bottom: {', '.join(horizontal)} (the {h_name} lines).",
    ]
