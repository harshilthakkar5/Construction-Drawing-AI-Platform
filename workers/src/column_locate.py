"""Where one column OCCURRENCE stands against the grid — measured from the
drawing, never read off a model's picture.

C03 asks: "if any column is centred to grid it is probably not dimensioned
from grid, but if any column is off the grids and not dimensioned, isolate and
highlight it". The first real targeted review of a forming plan answered it
with one RFI naming five column MARKS — and two of those marks stood dead on
their grid crossings. Nothing checked an occurrence: the model saw a whole
36x24 sheet at a few dozen DPI, read "no offset dimension beside the symbol"
as "location cannot be determined", and every later stage carried the mark
forward as one identity. So this module separates the three things that were
collapsed:

  * the MARK (C-12) from its OCCURRENCES — the same mark is printed at several
    places on one sheet, and each can be centred or not;
  * the LABEL from the BODY — a mark is printed beside its column, so a label's
    position says nothing about where the column is. The body is found in a
    high-resolution render of the area round the label, because on the
    client's sheets the column fills are raster tiles, not vector shapes;
  * the two AXES — centred on one grid line fixes one coordinate and says
    nothing about the other.

Tolerances are in the drawing's own units (inches at its printed scale), with
a floor in points for what a render and a bubble reading can resolve. Between
"centred" and "clearly off" there is a band that is neither, and an occurrence
in it is UNCERTAIN — never forced to one side. So is one whose body cannot be
found, whose body does not match its printed size, whose label sits between
two bodies, or which has no grid line within reach on an axis. A column whose
FACE lies on a grid line is also left uncertain: that is a definite position,
but whether it is the intended one is a question for a person, not a measured
fact.

A measurement never becomes a printed fact. It decides whether a "missing
offset dimension" claim is even possible (a column centred on two grid lines
needs no offset dimension), and its numbers are kept out of any RFI wording.

Everything here is in DISPLAY space — what a person sees — except the boxes
handed back for evidence, which are in the page's UNROTATED space like every
other stored box (`Occurrence.unrotated`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import fitz

# A column further than this from every grid line on an axis belongs to no
# line on that axis.
MAX_OFFSET_FT = 4.0
# |centre - line| at or under this is "on the line": drafting tolerance and
# what a render and a bubble reading can resolve.
CENTRE_IN = 3.0
# |centre - line| at or over this is clearly off it. Between the two is
# uncertain — neither claimed centred nor claimed off.
OFFSET_IN = 6.0
# Floor for both, in points: below this a render at the zoom used here and a
# bubble's position cannot tell two places apart, whatever the scale says.
REGISTRATION_PT = 2.0
# How far from its label a column body may be.
SEARCH_FT = 3.0
SEARCH_MIN_PT = 20.0
SEARCH_MAX_PT = 60.0
# Body size range. A column narrower than this is a wall line; a "column"
# longer than this is a wall.
MIN_BODY_IN = 6.0
MAX_BODY_FT = 6.0
# A body's box must be this full: a column is a filled rectangle or circle
# (pi/4 = 0.785), an L of wall corners is not.
MIN_FILL = 0.6
# The second-nearest body must be this much further than the nearest, or the
# label is between two columns and owns neither.
AMBIGUOUS_RATIO = 1.5
AMBIGUOUS_PT = 4.0
# A printed size "(14 x 30)" agrees with a body when each dimension is
# within this (the body's measured box includes its outline).
SIZE_TOL_SHARE = 0.25
SIZE_TOL_IN = 6.0
SIZE_TOL_PT = 4.0
# A printed dimension this close to a body may be the dimension locating it.
NEAR_DIMENSION_FT = 3.0

# A schedule mark that may label a column: C-12, C12, C-15.E, C-10.S. The
# suffix after a dot is part of the mark (on the client's sheets ".E" is an
# eccentric step and ".S" a sloped column) and is NEVER read as evidence of
# anything about the plan position.
MARK = re.compile(r"^[A-Z]{1,3}-?\d{1,3}[A-Z]?(?:\.[A-Z0-9]{1,2})?$")
# The size printed under a column mark: "(14 x 48)", "(24" DIA)".
_SIZE = re.compile(r"\(\s*(\d+(?:\.\d+)?)\s*\"?\s*[xX×]\s*(\d+(?:\.\d+)?)\s*\"?\s*\)")
_ROUND = re.compile(r"\(\s*(\d+(?:\.\d+)?)\s*\"?\s*(?:DIA|Ø|ROUND|RND)\.?\s*\)", re.I)
_DIMENSION = re.compile(r"\d+\s*'\s*-?\s*\d+(?:\s+\d+/\d+)?\s*\"|(?<![\d/])\d+(?:\s+\d+/\d+)?\s*\"")

# Families taken as column marks when no size is printed under them. A size
# under a mark is what makes it a column callout on any sheet; "C" is the
# common convention for a mark without one.
COLUMN_FAMILIES = ("C", "COL")

CENTRED, OFFSET, FACE, UNCERTAIN, NO_GRID = "centred", "offset", "face", "uncertain", "no_grid"
# Occurrence outcomes.
LOCATED = "located_by_grid"  # centred on a grid line in both directions
OFF_GRID = "off_grid"  # off a grid line in at least one direction, nothing nearby locates it
UNKNOWN = "unknown"  # could not be measured, or measured but not settled


def normalize(mark: str) -> str:
    return re.sub(r"[^A-Z0-9.]", "", (mark or "").upper())


def family(mark: str) -> str:
    m = re.match(r"[A-Z]+", normalize(mark))
    return m.group(0) if m else ""


# --- tolerances (pure) ----------------------------------------------------------------


def tolerances(pt_per_ft: float) -> tuple[float, float]:
    """(centred, clearly off) in display points at this printed scale."""
    centred = max(CENTRE_IN / 12 * pt_per_ft, REGISTRATION_PT)
    off = max(OFFSET_IN / 12 * pt_per_ft, 2 * centred)
    return centred, off


def inches(pt: float, pt_per_ft: float) -> float:
    return pt / pt_per_ft * 12


# --- one axis (pure) --------------------------------------------------------------------


@dataclass(frozen=True)
class Axis:
    """The column's relation to the grid lines drawn ACROSS one display axis:
    `direction` "x" compares the centre's x with the vertical lines."""

    direction: str
    line: str | None
    offset_pt: float | None  # centre - line, display points
    state: str
    also_named: tuple[str, ...] = ()

    def describe(self, pt_per_ft: float | None) -> str:
        name = self.name
        if self.state == NO_GRID:
            return f"no grid line within {MAX_OFFSET_FT:g} ft {'across' if self.direction == 'x' else 'up and down'} the sheet"
        if self.state == CENTRED:
            return f"centred on grid line {name}"
        if self.state == FACE:
            return f"its face, not its centre, appears to be on grid line {name}"
        side = ("right of" if (self.offset_pt or 0) > 0 else "left of") if self.direction == "x" else (
            "below" if (self.offset_pt or 0) > 0 else "above")
        if self.state == OFFSET:
            return f"centre {side} grid line {name}"
        return f"close to grid line {name} but not clearly on it"

    @property
    def name(self) -> str:
        return self.line if not self.also_named else f"{self.line} (also named {', '.join(self.also_named)})"

    def as_json(self, pt_per_ft: float | None) -> dict:
        return {
            "direction": self.direction,
            "line": self.line,
            "alsoNamed": list(self.also_named),
            "state": self.state,
            # Measured, not printed: kept for the audit, never for wording.
            "measuredOffsetIn": round(inches(self.offset_pt, pt_per_ft), 1) if self.offset_pt is not None and pt_per_ft else None,
        }


