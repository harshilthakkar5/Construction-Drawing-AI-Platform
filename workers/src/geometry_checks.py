"""The code's own comparison of the sheet pairs the full scan lines up — no model.

"Find RFIs in drawings" used to be two passes over the same drawings. The code
checks read every page for text problems; the AI comparison then downloaded the
same PDFs again, lined up pairs of sheets that should agree, and showed the AI
every window of every pair — even where the code could already measure that the
two sheets draw the same thing. The measurements existed (grid positions,
columns, walls) but were used only AFTER the AI had looked, to throw out its
mistakes. This module makes them do the work first:

  * FINDINGS (zero tokens). Two comparisons that measure rather than guess:
      - `grid_spacing_findings`: two sheets that name the same grid lines but
        draw them a different distance apart, at the scale both print.
      - `column_findings`: rfi_columns' column overlay — a column one sheet
        draws and the other does not, or draws feet away — on the pairs where
        a column must agree (architectural / structural, or one discipline's
        enlarged plan over its overall plan).
  * TRIAGE (`triage_tiles`). A window the code can settle is not sent to the
    AI: one side draws nothing there, or every wall line both sheets draw lines
    up exactly and no line has a parallel twin 3 in to 2 ft off (the shape of a
    wall that moved). Every other window goes to the AI with what the code
    already knows about it — findings already reported there, and lines it
    measured as slightly offset — so it neither re-reports nor misses them.

Precision first, as everywhere in the RFI code: each rule refuses rather than
guesses (a naming dispute is not a spacing difference; a sheet that prints
several scales is not measured), and a window is settled only on positive
evidence of agreement, never on the absence of a signal.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

import fitz

from rfi_checks import Finding, Page, fingerprint, page_label

log = logging.getLogger("worker.geometry_checks")

GRID_SPACING = "grid_spacing"
COLUMN_MISMATCH = "column_mismatch"

# --- Grid spacing -----------------------------------------------------------------

# A bay is reported only when the two sheets differ by more than this, in real
# inches at the printed scale, and never by less than SPACING_MIN_PT on paper
# (a bubble centre is read to about half a point).
SPACING_TOL_IN = 3.0
SPACING_MIN_PT = 1.5
# More than this is not one bay drawn differently: the names point at
# different lines (a naming dispute, which grid_mismatch reports) or the
# sheets are not the same area.
SPACING_MAX_FT = 10.0
# At most this many bays may differ, and at least this many must agree — one
# wrong bay shifts every line after it, which is exactly the shape measured.
SPACING_MAX_DIFFERING = 2
SPACING_MIN_AGREEING = 2


def _common_scale(pair) -> float | None:
    """Points per foot on sheet B, when it can be known without guessing: the
    ONE scale both sheets print (a same-level pair), or B's only scale (an
    enlarged plan over B)."""
    b_scales = {round(s, 3) for s in pair.b.scales}
    if pair.kind == "enlarged" or abs(getattr(pair.transform, "scale", 1.0) - 1) > 1e-6:
        return next(iter(b_scales)) if len(b_scales) == 1 else None
    common = b_scales & {round(s, 3) for s in pair.a.scales}
    return next(iter(common)) if len(common) == 1 else None


def _feet(pt: float, ptft: float) -> str:
    inches = round(abs(pt) / ptft * 12)
    return f"{inches // 12}'-{inches % 12}\""


def spacing_differences(axis_a: dict[str, float], axis_b: dict[str, float], scale: float, offset: float,
                        ptft: float) -> list[dict] | None:
    """Bays of one grid axis drawn a different size on two sheets. Pure.

    `scale`/`offset` map A's positions onto B's (B = scale * A + offset, the
    pair's line-up). Returns [] when the sheets agree, the differing bays when
    the pattern is one measurement can trust, and None when it is not
    comparable at all (too few shared lines, a different order, names that
    point at different lines)."""
    primary = sorted(
        (k for k in axis_a if k in axis_b and "." not in k),
        key=lambda k: axis_a[k],
    )
    if len(primary) < SPACING_MIN_AGREEING + 1:
        return None
    b_order = sorted(primary, key=lambda k: axis_b[k])
    if b_order != primary and b_order != primary[::-1]:
        return None  # the two sheets do not even put these lines in one order
    sign = 1 if b_order == primary else -1
    if sign * scale < 0:
        return None
    max_pt = SPACING_MAX_FT * ptft
    # Names must point at the same lines: under the line-up every shared line
    # lands within one bay difference of its name on the other sheet, and at
    # least one lands exactly. A naming dispute (RFI 002: one sheet's row 6 is
    # the other's 9) lands every line a whole bay off.
    tol = max(SPACING_MIN_PT, SPACING_TOL_IN / 12 * ptft)
    offsets = [axis_b[k] - (scale * axis_a[k] + offset) for k in primary]
    if any(abs(o) > max_pt for o in offsets) or not any(abs(o) <= tol for o in offsets):
        return None
    # A shared name that lands off its partner but ON another line of the
    # other sheet is a naming dispute, not a distance: on the client's RFI 002
    # sheets the architectural G lands exactly on the structural F.7, which
    # read as "F-G is 21'-1" here and 28'-10" there". grid_mismatch reports it.
    for k, o in zip(primary, offsets):
        if abs(o) <= tol:
            continue
        on_b = scale * axis_a[k] + offset
        on_a = (axis_b[k] - offset) / scale
        if any(abs(v - on_b) <= tol for name, v in axis_b.items() if name != k) or any(
            abs(v - on_a) <= tol / abs(scale) for name, v in axis_a.items() if name != k
        ):
            return None
    bays = []
    for first, second in zip(primary, primary[1:]):
        drawn_a = abs(scale * (axis_a[second] - axis_a[first]))
        drawn_b = abs(axis_b[second] - axis_b[first])
        bays.append({"from": first, "to": second, "a": drawn_a, "b": drawn_b, "diff": drawn_b - drawn_a})
    differing = [b for b in bays if abs(b["diff"]) > tol]
    agreeing = len(bays) - len(differing)
    if not differing:
        return []
    if len(differing) > SPACING_MAX_DIFFERING or agreeing < SPACING_MIN_AGREEING or agreeing <= len(differing):
        return None
    if any(abs(b["diff"]) > max_pt for b in differing):
        return None
    return differing


def _bay_box(axis: str, first: float, second: float, other: dict[str, float]) -> fitz.Rect | None:
    """The bay between two lines of one axis, across the first two lines of
    the other axis — tight enough to point at, unlike the whole grid."""
    across = sorted(other.values())
    if len(across) < 2:
        return None
    lo, hi = min(first, second), max(first, second)
    if axis == "x":
        return fitz.Rect(lo - 6, across[0] - 6, hi + 6, across[1] + 6)
    return fitz.Rect(across[0] - 6, lo - 6, across[1] + 6, hi + 6)


def _evidence(page_row: Page, page: fitz.Page | None, rect: fitz.Rect | None, quote: str) -> dict:
    bbox = None
    if page is not None and rect is not None:
        u = fitz.Rect(rect * page.derotation_matrix)
        u.normalize()
        bbox = {"x": round(u.x0, 2), "y": round(u.y0, 2), "width": round(u.width, 2), "height": round(u.height, 2)}
    return {
        "documentId": page_row.document_id,
        "pageNumber": page_row.page_number,
        "combinedPageNumber": page_row.combined_page_number,
        "sheetNumber": page_row.sheet_number,
        "bbox": bbox,
        "chunkId": None,
        "quote": quote,
        "role": "finding",
    }


def grid_spacing_findings(pairs, pages: dict[str, Page], open_page) -> tuple[list[Finding], list[str]]:
    """One finding per pair and axis whose shared grid lines are drawn a
    different distance apart. Only pairs lined up by their GRIDS take part — a
    pair lined up by its walls has no grid to measure."""
    findings: list[Finding] = []
    notes: list[str] = []
    compared = 0
    for pair in pairs:
        if pair.transform is None or pair.wall_shift is not None:
            continue
        if not (pair.a.has_grid() and pair.b.has_grid()):
            continue
        ptft = _common_scale(pair)
        if not ptft:
            continue
        row_a, row_b = pages.get(pair.a.page_id), pages.get(pair.b.page_id)
        if row_a is None or row_b is None:
            continue
        compared += 1
        t = pair.transform
        for axis, offset in (("x", t.tx), ("y", t.ty)):
            other = "y" if axis == "x" else "x"
            diffs = spacing_differences(pair.a.grid.get(axis) or {}, pair.b.grid.get(axis) or {}, t.scale, offset, ptft)
            if not diffs:
                continue
            ptft_a = ptft / t.scale
            page_a = open_page(pair.a.document_id, pair.a.page_number) if open_page else None
            page_b = open_page(pair.b.document_id, pair.b.page_number) if open_page else None
            lines, evidence = [], []
            for d in diffs:
                lines.append(
                    f"grid {d['from']} to {d['to']} is {_feet(d['a'] / t.scale, ptft_a)} on {pair.a.label} "
                    f"and {_feet(d['b'], ptft)} on {pair.b.label}"
                )
                box_a = _bay_box(axis, pair.a.grid[axis][d["from"]], pair.a.grid[axis][d["to"]], pair.a.grid.get(other) or {})
                box_b = _bay_box(axis, pair.b.grid[axis][d["from"]], pair.b.grid[axis][d["to"]], pair.b.grid.get(other) or {})
                evidence.append(_evidence(row_a, page_a, box_a,
                                          f"{pair.a.label}: grid {d['from']}-{d['to']} drawn {_feet(d['a'] / t.scale, ptft_a)} apart"))
                evidence.append(_evidence(row_b, page_b, box_b,
                                          f"{pair.b.label}: grid {d['from']}-{d['to']} drawn {_feet(d['b'], ptft)} apart"))
            names = ", ".join(f"{d['from']}-{d['to']}" for d in diffs)
            findings.append(Finding(
                check_type=GRID_SPACING,
                fingerprint=fingerprint(GRID_SPACING, *sorted([pair.a.label, pair.b.label]), axis,
                                        *sorted(f"{d['from']}-{d['to']}" for d in diffs)),
                # Measured from where each sheet DRAWS its lines, not from a
                # printed dimension string, so never more than medium.
                confidence="medium",
                subject=f"Grid spacing {names} differs between {pair.a.label} and {pair.b.label}",
                question=(
                    f"{pair.a.label} and {pair.b.label} name the same grid lines, but measured at the printed scale "
                    f"{'; '.join(lines)}. The other bays agree. Please confirm the correct distance between these "
                    "grid lines and which drawing will be revised."
                ),
                evidence=evidence[:6],
                facts={"sheets": [pair.a.label, pair.b.label], "axis": axis,
                       "bays": [{k: (round(v, 2) if isinstance(v, float) else v) for k, v in d.items()} for d in diffs]},
            ))
    if compared:
        notes.append(f"Grid spacing: compared the grid lines of {compared} sheet pair(s) at their printed scale.")
    return findings, notes


# --- Columns --------------------------------------------------------------------

# Disciplines whose plans must agree on where a column is. An MEP plan's
# background may leave the columns out, and that is not a question to ask.
COLUMN_DISCIPLINES = {"architectural", "structural", "interiors"}


def _column_pair(pair) -> bool:
    a, b = pair.a.discipline, pair.b.discipline
    if pair.kind == "enlarged":
        return a == b or (a in COLUMN_DISCIPLINES and b in COLUMN_DISCIPLINES)
    return a in COLUMN_DISCIPLINES and b in COLUMN_DISCIPLINES


def column_findings(pairs, pages: dict[str, Page], open_page, cache: dict | None = None) -> tuple[list[Finding], list[str]]:
    """rfi_columns' overlay on every pair where a column must agree. Size
    differences between two DISCIPLINES are dropped: an architectural plan
    draws a column with its finishes and the structural plan without."""
    import plan_match
    import rfi_columns

    cache = {} if cache is None else cache
    geometry = cache.setdefault("geometry", {})
    findings: list[Finding] = []
    notes: list[str] = []
    for pair in pairs:
        if not _column_pair(pair):
            continue
        rows = pages.get(pair.a.page_id), pages.get(pair.b.page_id)
        if None in rows:
            continue
        sheets = []
        for f, row in zip((pair.a, pair.b), rows):
            key = (f.document_id, f.page_number)
            if key not in geometry:
                page = open_page(f.document_id, f.page_number)
                geometry[key] = plan_match.SheetGeometry.read(page) if page is not None else None
            if geometry[key] is None:
                break
            # The level is the pair's: the planner already matched it, with
            # a reader that knows "FIRST FLOOR" as well as "LEVEL 1".
            sheets.append(rfi_columns.Sheet.from_geometry(row, geometry[key], level=f.level))
        if len(sheets) < 2:
            continue
        blind = [sh.label for sh in sheets if not sh.geometry.elements]
        if blind:
            # "Could not look" must not read as "found nothing".
            notes.append(f"Columns: {' and '.join(blind)} draw no columns the code can recognise (filled, "
                         f"column-sized boxes), so the columns of {pair.a.label} and {pair.b.label} were not compared.")
            continue
        a, b = sheets
        alignments = pair.alignments or plan_match.align_sheets(a.geometry, b.geometry)
        alignments, why = rfi_columns.comparable_alignments(a, b, alignments)
        if why:
            notes.append(why)
        if not alignments:
            continue
        kinds = None if pair.a.discipline == pair.b.discipline else {"moved", "only_a", "only_b"}
        found, more = rfi_columns.column_mismatches(a, b, alignments, kinds=kinds)
        findings += found
        notes += more
    return findings, notes


# --- Triage -----------------------------------------------------------------------

# A window is blank when fewer than this share of its pixels carry ink.
BLANK_SHARE = 0.003
BLANK_PX = 256
# Two wall lines are the same line within SAME_LINE_PT on paper or SAME_LINE_IN
# in real inches, whichever is larger: on JETRIGHT's 1/8" plans a 1" offset is
# a pen width, and 26 of the first 64 "moved walls" measured were exactly that.
# A line has a "parallel twin" — the shape of a wall drawn somewhere else —
# when the nearest line of the other sheet runs alongside it at least
# NEAR_MISS_MIN_IN and at most NEAR_MISS_FT away.
SAME_LINE_PT = 0.75
SAME_LINE_IN = 1.5
NEAR_MISS_MIN_IN = 3.0
NEAR_MISS_FT = 2.0
DEFAULT_PT_PER_FT = 9.0  # 1/8" = 1'-0", when no scale is known
# Settled only on positive evidence: this many lines of the window line up,
# and on ONE of the two sheets nearly every line has a partner on the other.
# One-sided on purpose: an engineer's plan is a copy of the architect's
# background (measured on JETRIGHT: 87-100% of the electrical sheet's lines in
# a window land on the architectural sheet), while the architect's sheet also
# carries furniture, finishes and text boxes the copy leaves out (33-45%).
SETTLE_MIN_MATCHED = 25
SETTLE_MIN_SHARE = 0.85
MAX_HINTS = 6


@dataclass
class TileView:
    """What the code measured in one window of one pair."""

    pair: int
    tile: int
    blank: str | None = None  # "A" | "B" when that side draws nothing here
    lines_a: int = 0
    lines_b: int = 0
    matched_a: int = 0
    matched_b: int = 0
    near_misses: list[dict] = field(default_factory=list)

    def settled(self) -> str | None:
        if self.blank:
            return f"sheet {self.blank} draws nothing in this area"
        if self.near_misses or min(self.matched_a, self.matched_b) < SETTLE_MIN_MATCHED:
            return None
        share_a = self.matched_a / max(self.lines_a, 1)
        share_b = self.matched_b / max(self.lines_b, 1)
        if max(share_a, share_b) < SETTLE_MIN_SHARE:
            return None
        copy, share = ("B", share_b) if share_b >= share_a else ("A", share_a)
        return (f"sheet {copy} copies the other here: {round(100 * share)}% of its wall lines land exactly on the "
                f"other sheet's, and no wall line of either is drawn 3 in to 2 ft off")

    def as_json(self) -> dict:
        return {"pair": self.pair, "tile": self.tile, "blank": self.blank, "linesA": self.lines_a,
                "linesB": self.lines_b, "matchedA": self.matched_a, "matchedB": self.matched_b,
                "nearMisses": len(self.near_misses)}


@dataclass
class Triage:
    reasons: dict = field(default_factory=dict)  # (pair, tile) -> why the code settled it
    hints: dict = field(default_factory=dict)  # (pair, tile) -> {"known": [...], "measured": [...]}
    views: list = field(default_factory=list)


def ink_share(page: fitz.Page, rect: list[float]) -> float:
    """Share of the window's pixels that carry ink, at a thumbnail size."""
    r = fitz.Rect(rect)
    if r.is_empty:
        return 0.0
    zoom = BLANK_PX / max(r.width, r.height)
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=r, colorspace=fitz.csGRAY, alpha=False)
    samples = pix.samples
    if not samples:
        return 0.0
    return sum(1 for v in samples if v < 200) / len(samples)


