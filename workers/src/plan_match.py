"""Lay one drawing over another: an enlarged plan over the overall plan it
enlarges, or two plans of one level at the same scale.

Built from a real client RFI (015, "Discrepancies in dimensions and column
location", A3.27 against A3.35) that the targeted review read and found
nothing in. Nothing it could see would have said so: both sheets are
architectural (the grid comparison only sets different disciplines against each
other), the enlarged sheet carries no grid bubbles at all, the two are drawn at
1/8" and 1/4", and the model was shown each sheet whole at ~44 DPI, where a
2'x1' column is a speck. What the two sheets DO share is the columns
themselves, so the columns are what lines them up:

    scales          every `1/4" = 1'-0"` on each page → the candidate size
                    ratios between the two (0.5 for 1/4" onto 1/8")
    elements        every small concrete element: a filled rectangle of
                    column size (6" to 4') with the concrete stipple drawn
                    inside it. The stipple is what separates a column from a
                    text mask, a legend swatch or a door leaf
    details         the enlarged sheet split into its drawings (connected
                    regions of ink), because each detail of an enlarged-plan
                    sheet is its own window onto the overall plan with its own
                    offset — A3.35's two Level 14 details land 590pt apart
    alignment       per detail, every element paired with every element of
                    the other sheet whose size agrees at that ratio votes for
                    one translation. The winner must be carried by
                    MIN_INLIERS elements and beat the runner-up by MIN_MARGIN,
                    for the reason rfi_grid gives: a regular bay lines up with
                    a copy of itself one bay over nearly as well as with the
                    original

Nothing here asks a model. `column_mismatches` (A, the check) reads its
findings straight off an alignment, and `pair_windows` (B, the pictures) cuts
the SAME area out of both sheets so a model can compare them side by side at a
resolution where a column is a shape and not a speck.

Everything is in DISPLAY space (what a person sees, `page.rotation_matrix`
applied); `to_pdf_box` converts back to the unrotated space chunk and evidence
bboxes use. Both sheets of RFI 015 are /Rotate 90.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import fitz

try:  # numpy/cv2 are worker dependencies; imported lazily so pure helpers test without them
    import cv2
    import numpy as np
except Exception:  # pragma: no cover
    cv2 = None
    np = None

import grid

# A column, pier or pilaster: smaller than this is a hatch mark, a curb or a
# text mask edge (a column is 8" at the least), larger is a wall or a slab.
MIN_SIDE_FT = 0.66
MAX_SIDE_FT = 4.0
# Concrete is drawn as a stipple of tiny strokes; a filled rectangle holding
# at least this many is concrete. A white text mask holds none. At 1/8" a
# 2'x1' column holds only two dots, so one or two also count when the box
# carries its own outline (a stroke the length of its long side) — a text
# mask never has one.
MIN_STIPPLE = 3
STIPPLE_MAX_PT = 3.0
# Sizes agree when within this fraction (or SIZE_TOL_PT) on each side.
SIZE_TOL = 0.15
SIZE_TOL_PT = 1.5
# Two elements are "the same place" within this, in real feet.
PLACE_TOL_FT = 0.5
# An element with no partner within PLACE_TOL but one of the same size within
# this is MOVED rather than missing.
MOVED_FT = 6.0
MIN_INLIERS = 3
MIN_MARGIN = 2
# Detail segmentation raster: points per pixel, and how far ink is joined.
SEG_PT_PER_PX = 6.0
SEG_JOIN_PX = 2

_SCALE = re.compile(
    r"""(?<![\w.'"/-])(?P<num>\d+(?:\s+\d+/\d+)?(?:/\d+)?)\s*(?:"|''|”|″)\s*=\s*1\s*(?:'|’|′)\s*-?\s*0\s*(?:"|''|”|″)"""
)


def _inches(text: str) -> float | None:
    """ "1/4" → 0.25, "1 1/2" → 1.5, "3" → 3.0."""
    total = 0.0
    for part in text.split():
        if "/" in part:
            a, b = part.split("/", 1)
            if not b.isdigit() or int(b) == 0 or not a.isdigit():
                return None
            total += int(a) / int(b)
        elif part.isdigit():
            total += int(part)
        else:
            return None
    return total or None


def scales_in(text: str) -> list[float]:
    """Every architectural scale printed in `text`, as points per foot
    (1/4" = 1'-0" → 18). Distinct, sorted."""
    out = set()
    for m in _SCALE.finditer(text):
        inches = _inches(m.group("num"))
        if inches and 1 / 64 <= inches <= 3:
            out.add(round(inches * 72, 4))
    return sorted(out)


def page_scales(page: fitz.Page) -> list[float]:
    """Read LINE BY LINE: joined into one string, "2026.02.27" on the line
    before made "27 1/4\"" a mixed number and the scale 27 1/4\" = 1'-0\"."""
    out: set[float] = set()
    for line in _lines(page):
        out.update(scales_in(line))
    return sorted(out)


def _lines(page: fitz.Page):
    for _, text in text_lines(page):
        yield text


def text_lines(page: fitz.Page, words=None) -> list[tuple[fitz.Rect, str]]:
    """Each text line as (display rect, text), rebuilt from the word list.
    `get_text("dict")` gives the same lines and cost 2.5s on a structural
    sheet whose word list costs a tenth of that."""
    words = page.get_text("words") if words is None else words
    matrix = page.rotation_matrix
    grouped: dict[tuple[int, int], list] = {}
    for w in words:
        grouped.setdefault((w[5], w[6]), []).append(w)
    out = []
    for ws in grouped.values():
        ws.sort(key=lambda w: w[7])
        rect = fitz.Rect(ws[0][:4])
        for w in ws[1:]:
            rect |= fitz.Rect(w[:4])
        out.append((rect * matrix, " ".join(w[4] for w in ws)))
    return out


def ratio_candidates(scales_a: list[float], scales_b: list[float]) -> list[float]:
    """Ratios s with (a point on B) = s × (a point on A), from every pair of
    scales the two pages print. A page may print a detail scale beside its
    plan scale, so more than one is tried and the alignment decides."""
    return sorted({round(b / a, 6) for a in scales_a for b in scales_b})


# --- elements -------------------------------------------------------------------


@dataclass(frozen=True)
class Element:
    """One concrete element in display space."""

    x0: float
    y0: float
    x1: float
    y1: float
    stipple: int = 0
    # The fill colour, rounded: a white column and a grey pad are different
    # things even at one size.
    fill: tuple = ()

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def w(self) -> float:
        return self.x1 - self.x0

    @property
    def h(self) -> float:
        return self.y1 - self.y0

    def rect(self) -> fitz.Rect:
        return fitz.Rect(self.x0, self.y0, self.x1, self.y1)


def elements_from_drawings(
    drawings: list[dict], matrix: fitz.Matrix, pt_per_ft: list[float], words: list[fitz.Rect] = ()
) -> list[Element]:
    """Small filled rectangles holding concrete stipple — see the module doc.
    `pt_per_ft` is every scale the page prints; a rectangle qualifies if its
    size is column-like at ANY of them. Three look-alikes are refused, each
    found on RFI 015's own sheets: a box with a WORD in it is the white mask
    behind a dimension string; a box touching ANOTHER stippled box is one
    piece of a concrete wall drawn as several rectangles; and (in
    `differences`) a box of another fill than the columns that aligned is a
    pad or a curb."""
    if not pt_per_ft:
        return []
    lo = MIN_SIDE_FT * min(pt_per_ft)
    hi = MAX_SIDE_FT * max(pt_per_ft)
    boxes: list[tuple[fitz.Rect, tuple]] = []
    pieces: list[fitz.Rect] = []  # every filled rectangle of wall thickness
    dots: list[tuple[float, float]] = []
    edges: list[tuple[float, float, float]] = []  # centre x, centre y, length
    for d in drawings:
        r = fitz.Rect(d["rect"]) * matrix
        items = d.get("items") or []
        if d.get("type") in ("f", "fs") and d.get("fill") is not None and len(items) == 1 and items[0][0] == "re":
            if min(r.width, r.height) <= hi:
                pieces.append(r)
            if min(r.width, r.height) >= lo and max(r.width, r.height) <= hi:
                boxes.append((r, tuple(round(c, 2) for c in d["fill"])))
        elif d.get("type") == "s":
            size = max(r.width, r.height)
            centre = ((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2)
            if size < STIPPLE_MAX_PT:
                dots.append(centre)
            elif size <= hi + 1:
                edges.append((*centre, size))
    if not boxes:
        return []
    # Bucket the stipple so each box counts only its neighbours.
    cell = 40.0

    def bucket(points):
        grid_: dict[tuple[int, int], list] = {}
        for p in points:
            grid_.setdefault((int(p[0] // cell), int(p[1] // cell)), []).append(p)
        return grid_

    dot_cells, edge_cells = bucket(dots), bucket(edges)

    def inside(cells, r: fitz.Rect):
        for i in range(int(r.x0 // cell), int(r.x1 // cell) + 1):
            for j in range(int(r.y0 // cell), int(r.y1 // cell) + 1):
                for p in cells.get((i, j), ()):
                    if r.x0 - 0.5 <= p[0] <= r.x1 + 0.5 and r.y0 - 0.5 <= p[1] <= r.y1 + 0.5:
                        yield p

    stippled = [q for q in pieces if any(True for _ in inside(dot_cells, q))]
    word_cells = _rect_cells(words, cell)
    piece_cells = _rect_cells(stippled, cell)
    out = []
    for r, fill in boxes:
        if any(r.intersects(w) for w in _near(word_cells, r, cell)):
            continue
        n = sum(1 for _ in inside(dot_cells, r))
        outlined = any(abs(p[2] - max(r.width, r.height)) <= 1.0 for p in inside(edge_cells, r))
        if not (n >= MIN_STIPPLE or (n >= 1 and outlined)):
            continue
        # The other leg of a wall corner is a piece of about the same size.
        # A slab the column stands in is many times thicker, and the concrete
        # band along a slab edge many times LONGER — every column on RFI 015's
        # clouded wall touches one, 553pt and 982pt long — so neither counts.
        grown = fitz.Rect(r.x0 - 0.75, r.y0 - 0.75, r.x1 + 0.75, r.y1 + 0.75)
        thick, long = 2 * min(r.width, r.height), 3 * max(r.width, r.height)
        if any(
            grown.intersects(q) and not _same_rect(q, r)
            and min(q.width, q.height) <= thick and max(q.width, q.height) <= long
            for q in _near(piece_cells, grown, cell)
        ):
            continue
        out.append(Element(r.x0, r.y0, r.x1, r.y1, n, fill))
    return _dedupe(out)


def _rect_cells(rects, cell: float) -> dict[tuple[int, int], list[fitz.Rect]]:
    """Rects bucketed by every cell they touch; a slab spanning half the
    sheet is skipped here, since only rects near a column's size matter."""
    out: dict[tuple[int, int], list[fitz.Rect]] = {}
    for r in rects:
        if (r.x1 - r.x0) / cell > 40 or (r.y1 - r.y0) / cell > 40:
            continue
        for i in range(int(r.x0 // cell), int(r.x1 // cell) + 1):
            for j in range(int(r.y0 // cell), int(r.y1 // cell) + 1):
                out.setdefault((i, j), []).append(r)
    return out


def _near(cells, r: fitz.Rect, cell: float):
    seen = set()
    for i in range(int(r.x0 // cell), int(r.x1 // cell) + 1):
        for j in range(int(r.y0 // cell), int(r.y1 // cell) + 1):
            for q in cells.get((i, j), ()):
                if id(q) not in seen:
                    seen.add(id(q))
                    yield q


def _same_rect(a: fitz.Rect, b: fitz.Rect) -> bool:
    return all(abs(u - v) < 1.0 for u, v in zip(a, b))


def _dedupe(elements: list[Element]) -> list[Element]:
    """A column drawn as fill + outline, or twice on two layers, is one."""
    kept: list[Element] = []
    for e in sorted(elements, key=lambda e: -e.stipple):
        if not any(abs(e.cx - k.cx) < 1 and abs(e.cy - k.cy) < 1 and abs(e.w - k.w) < 1.5 for k in kept):
            kept.append(e)
    return kept


def page_elements(page: fitz.Page, pt_per_ft: list[float], words=None) -> list[Element]:
    matrix = page.rotation_matrix
    words = page.get_text("words") if words is None else words
    return elements_from_drawings(page.get_drawings(), matrix, pt_per_ft, [fitz.Rect(w[:4]) * matrix for w in words])


# --- details ---------------------------------------------------------------------


def detail_labels(page: fitz.Page):
    """(label image, pt per px): each connected region of ink on the page a
    different integer, 0 for paper. Title block and frame end up as their own
    regions; nothing downstream asks for them, because a detail is chosen by
    the elements inside it."""
    if cv2 is None:
        return None, SEG_PT_PER_PX
    zoom = 1.0 / SEG_PT_PER_PX
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), colorspace=fitz.csGRAY, alpha=False)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
    ink = (img < 245).astype(np.uint8)
    ink = cv2.dilate(ink, np.ones((2 * SEG_JOIN_PX + 1, 2 * SEG_JOIN_PX + 1), np.uint8))
    _, labels = cv2.connectedComponents(ink, connectivity=8)
    return labels, SEG_PT_PER_PX


def label_at(labels, pt_per_px: float, x: float, y: float) -> int:
    if labels is None:
        return 1
    i, j = int(y / pt_per_px), int(x / pt_per_px)
    if 0 <= i < labels.shape[0] and 0 <= j < labels.shape[1]:
        return int(labels[i, j])
    return 0


# --- alignment -------------------------------------------------------------------


@dataclass
class Alignment:
    """B ≈ scale × A + (tx, ty), for the elements of ONE detail of A."""

    scale: float
    tx: float
    ty: float
    inliers: list[tuple[Element, Element]]
    runner_up: int
    detail: int = 1
    members: list[Element] = field(default_factory=list)

    def to_b(self, x: float, y: float) -> tuple[float, float]:
        return self.scale * x + self.tx, self.scale * y + self.ty

    def to_a(self, x: float, y: float) -> tuple[float, float]:
        return (x - self.tx) / self.scale, (y - self.ty) / self.scale

    def rect_to_b(self, r: fitz.Rect) -> fitz.Rect:
        x0, y0 = self.to_b(r.x0, r.y0)
        x1, y1 = self.to_b(r.x1, r.y1)
        return fitz.Rect(x0, y0, x1, y1)


def same_size(a: Element, b: Element, scale: float) -> bool:
    def close(u: float, v: float) -> bool:
        return abs(u - v) <= max(SIZE_TOL_PT, SIZE_TOL * max(u, v))

    return close(a.w * scale, b.w) and close(a.h * scale, b.h)


def align(members: list[Element], others: list[Element], scale: float, tol_b: float) -> Alignment | None:
    """The translation most size-matched pairs agree on, or None when it is
    not carried by MIN_INLIERS or does not beat the runner-up by MIN_MARGIN.

    Votes are binned at tol_b and a winner is counted with its neighbours, so
    a translation straddling a bin edge is not split in two."""
    votes: dict[tuple[int, int], list[tuple[Element, Element]]] = {}
    for a in members:
        for b in others:
            if not same_size(a, b, scale):
                continue
            tx, ty = b.cx - scale * a.cx, b.cy - scale * a.cy
            votes.setdefault((round(tx / tol_b), round(ty / tol_b)), []).append((a, b))
    if not votes:
        return None

    def support(key: tuple[int, int]) -> list[tuple[Element, Element]]:
        pairs = []
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                pairs += votes.get((key[0] + di, key[1] + dj), [])
        return pairs

    scored = []
    for key in votes:
        pairs = support(key)
        # A vote counts DISTINCT elements on both sides: one element pairing
        # with three look-alikes is one piece of support, not three.
        distinct = min(len({id(a) for a, _ in pairs}), len({id(b) for _, b in pairs}))
        scored.append((distinct, key, pairs))
    scored.sort(key=lambda s: -s[0])
    best_n, best_key, pairs = scored[0]
    # Runner-up: the best mode that is not a neighbour of the winner.
    runner = next((n for n, k, _ in scored if abs(k[0] - best_key[0]) > 2 or abs(k[1] - best_key[1]) > 2), 0)
    if best_n < MIN_INLIERS or best_n - runner < MIN_MARGIN:
        return None
    tx = sum(b.cx - scale * a.cx for a, b in pairs) / len(pairs)
    ty = sum(b.cy - scale * a.cy for a, b in pairs) / len(pairs)
    # Re-select inliers against the refined translation, one partner each.
    inliers, used = [], set()
    for a in members:
        px, py = scale * a.cx + tx, scale * a.cy + ty
        best = min(
            (b for b in others if id(b) not in used and same_size(a, b, scale)),
            key=lambda b: math.hypot(b.cx - px, b.cy - py),
            default=None,
        )
        if best is not None and math.hypot(best.cx - px, best.cy - py) <= tol_b:
            inliers.append((a, best))
            used.add(id(best))
    if len(inliers) < MIN_INLIERS:
        return None
    return Alignment(scale, tx, ty, inliers, runner)


@dataclass
class SheetGeometry:
    """What one page contributes: its scales, elements and detail regions."""

    page: fitz.Page
    scales: list[float]
    elements: list[Element]
    labels: object = None
    pt_per_px: float = SEG_PT_PER_PX

    words: list = field(default_factory=list)

    @classmethod
    def read(cls, page: fitz.Page) -> "SheetGeometry":
        page = grid.without_markup(page)
        words = page.get_text("words")
        scales = sorted({v for _, text in text_lines(page, words) for v in scales_in(text)})
        # No printed scale, no sizes in feet: nothing to compare, and the
        # drawing list — 90,000 paths on a structural sheet — is not read.
        elements = page_elements(page, scales, words) if scales else []
        labels, ppp = detail_labels(page) if elements else (None, SEG_PT_PER_PX)
        return cls(page, scales, elements, labels, ppp, words)

    def detail_of(self, e: Element) -> int:
        return label_at(self.labels, self.pt_per_px, e.cx, e.cy)


def align_sheets(a: SheetGeometry, b: SheetGeometry) -> list[Alignment]:
    """Every detail of A that lines up with B. A is expected to be the
    LARGER scale (the enlarged plan); for two sheets at one scale either way
    round works. Each detail is aligned on its own elements only."""
    out: list[Alignment] = []
    if not a.elements or not b.elements:
        return out
    by_detail: dict[int, list[Element]] = {}
    for e in a.elements:
        d = a.detail_of(e)
        if d:
            by_detail.setdefault(d, []).append(e)
    for scale in ratio_candidates(a.scales, b.scales):
        if scale > 1.0 + 1e-6:
            continue  # A must be the enlarged (or equal) one
        tol_b = PLACE_TOL_FT * min(b.scales)
        for detail, members in by_detail.items():
            if len(members) < MIN_INLIERS:
                continue
            found = align(members, b.elements, scale, tol_b)
            if found is not None:
                found.detail = detail
                found.members = members
                out.append(found)
    # One alignment per detail: the scale with the most inliers wins.
    best: dict[int, Alignment] = {}
    for al in out:
        if al.detail not in best or len(al.inliers) > len(best[al.detail].inliers):
            best[al.detail] = al
    return list(best.values())


def detail_names(page: fitz.Page, sheet: "SheetGeometry") -> dict[int, str]:
    """ {region: "detail 1"} from the sheet's own detail titles: a scale line
    (`1/4" = 1'-0"`), the title just above it, and the detail number printed
    alone to the title's left. A region is named by the title nearest below
    its ink. Regions with no title get none — callers fall back to the sheet."""
    matrix = page.rotation_matrix
    raw = sheet.words or page.get_text("words")
    lines = [(r, t) for r, t in text_lines(page, raw) if t.strip()]
    words = [(fitz.Rect(w[:4]) * matrix, w[4]) for w in raw]
    titles = []
    for rect, text in lines:
        if not scales_in(text):
            continue
        above = [
            (r, t) for r, t in lines
            if r.y1 <= rect.y0 + 2 and rect.y0 - r.y1 < 30 and abs(r.x0 - rect.x0) < 40 and not scales_in(t)
        ]
        if not above:
            continue
        title_rect, _ = max(above, key=lambda rt: rt[0].y1)
        number = next(
            (t for r, t in words
             if t.isdigit() and len(t) <= 2 and title_rect.x0 - 80 < r.x1 <= title_rect.x0 + 2
             and abs((r.y0 + r.y1) / 2 - title_rect.y1) < 30),
            None,
        )
        if number:
            titles.append((title_rect, f"detail {number}"))
    names: dict[int, str] = {}
    if sheet.labels is None:
        return names
    for region in {sheet.detail_of(e) for e in sheet.elements}:
        box = _region_box(sheet, region)
        if box is None:
            continue
        below = [
            (t.y0 - box.y1, name) for t, name in titles
            # Under the drawing, or inside its lower half: RFI 015's A3.35
            # detail 1 runs on below its own title.
            if box.x0 - 20 <= t.x0 <= box.x1 and (box.y0 + box.y1) / 2 <= t.y0 <= box.y1 + 150
        ]
        if below:
            names[region] = min(below, key=lambda b: abs(b[0]))[1]
    return names


def _region_box(sheet: "SheetGeometry", region: int) -> fitz.Rect | None:
    if sheet.labels is None:
        return None
    ys, xs = np.nonzero(sheet.labels == region)
    if not len(xs):
        return None
    k = sheet.pt_per_px
    return fitz.Rect(xs.min() * k, ys.min() * k, (xs.max() + 1) * k, (ys.max() + 1) * k)


def to_pdf_box(page: fitz.Page, r: fitz.Rect) -> dict:
    """A display-space rect as the {x, y, width, height} evidence boxes use
    (the page's unrotated space, like chunk bboxes)."""
    u = fitz.Rect(r * page.derotation_matrix).normalize()
    return {"x": round(u.x0, 2), "y": round(u.y0, 2), "width": round(u.width, 2), "height": round(u.height, 2)}


# --- the check (A) ----------------------------------------------------------------


@dataclass
class Difference:
    """One column the two sheets disagree about. `a`/`b` are the element on
    each sheet (None when that sheet shows nothing there)."""

    kind: str  # moved | size | only_a | only_b
    a: Element | None
    b: Element | None
    offset_ft: float = 0.0


def _ft(pt: float, pt_per_ft: float) -> str:
    """Feet-and-inches, the way a drawing writes it: 2'-0", 1'-2"."""
    inches = round(pt / pt_per_ft * 12)
    return f"{inches // 12}'-{inches % 12}\""


def size_text(e: Element, pt_per_ft: float) -> str:
    return f"{_ft(e.w, pt_per_ft)} x {_ft(e.h, pt_per_ft)}"


def differences(al: Alignment, a: SheetGeometry, b: SheetGeometry) -> list[Difference]:
    """What the two sheets disagree about inside one aligned detail.

    Only column-SIZED elements take part (MIN/MAX_SIDE_FT at this detail's own
    scale, which the alignment has now told us), and only where the other
    sheet has drawing to compare: an element of B counts only if it falls
    inside this detail's ink on A, because an enlarged plan shows a window
    of the overall plan and a column outside the window is not missing from it.
    """
    ptft_b = min(b.scales)
    ptft_a = ptft_b / al.scale
    tol_b = PLACE_TOL_FT * ptft_b
    moved_b = MOVED_FT * ptft_b

    # The columns that lined up say what a column LOOKS like on each sheet.
    fills_a = {x.fill for x, _ in al.inliers}
    fills_b = {y.fill for _, y in al.inliers}

    def column(e: Element, ptft: float, fills: set) -> bool:
        return (
            MIN_SIDE_FT * ptft <= min(e.w, e.h)
            and max(e.w, e.h) <= MAX_SIDE_FT * ptft
            and e.fill in fills
        )

    mine = [e for e in al.members if column(e, ptft_a, fills_a)]
    edge = detail_box(a, al.detail)
    page_b = b.page.rect
    theirs = []
    for e in b.elements:
        if not column(e, ptft_b, fills_b):
            continue
        x, y = al.to_a(e.cx, e.cy)
        if a.labels is None or label_at(a.labels, a.pt_per_px, x, y) == al.detail:
            theirs.append(e)

    out: list[Difference] = []
    claimed: set[int] = set()
    for e in mine:
        px, py = al.to_b(e.cx, e.cy)
        if not page_b.contains(fitz.Point(px, py)):
            continue
        near = sorted(theirs, key=lambda o: math.hypot(o.cx - px, o.cy - py))
        here = [o for o in near if math.hypot(o.cx - px, o.cy - py) <= tol_b and id(o) not in claimed]
        if here:
            o = here[0]
            claimed.add(id(o))
            # A box at the very edge of an enlarged detail may be cut off by
            # the detail's window, which is not a size the drafter chose.
            if not same_size(e, o, al.scale) and not _at_edge(e, edge, PLACE_TOL_FT * ptft_a):
                out.append(Difference("size", e, o))
            continue
        moved = next(
            (o for o in near if id(o) not in claimed and same_size(e, o, al.scale)
             and math.hypot(o.cx - px, o.cy - py) <= moved_b
             and not _has_partner(o, mine, al, tol_b)),
            None,
        )
        if moved is not None:
            claimed.add(id(moved))
            out.append(Difference("moved", e, moved, math.hypot(moved.cx - px, moved.cy - py) / ptft_b))
        else:
            out.append(Difference("only_a", e, None))
    for o in theirs:
        if id(o) in claimed or _has_partner(o, mine, al, tol_b):
            continue
        out.append(Difference("only_b", None, o))
    return out


def detail_box(sheet: SheetGeometry, detail: int) -> fitz.Rect | None:
    """The bounding box of one detail's ink, in display points."""
    if sheet.labels is None:
        return None
    ys, xs = np.nonzero(sheet.labels == detail)
    if not len(xs):
        return None
    k = sheet.pt_per_px
    # The segmentation dilates ink by SEG_JOIN_PX; take it back off.
    pad = SEG_JOIN_PX * k
    return fitz.Rect(xs.min() * k + pad, ys.min() * k + pad, (xs.max() + 1) * k - pad, (ys.max() + 1) * k - pad)


def _at_edge(e: Element, box: fitz.Rect | None, tol: float) -> bool:
    if box is None:
        return False
    return (
        e.x0 - box.x0 <= tol or box.x1 - e.x1 <= tol or e.y0 - box.y0 <= tol or box.y1 - e.y1 <= tol
    )


def _has_partner(o: Element, mine: list[Element], al: Alignment, tol_b: float) -> bool:
    for e in mine:
        px, py = al.to_b(e.cx, e.cy)
        if math.hypot(o.cx - px, o.cy - py) <= tol_b:
            return True
    return False