def classify_axis(direction: str, centre: float, half: float, lines: dict[str, float], pt_per_ft: float) -> Axis:
    """Where a body's centre stands against the nearest grid line on one axis.

    `half` is the body's half-extent along this axis: a centre `half` off the
    line puts the column's FACE on it."""
    limit = MAX_OFFSET_FT * pt_per_ft
    near = [(abs(centre - pos), label, pos) for label, pos in lines.items() if abs(centre - pos) <= limit]
    if not near:
        return Axis(direction, None, None, NO_GRID)
    near.sort(key=lambda t: (t[0], t[1]))
    _, label, pos = near[0]
    # One drawn line bubbled with two names (S2.105's 2.3/2.4) sits at one
    # position; the second name is said, not lost.
    aliases = tuple(sorted(lab for d, lab, p in near[1:] if abs(p - pos) < 0.5))
    offset = centre - pos
    centred, off = tolerances(pt_per_ft)
    if abs(offset) <= centred:
        state = CENTRED
    elif abs(abs(offset) - half) <= centred:
        state = FACE
    elif abs(offset) >= off:
        state = OFFSET
    else:
        state = UNCERTAIN
    return Axis(direction, label, offset, state, aliases)


# --- bodies (pure on an image) -------------------------------------------------------------


def find_bodies(rgb, origin: tuple[float, float], zoom: float, pt_per_ft: float) -> list[fitz.Rect]:
    """Column-shaped filled regions in a render, as display rects.

    `rgb` is an HxWx3 uint8 array of the area whose top-left display point is
    `origin`, rendered at `zoom` pixels per point. A column body is a NEUTRAL
    (grey or black) filled region: coloured underlays are drawn in another
    colour, and an architectural background drawn through a body is bridged
    by a small closing. The opening removes everything thinner than a column
    — text strokes, grid lines, outlines, hatching."""
    import cv2
    import numpy as np

    a = rgb.astype(np.int16)
    sat = a.max(axis=2) - a.min(axis=2)
    value = a.mean(axis=2)
    mask = ((sat < 40) & (value < 235)).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    k = max(3, int(round(MIN_BODY_IN * 2 / 3 / 12 * pt_per_ft * zoom)))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((k, k), np.uint8))
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask)
    lo = MIN_BODY_IN / 12 * pt_per_ft
    hi = MAX_BODY_FT * pt_per_ft
    out = []
    for i in range(1, count):
        x, y, w, h, area = (int(v) for v in stats[i])
        wpt, hpt = w / zoom, h / zoom
        if min(wpt, hpt) < lo or max(wpt, hpt) > hi:
            continue
        if area / float(w * h) < MIN_FILL:
            continue
        # A component touching the render's edge was cut off: its centre is
        # not the column's.
        if x == 0 or y == 0 or x + w >= rgb.shape[1] or y + h >= rgb.shape[0]:
            continue
        ox, oy = origin
        out.append(fitz.Rect(ox + x / zoom, oy + y / zoom, ox + (x + w) / zoom, oy + (y + h) / zoom))
    return out