def _segments_in(walls, rect: list[float]):
    """(horizontal, vertical) wall segments whose middle is in the window."""
    x0, y0, x1, y1 = rect
    h = [s for s in walls.horizontal if y0 <= s.pos <= y1 and x0 <= (s.lo + s.hi) / 2 <= x1]
    v = [s for s in walls.vertical if x0 <= s.pos <= x1 and y0 <= (s.lo + s.hi) / 2 <= y1]
    return h, v


def _overlap(lo1: float, hi1: float, lo2: float, hi2: float) -> float:
    return max(0.0, min(hi1, hi2) - max(lo1, lo2))


def compare_lines(walls_a, walls_b, wa: list[float], wb: list[float], scale: float, tx: float, ty: float,
                  ptft: float) -> tuple[int, int, int, int, list[dict]]:
    """(lines on A, lines on B, A lines with a partner, B lines with a
    partner, near misses) for one window. Pure: walls are display-space
    wall_match.Walls, and B = scale * A + (tx, ty)."""
    ha, va = _segments_in(walls_a, wa)
    hb, vb = _segments_in(walls_b, wb)
    same = max(SAME_LINE_PT, SAME_LINE_IN / 12 * ptft)
    near_lo, near_hi = max(same, NEAR_MISS_MIN_IN / 12 * ptft), NEAR_MISS_FT * ptft
    matched_a = 0
    matched_b_ids: set[int] = set()
    near: list[dict] = []
    for segs_a, segs_b, horizontal in ((ha, hb, True), (va, vb, False)):
        shift_pos, shift_run = (ty, tx) if horizontal else (tx, ty)
        for s in segs_a:
            pos = scale * s.pos + shift_pos
            lo, hi = scale * s.lo + shift_run, scale * s.hi + shift_run
            length = hi - lo
            exact = [t for t in segs_b if abs(t.pos - pos) <= same
                     and _overlap(lo, hi, t.lo, t.hi) >= 0.6 * min(length, t.hi - t.lo)]
            if exact:
                matched_a += 1
                matched_b_ids.update(id(t) for t in exact)
                continue
            twin = min(
                (t for t in segs_b if near_lo <= abs(t.pos - pos) <= near_hi
                 and _overlap(lo, hi, t.lo, t.hi) >= 0.8 * max(length, t.hi - t.lo)),
                key=lambda t: abs(t.pos - pos), default=None,
            )
            if twin is not None:
                near.append({"horizontal": horizontal, "posA": s.pos, "posOnB": pos, "posB": twin.pos,
                             "offset": twin.pos - pos, "lo": lo, "hi": hi})
    # A twin whose own line has an exact partner is the other face of a wall,
    # not a wall that moved.
    exact_b = {(t.pos, t.lo, t.hi) for segs in (hb, vb) for t in segs if id(t) in matched_b_ids}
    near = [n for n in near if not any(abs(n["posB"] - p) < 1e-6 for p, _, _ in exact_b)]
    return len(ha) + len(va), len(hb) + len(vb), matched_a, len(matched_b_ids), near


