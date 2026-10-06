"""Line two plans up by the walls they both draw, for sheets with no grid.

The full scan lines a same-level pair up by its grid lines (rfi_grid.align).
Electrical and life-safety plans often draw no grid at all, and a 103-page
client set (JETRIGHT, Oct 2026) paired fifteen plans and lined up none. Two
earlier attempts failed on that set: raster ink matching found shifts that put
"OFFICE 104" in different places, and room labels as anchors agreed in only one
or two of nine votes. Both are WRONG answers rather than no answer, and a wrong
alignment is a confident false finding.

What does hold is that an engineer's plan is drawn over the architect's floor
plan as an exact copy (an xref): the same wall line, at the same length, shifted
by one constant. So this reads every horizontal and vertical line segment of
both sheets (display space) and lets each pair of EQUAL-LENGTH segments vote for
the shift between them. A copied background produces one sharp peak — on
A1.01 against E2.01, 888 of the electrical sheet's 1383 segments land within
0.6pt, against 39 at the best other shift. Room labels do not: the electrical
drafter moves them, which is why the label method failed.

Translation only, at scale 1, like rfi_grid: the pair already prints a common
scale, and a different drawing scale is a missed finding, never a forced one.
The rules for accepting a shift are the ones that separate a copy from a
coincidence, and every number below is from that set:

  * enough walls land EXACTLY (MIN_MATCHED segments, within MATCH_TOL);
  * they are a real share of the smaller sheet's segments (MIN_SHARE);
  * the best shift beats every other one by MIN_RATIO — a regular wall
    module lines up with a copy of itself shifted one bay, as a grid does.

Every sheet of a set shares its border and title block at the same place, so
those lines vote for a zero shift on ANY two sheets. Measured on that set they
are 29 segments, half of MIN_MATCHED; a real copied background landed 670-965.
"""

from __future__ import annotations

import collections
from dataclasses import dataclass

MIN_LENGTH_PT = 15.0  # shorter strokes are hatching, text and symbols
AXIS_TOL_PT = 0.05  # a horizontal segment's two ends share one y within this
LENGTH_BIN_PT = 1.0  # equal length, rounded to this
MATCH_TOL = 0.6  # a segment "lands" when both ends are this close after the shift
MIN_MATCHED = 60
MIN_SHARE = 0.15
MIN_RATIO = 4.0
NEAR_PT = 3.0  # votes within this of the peak are the same shift, split by rounding
MAX_SEGMENTS = 20000  # per axis; a sheet beyond this is hatch, and voting would be quadratic


@dataclass(frozen=True)
class Segment:
    pos: float  # y for a horizontal segment, x for a vertical one
    lo: float
    hi: float


@dataclass
class Walls:
    horizontal: list[Segment]
    vertical: list[Segment]

    def count(self) -> int:
        return len(self.horizontal) + len(self.vertical)


@dataclass
class WallShift:
    tx: float  # B = A + (tx, ty), display points
    ty: float
    matched: int  # B segments that land on an A segment
    share: float  # matched / the smaller sheet's segment count
    runner_up: int  # matched at the best shift more than NEAR_PT away
    extent: list[float]  # on A: the box of the segments that matched


def read_walls(page) -> Walls:
    """Every long horizontal and vertical line of one fitz page, in display
    space, markup stripped. Rectangles count as their four edges (walls are
    often drawn as filled boxes)."""
    import grid

    clean = grid.without_markup(page)
    to_display = page.rotation_matrix
    horizontal, vertical = set(), set()
    for drawing in clean.get_drawings():
        for item in drawing["items"]:
            if item[0] == "l":
                edges = [(item[1], item[2])]
            elif item[0] == "re":
                r = item[1]
                edges = [(r.tl, r.tr), (r.tr, r.br), (r.br, r.bl), (r.bl, r.tl)]
            else:
                continue
            for p, q in edges:
                p, q = p * to_display, q * to_display
                if abs(p.y - q.y) < AXIS_TOL_PT and abs(p.x - q.x) >= MIN_LENGTH_PT:
                    horizontal.add(Segment(round(p.y, 2), round(min(p.x, q.x), 2), round(max(p.x, q.x), 2)))
                elif abs(p.x - q.x) < AXIS_TOL_PT and abs(p.y - q.y) >= MIN_LENGTH_PT:
                    vertical.add(Segment(round(p.x, 2), round(min(p.y, q.y), 2), round(max(p.y, q.y), 2)))
    return Walls(sorted(horizontal, key=lambda s: (s.pos, s.lo)), sorted(vertical, key=lambda s: (s.pos, s.lo)))