def rect_distance(a: fitz.Rect, b: fitz.Rect) -> float:
    dx = max(b.x0 - a.x1, a.x0 - b.x1, 0.0)
    dy = max(b.y0 - a.y1, a.y0 - b.y1, 0.0)
    return (dx * dx + dy * dy) ** 0.5


def printed_size(text: str | None) -> tuple[float, float] | None:
    """Inches, from the line printed under a mark."""
    if not text:
        return None
    m = _SIZE.search(text)
    if m:
        return float(m.group(1)), float(m.group(2))
    m = _ROUND.search(text)
    if m:
        return float(m.group(1)), float(m.group(1))
    return None


def size_agrees(size_in: tuple[float, float], body: fitz.Rect, pt_per_ft: float) -> bool:
    measured = sorted((inches(body.width, pt_per_ft), inches(body.height, pt_per_ft)))
    nominal = sorted(size_in)
    floor_in = max(SIZE_TOL_IN, inches(SIZE_TOL_PT, pt_per_ft))
    return all(abs(m - n) <= max(SIZE_TOL_SHARE * n, floor_in) for m, n in zip(measured, nominal))


def associate(label: fitz.Rect, bodies: list[fitz.Rect], size_in, pt_per_ft: float) -> tuple[fitz.Rect | None, str | None]:
    """(the body a label belongs to, or None with why). Nearest by distance;
    a printed size must agree with it; a second candidate about as near makes
    the label ambiguous. A body that is wrong for the size is not swapped for
    the next one: that next one would be a guess."""
    if not bodies:
        return None, "no column body was found next to the label"
    ranked = sorted(bodies, key=lambda b: rect_distance(label, b))
    best = ranked[0]
    d0 = rect_distance(label, best)
    if len(ranked) > 1:
        d1 = rect_distance(label, ranked[1])
        if d1 <= max(d0 * AMBIGUOUS_RATIO, d0 + AMBIGUOUS_PT) and (
            size_in is None or size_agrees(size_in, ranked[1], pt_per_ft)
        ):
            return None, "the label sits between two column bodies and belongs to neither for certain"
    if size_in is not None and not size_agrees(size_in, best, pt_per_ft):
        return None, "the nearest body does not match the size printed under the mark"
    return best, None