def near_miss_rect(n: dict) -> list[float]:
    """The strip between a wall line and its twin, on sheet B (display space):
    where the two drawings put one wall in two places."""
    lo_pos, hi_pos = sorted((n.get("posOnB", n["posB"]), n["posB"]))
    if n["horizontal"]:
        return [n["lo"], lo_pos, n["hi"], hi_pos]
    return [lo_pos, n["lo"], hi_pos, n["hi"]]


def _where(n: dict, wb: list[float]) -> str:
    """A near miss's place, as a share of the window, which is the same on
    both images (they show one area)."""
    w, h = wb[2] - wb[0], wb[3] - wb[1]
    run = (n["lo"] + n["hi"]) / 2
    x, y = (run, n["posB"]) if n["horizontal"] else (n["posB"], run)
    return f"about {round(100 * (x - wb[0]) / w)}% from the left and {round(100 * (y - wb[1]) / h)}% from the top"


def _known_in(findings, pair, wa: list[float], wb: list[float], pages_display) -> list[str]:
    """The subjects of findings whose evidence falls inside this window on
    either sheet — already reported, so the AI must not spend a look on them."""
    out = []
    for finding in findings:
        for e in finding.evidence:
            box = e.get("bbox")
            if not box:
                continue
            for f, w in ((pair.a, wa), (pair.b, wb)):
                if e.get("documentId") != f.document_id or e.get("pageNumber") != f.page_number:
                    continue
                to_display = pages_display.get((f.document_id, f.page_number))
                if to_display is None:
                    continue
                r = fitz.Rect(box["x"], box["y"], box["x"] + box["width"], box["y"] + box["height"]) * to_display
                r.normalize()
                if r.intersects(fitz.Rect(w)) and finding.subject not in out:
                    out.append(finding.subject)
    return out[:MAX_HINTS]