def _by_length(segments: list[Segment]) -> dict[int, list[Segment]]:
    out: dict[int, list[Segment]] = collections.defaultdict(list)
    for s in segments:
        out[round((s.hi - s.lo) / LENGTH_BIN_PT)].append(s)
    return out


def _votes(a: Walls, b: Walls) -> collections.Counter:
    """(dx, dy) rounded to a point -> how many equal-length segment pairs
    propose it. A horizontal pair fixes dy by its position and dx by its start;
    a vertical pair the other way round."""
    votes: collections.Counter = collections.Counter()
    for segs_a, segs_b, horizontal in ((a.horizontal, b.horizontal, True), (a.vertical, b.vertical, False)):
        index = _by_length(segs_b)
        for s in segs_a:
            length = round((s.hi - s.lo) / LENGTH_BIN_PT)
            for k in (length - 1, length, length + 1):
                for t in index.get(k, ()):
                    d_pos, d_lo = round(t.pos - s.pos), round(t.lo - s.lo)
                    votes[(d_lo, d_pos) if horizontal else (d_pos, d_lo)] += 1
    return votes


def _landed(a: Walls, b: Walls, tx: float, ty: float) -> tuple[int, list[Segment], list[Segment]]:
    """How many of B's segments an A segment lands on, after A + (tx, ty),
    and which A segments did the landing (horizontal, vertical)."""
    total = 0
    hit_h: list[Segment] = []
    hit_v: list[Segment] = []
    for segs_a, segs_b, horizontal, hits in (
        (a.horizontal, b.horizontal, True, hit_h), (a.vertical, b.vertical, False, hit_v)
    ):
        cells: dict[int, list[Segment]] = collections.defaultdict(list)
        for t in segs_b:
            cells[round(t.pos)].append(t)
        shift_pos, shift_run = (ty, tx) if horizontal else (tx, ty)
        used: set[Segment] = set()
        for s in segs_a:
            pos, lo, hi = s.pos + shift_pos, s.lo + shift_run, s.hi + shift_run
            for cell in (round(pos) - 1, round(pos), round(pos) + 1):
                found = next(
                    (t for t in cells.get(cell, ()) if t not in used and abs(t.pos - pos) <= MATCH_TOL
                     and abs(t.lo - lo) <= MATCH_TOL and abs(t.hi - hi) <= MATCH_TOL),
                    None,
                )
                if found is not None:
                    used.add(found)
                    hits.append(s)
                    break
        total += len(used)
    return total, hit_h, hit_v


def align(a: Walls, b: Walls) -> WallShift | None:
    """The one shift that lays A's walls onto B's, or None. Pure."""
    if min(a.count(), b.count()) < MIN_MATCHED:
        return None
    if max(len(a.horizontal), len(a.vertical), len(b.horizontal), len(b.vertical)) > MAX_SEGMENTS:
        return None
    votes = _votes(a, b)
    if not votes:
        return None
    ranked = votes.most_common(400)
    best = ranked[0][0]
    rival = next((k for k, _ in ranked if abs(k[0] - best[0]) > NEAR_PT or abs(k[1] - best[1]) > NEAR_PT), None)

    # The rounded peak is within a point; refine to the median exact offset of
    # the pairs that voted for it, so MATCH_TOL can be tight.
    dxs, dys = [], []
    for segs_a, segs_b, horizontal in ((a.horizontal, b.horizontal, True), (a.vertical, b.vertical, False)):
        index = _by_length(segs_b)
        for s in segs_a:
            for t in index.get(round((s.hi - s.lo) / LENGTH_BIN_PT), ()):
                d_pos, d_lo = t.pos - s.pos, t.lo - s.lo
                dx, dy = (d_lo, d_pos) if horizontal else (d_pos, d_lo)
                if abs(dx - best[0]) <= 1.5 and abs(dy - best[1]) <= 1.5:
                    dxs.append(dx)
                    dys.append(dy)
    if not dxs:
        return None
    tx, ty = sorted(dxs)[len(dxs) // 2], sorted(dys)[len(dys) // 2]

    matched, hit_h, hit_v = _landed(a, b, tx, ty)
    runner_up = _landed(a, b, float(rival[0]), float(rival[1]))[0] if rival else 0
    share = matched / min(a.count(), b.count())
    if matched < MIN_MATCHED or share < MIN_SHARE or matched < MIN_RATIO * max(runner_up, 1):
        return None
    xs = [v for s in hit_h for v in (s.lo, s.hi)] + [s.pos for s in hit_v]
    ys = [s.pos for s in hit_h] + [v for s in hit_v for v in (s.lo, s.hi)]
    return WallShift(round(tx, 2), round(ty, 2), matched, round(share, 3), runner_up,
                     [min(xs), min(ys), max(xs), max(ys)])