# --- an occurrence ---------------------------------------------------------------------------


@dataclass
class Occurrence:
    mark: str
    label: fitz.Rect  # display
    size_text: str | None
    body: fitz.Rect | None = None  # display
    x: Axis | None = None
    y: Axis | None = None
    status: str = UNKNOWN
    reason: str = ""
    dimensions_near: list[str] = field(default_factory=list)
    pt_per_ft: float | None = None
    # Filled in by the caller that knows the page.
    unrotated: fitz.Rect | None = None

    @property
    def crossing(self) -> str | None:
        """The nearest crossing as the drawing would name it: the line
        through x first, then the one through y ("D/4.7")."""
        if self.x is None or self.y is None or not self.x.line or not self.y.line:
            return None
        return f"{self.x.line}/{self.y.line}"

    @property
    def ident(self) -> str:
        where = self.crossing or f"{self.label.x0:.0f},{self.label.y0:.0f}"
        return f"{self.mark}@{where}"

    def where(self) -> str:
        return f"{self.mark} near {self.crossing}" if self.crossing else f"{self.mark}"

    def summary(self) -> str:
        """One line a person can check by looking at the sheet."""
        if self.x is None or self.y is None:
            return f"{self.where()}: {self.reason}"
        axes = f"{self.x.describe(self.pt_per_ft)}; {self.y.describe(self.pt_per_ft)}"
        tail = {
            LOCATED: "located by the grid in both directions, so no offset dimension is needed",
            OFF_GRID: "off grid with no locating dimension printed near it",
        }.get(self.status, "not settled by measurement — a person must check" if not self.dimensions_near else self.reason)
        return f"{self.where()}: {axes} — {tail}"

    def unresolved(self) -> list[str]:
        """The grid lines this occurrence is OFF (the coordinates a dimension
        would have to fix)."""
        return [a.name for a in (self.x, self.y) if a is not None and a.state == OFFSET]

    def as_json(self) -> dict:
        return {
            "mark": self.mark,
            "occurrence": self.ident,
            "crossing": self.crossing,
            "status": self.status,
            "reason": self.reason,
            "sizePrinted": self.size_text,
            "x": self.x.as_json(self.pt_per_ft) if self.x else None,
            "y": self.y.as_json(self.pt_per_ft) if self.y else None,
            "dimensionsNear": self.dimensions_near[:4],
            "relation": "centreline",
            "bbox": box_json(self.unrotated) if self.unrotated is not None else None,
        }


def box_json(r: fitz.Rect) -> dict:
    return {"x": r.x0, "y": r.y0, "width": r.width, "height": r.height}