def triage_tiles(pairs, open_page, cache: dict | None = None, findings=()) -> Triage:
    """Which windows the code settles itself, and what it tells the AI about
    the rest. Indices are (pair index in `pairs`, window index), as the plan
    writes its tiles."""
    import wall_match

    cache = {} if cache is None else cache
    walls = cache.setdefault("walls", {})
    out = Triage()
    for i, pair in enumerate(pairs):
        if pair.transform is None:
            continue
        pages = {}
        to_display = {}
        for f in (pair.a, pair.b):
            key = (f.document_id, f.page_number)
            page = open_page(f.document_id, f.page_number)
            pages[key] = page
            if page is not None:
                to_display[key] = page.rotation_matrix
                if key not in walls:
                    walls[key] = wall_match.read_walls(page)
        key_a, key_b = (pair.a.document_id, pair.a.page_number), (pair.b.document_id, pair.b.page_number)
        if pages[key_a] is None or pages[key_b] is None:
            continue
        ptft = _common_scale(pair) or DEFAULT_PT_PER_FT
        t = pair.transform
        for j, (wa, wb) in enumerate(pair.windows):
            view = TileView(i, j)
            if ink_share(pages[key_a], wa) < BLANK_SHARE:
                view.blank = "A"
            elif ink_share(pages[key_b], wb) < BLANK_SHARE:
                view.blank = "B"
            else:
                (view.lines_a, view.lines_b, view.matched_a, view.matched_b,
                 view.near_misses) = compare_lines(walls[key_a], walls[key_b], wa, wb, t.scale, t.tx, t.ty, ptft)
            out.views.append(view)
            why = view.settled()
            if why:
                # One sheet copies the other here (or one is blank): there is
                # no disagreement between drawings for the AI to find, whatever
                # the text checks already reported in this area.
                out.reasons[(i, j)] = why
                continue
            known = _known_in(findings, pair, wa, wb, to_display)
            measured: list[str] = []
            measured_at: list[dict] = []
            for n in view.near_misses:
                line = (f"a {'horizontal' if n['horizontal'] else 'vertical'} wall line {_where(n, wb)} "
                        f"is drawn {_feet(n['offset'], ptft)} apart on the two sheets")
                if line not in measured and len(measured) < MAX_HINTS:
                    measured.append(line)
                    # Where, on sheet B, so a problem the AI raises there can
                    # be recognised as the same one (fullscan_run.settle).
                    measured_at.append({"rect": near_miss_rect(n), "note": line, "ptPerFt": ptft})
            if known or measured:
                out.hints[(i, j)] = {"known": known, "measured": measured, "measuredAt": measured_at}
    return out