def settle(occ: Occurrence) -> Occurrence:
    """The occurrence's outcome from its two axes and what is printed near it.

    LOCATED only when BOTH axes are centred. OFF_GRID when an axis is clearly
    off and nothing near the column is a dimension that might locate it.
    Everything else is UNKNOWN with the reason."""
    if occ.body is None or occ.x is None or occ.y is None:
        occ.status = UNKNOWN
        return occ
    states = (occ.x.state, occ.y.state)
    if states == (CENTRED, CENTRED):
        occ.status, occ.reason = LOCATED, "centred on a grid line in both directions"
    elif OFFSET in states:
        if occ.dimensions_near:
            occ.status = UNKNOWN
            occ.reason = (
                f"off grid line {', '.join(occ.unresolved())}, but a printed dimension "
                f"({occ.dimensions_near[0]}) is near it and may locate it — a person must check"
            )
        elif any(s in (FACE, UNCERTAIN, NO_GRID) for s in states):
            occ.status = OFF_GRID
            occ.reason = f"off grid line {', '.join(occ.unresolved())}; the other direction is not settled"

        else:
            occ.status, occ.reason = OFF_GRID, f"off grid line {', '.join(occ.unresolved())}"
    else:
        unsettled = [a for a in (occ.x, occ.y) if a.state != CENTRED]
        occ.status = UNKNOWN
        occ.reason = "; ".join(a.describe(occ.pt_per_ft) for a in unsettled) + " — not settled by measurement"
    return occ


# --- reading a page ---------------------------------------------------------------------------


def zoom_for(pt_per_ft: float) -> float:
    """At least 200 DPI, and at least 2.5 pixels per drawn inch."""
    return min(12.0, max(200 / 72, 2.5 / (pt_per_ft / 12)))


def _labels(page: fitz.Page, wanted: set[str] | None) -> list[tuple[str, fitz.Rect, str | None]]:
    """(mark, display rect, size printed under it) for every mark label on
    the page, each physical label once (the client's sheets draw some words
    twice at one spot)."""
    import gridmarks

    words = page.get_text("words")
    sizes = gridmarks._size_lines(words)
    to_display = page.rotation_matrix
    seen: set[tuple[str, int, int]] = set()
    out = []
    for x0, y0, x1, y1, text, *_ in words:
        text = text.strip(",;:")
        if not MARK.match(text):
            continue
        key = normalize(text)
        if wanted is not None and key not in wanted:
            continue
        box = fitz.Rect(x0, y0, x1, y1)
        size = gridmarks._size_under(box, sizes)
        if wanted is None and size is None and family(text) not in COLUMN_FAMILIES:
            continue
        shown = fitz.Rect(box * to_display).normalize()
        spot = (key, round(shown.x0), round(shown.y0))
        if spot in seen:
            continue
        seen.add(spot)
        out.append((text, shown, size))
    return out


def _render(page: fitz.Page, clip: fitz.Rect, zoom: float):
    import numpy as np

    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip, alpha=False)
    return np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w, pix.n)[:, :, :3]


def _dimensions(page: fitz.Page) -> list[tuple[fitz.Rect, str]]:
    import plan_match

    return [(r, m.group(0)) for r, text in plan_match.text_lines(page) for m in _DIMENSION.finditer(text)]


def locate(
    page: fitz.Page,
    marks: set[str] | None = None,
    *,
    grid_lines: tuple[dict[str, float], dict[str, float]] | None = None,
    pt_per_ft: float | None = None,
) -> tuple[list[Occurrence], str | None]:
    """Every occurrence of the given marks (normalized), or of every column
    mark when None, measured against the page's grid. (occurrences, note):
    the note says why nothing could be measured, when that is so.

    The page should already have its markup stripped (`grid.without_markup`):
    a reviewer's cloud drawn over a column is not the column."""
    import grid
    import plan_match

    columns, rows = grid_lines if grid_lines is not None else grid.page_grid(page)
    if pt_per_ft is None:
        scales = plan_match.page_scales(page)
        pt_per_ft = min(scales) if scales else None
    labels = _labels(page, marks)
    if not labels:
        return [], "no column mark was found on the sheet"
    if not pt_per_ft:
        return [
            Occurrence(m, r, s, reason="the sheet prints no drawing scale, so nothing can be measured in inches")
            for m, r, s in labels
        ], "no drawing scale printed"
    if not columns or not rows:
        return [
            Occurrence(m, r, s, pt_per_ft=pt_per_ft, reason="no grid was read on this sheet")
            for m, r, s in labels
        ], "no grid read"
    zoom = zoom_for(pt_per_ft)
    reach = min(max(SEARCH_FT * pt_per_ft, SEARCH_MIN_PT), SEARCH_MAX_PT)
    pad = reach + MAX_BODY_FT * pt_per_ft / 2
    dims = _dimensions(page)
    out: list[Occurrence] = []
    claimed: dict[tuple[int, int], list[Occurrence]] = {}
    for mark, label, size_text in labels:
        occ = Occurrence(mark, label, size_text, pt_per_ft=pt_per_ft)
        out.append(occ)
        window = fitz.Rect(label.x0 - pad, label.y0 - pad, label.x1 + pad, label.y1 + pad) & page.rect
        if window.is_empty:
            occ.reason = "the label is at the edge of the sheet"
            continue
        rgb = _render(page, window, zoom)
        bodies = [b for b in find_bodies(rgb, (window.x0, window.y0), zoom, pt_per_ft) if rect_distance(label, b) <= reach]
        body, why = associate(label, bodies, printed_size(size_text), pt_per_ft)
        if body is None:
            occ.reason = why or "no column body found"
            continue
        occ.body = body
        claimed.setdefault((round(body.x0), round(body.y0)), []).append(occ)
        cx, cy = (body.x0 + body.x1) / 2, (body.y0 + body.y1) / 2
        occ.x = classify_axis("x", cx, body.width / 2, columns, pt_per_ft)
        occ.y = classify_axis("y", cy, body.height / 2, rows, pt_per_ft)
        near = NEAR_DIMENSION_FT * pt_per_ft
        occ.dimensions_near = [text for r, text in dims if rect_distance(r, body) <= near]
        settle(occ)
    # Two DIFFERENT marks claiming one body: neither label can be trusted to
    # be that column's.
    for owners in claimed.values():
        if len({normalize(o.mark) for o in owners}) > 1:
            for o in owners:
                o.body, o.x, o.y, o.status = None, None, None, UNKNOWN
                o.reason = "two different marks are printed next to the same column body"
    to_unrot = page.derotation_matrix
    for occ in out:
        area = fitz.Rect(occ.label)
        if occ.body is not None:
            area |= occ.body
        occ.unrotated = fitz.Rect(area * to_unrot).normalize()
    return out, None


def for_marks(occurrences: list[Occurrence], marks: set[str]) -> list[Occurrence]:
    return [o for o in occurrences if normalize(o.mark) in marks]


def describe(sheet: str, occurrences: list[Occurrence], note: str | None) -> str:
    """The C03 aid's text. Its first line says what it is and is not."""
    head = (
        f"MEASURED on {sheet} from the PDF geometry, not printed on the drawing: for each column MARK "
        "printed on the sheet, the column body next to it, and where that body's CENTRE stands against the "
        "grid lines in each direction (minor grid lines included). Centred on one line settles only that "
        "direction. A mark's suffix (.E, .S) says nothing about its plan position."
    )
    if note and not any(o.body is not None for o in occurrences):
        return head + f"\nNothing could be measured: {note}."
    lines = [head]
    for o in occurrences:
        lines.append(f"- {o.summary()}")
    return "\n".join(lines)
