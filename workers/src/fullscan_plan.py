"""Phase 2 of the full AI scan: which sheets to compare, and which windows of
them to show the model together. Pure — page facts in, pairs and tiles out —
so every rule that decides what the model is ever shown is tested without a
PDF or a model.

An RFI is two drawings that should agree and do not. So the AI never looks at
a sheet on its own: it is shown the SAME AREA of two sheets side by side.
Which two is decided here, by code, and the rules are the ones the client's
rejected drafts paid for:

  * Only PLANS are paired. Sections, details, schedules, notes and covers are
    read by the text checks, which already see every page.
  * Both sheets must name ONE level, and the same one. Columns stop, move and
    shrink between floors by design (A3.03 Level 4 against A3.05 Level 6 was
    the first rejected draft), so a page with no readable level, or two levels
    on one sheet, is left out — and the plan screen lists it as left out.
  * `same_level`: two disciplines (architectural and structural, say) drawing
    the same level at a COMMON printed scale. Lined up by grid-line
    POSITIONS, never by grid names — the two disciplines name the same lines
    differently, which is itself the client's RFI 002.
  * `enlarged`: an enlarged plan laid over the plan of the same level that it
    enlarges (RFI 015's shape), lined up by the columns both draw
    (plan_match), one window per aligned detail.
  * A sheet whose grid label appears in two places along its axis is several
    views on one sheet, and its positions are paper-space: never paired by
    grid (S1.102 against A3.36 was the second rejected draft).
  * A pair that cannot be lined up is left out, never guessed: the same area
    of two sheets is the whole premise of what the model is asked.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field

from sheet_facts import PageFacts

# What one tile image is rendered at. The whole-sheet render measured 61-73
# DPI and misread member sizes (8X8 as 6X6); a close-up crop at ~990 DPI read
# every one. 150 DPI on a 1568px image is a ~750pt window: legible text, and a
# sheet's drawing area in about six tiles rather than sixty.
TILE_DPI = float(os.environ.get("FULL_SCAN_TILE_DPI", "150"))
TILE_EDGE = int(os.environ.get("FULL_SCAN_TILE_EDGE", "1568"))
# Neighbouring tiles overlap so an element on a seam is whole in one of them.
TILE_OVERLAP = 0.12
MAX_PAIRS = int(os.environ.get("FULL_SCAN_MAX_PAIRS", "300"))
MAX_TILES = int(os.environ.get("FULL_SCAN_MAX_TILES", "3000"))
# Two printed scales are the same scale within this fraction (rfi_grid).
SCALE_TOL = 0.02
# An enlarged plan is at least this many times the scale of its overall plan.
MIN_ENLARGEMENT = 1.5

# Token model for the estimate. An image of TILE_EDGE costs about this many
# input tokens on either provider at these sizes; the words printed in the
# windows and the instructions are the rest.
IMAGE_TOKENS = 1600
SYSTEM_TOKENS = 1800
WORDS_TOKENS = 500
DISCOVERY_OUTPUT = 700
VERIFY_INPUT = 2 * IMAGE_TOKENS + SYSTEM_TOKENS + 600
VERIFY_OUTPUT = 600
# What share of tiles the first look flags, as a range: the verify calls are
# not known until it has run.
VERIFY_SHARE = (0.05, 0.35)


def tile_pt() -> float:
    """The window, in points of the sheet being tiled, one tile image covers."""
    return TILE_EDGE * 72.0 / TILE_DPI


@dataclass
class Transform:
    """Display points of sheet A -> display points of sheet B."""

    scale: float
    tx: float
    ty: float

    def forward(self, x: float, y: float) -> tuple[float, float]:
        return self.scale * x + self.tx, self.scale * y + self.ty

    def rect(self, r: list[float]) -> list[float]:
        x0, y0 = self.forward(r[0], r[1])
        x1, y1 = self.forward(r[2], r[3])
        return [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)]

    def as_json(self) -> dict:
        return {"scale": round(self.scale, 6), "tx": round(self.tx, 2), "ty": round(self.ty, 2)}


@dataclass
class Pair:
    kind: str  # same_level | enlarged
    a: PageFacts
    b: PageFacts
    reason: str
    transform: Transform | None = None
    # [(window on A, window on B)], display points.
    windows: list[tuple[list[float], list[float]]] = field(default_factory=list)

    def tiles(self) -> list[dict]:
        return [
            {
                "a": {"documentId": self.a.document_id, "pageNumber": self.a.page_number, "rect": [round(v, 2) for v in wa]},
                "b": {"documentId": self.b.document_id, "pageNumber": self.b.page_number, "rect": [round(v, 2) for v in wb]},
            }
            for wa, wb in self.windows
        ]


# --- Which sheets ----------------------------------------------------------------------


def share_a_scale(a: list[float], b: list[float]) -> bool:
    return any(abs(x - y) <= SCALE_TOL * max(x, y) for x in a for y in b)


def enlargement(a: list[float], b: list[float]) -> float | None:
    """How many times larger A is drawn than B (its plan scale over B's), or
    None when no pair of printed scales makes A an enlargement of B. The
    SMALLEST scale on each sheet is its plan; larger ones are its details."""
    if not a or not b:
        return None
    ratio = min(a) / min(b)
    return ratio if ratio >= MIN_ENLARGEMENT else None


def _why_left_out(f: PageFacts) -> str | None:
    if f.kind not in ("plan", "enlarged_plan"):
        return None  # not a plan: covered by the text checks, not "left out"
    if not f.level:
        return "plan whose drawing title names no single level"
    if not f.scales:
        return "plan with no printed drawing scale"
    # Only a real grid can say "two views": E4.01 read one stray bubble on one
    # axis and two on the other, and was left out of a pair its walls line up.
    if f.grid.get("repeats") and len(f.grid.get("x") or {}) >= 2 and len(f.grid.get("y") or {}) >= 2:
        return "sheet with several views (a grid label in two places)"
    return None


# The disciplines whose plans GOVERN where things are: walls, columns, slab
# edges. Every other discipline draws its design over a copy of one of these.
GOVERNING = frozenset({"architectural", "structural", "interiors"})


def governs(a: PageFacts, b: PageFacts) -> bool:
    """Whether comparing a and b can find a real disagreement rather than two
    copies of a background disagreeing.

    An electrical plan and a plumbing plan share nothing but the architectural
    floor plan each consultant copied under its own design — and each copy is
    exported, halftoned and trimmed differently. JETRIGHT's E2.02 against
    P1.03 produced "the mezzanine floor edge is uniform on one and stepped on
    the other": pale background linework, compared as if it were design. So a
    pair needs one sheet that governs the geometry (each consultant plan is
    still compared with the architectural or structural plan of its level),
    or two sheets of ONE discipline (an enlarged plan with its own overall)."""
    return a.discipline == b.discipline or a.discipline in GOVERNING or b.discipline in GOVERNING


def _why_unpaired(f: PageFacts, same_level: list[PageFacts]) -> str:
    """Why a plan WITH a level and a scale still found no partner — said as
    the thing to fix. One line ("no other sheet of its level") used to cover
    three causes with three different fixes."""
    if not f.discipline:
        return "plan whose sheet number gave no discipline (mark the title-block region so sheet numbers are read)"
    others = [g for g in same_level if g is not f and g.discipline and g.discipline != f.discipline]
    if not others:
        return "plan with no plan of another discipline on the same level"
    if not any(governs(f, g) for g in others):
        return ("plan whose level has no architectural or structural plan to compare against "
                "(two consultants' plans share only the background each copied)")
    return "plan with no printed scale in common with the other discipline's plan of its level"


def kept_out_note(excluded: dict[str, int], read: int,
                  excluded_documents: list[tuple[str, str | None]] = ()) -> str | None:
    """What to do when pages were kept out of the scan, by reason, or None.
    Pure. A new project planned its scan while its one document was still
    processing, and the screen said "Read 0 pages … 103 kept out (old
    revisions, RFIs, unprocessed)" — three causes with three different fixes,
    and nothing to say which.

    `excluded_documents` is (filename, stored reason) for each document kept
    out of RFI analysis. The count alone sent the next person hunting: a drawing
    set whose name and first pages do not look like an RFI was off, and only its
    stored reason ("by a person", "filename", "form text") says who switched it."""
    parts = []
    if excluded.get("notProcessed"):
        parts.append(f"{excluded['notProcessed']} page(s) belong to a document still processing — wait until the "
                     "Docs tab shows it completed, then close this plan and plan again")
    if excluded.get("excludedFromRfi"):
        named = "; ".join(f'"{name}" ({reason or "no reason recorded"})' for name, reason in excluded_documents[:3])
        more = f" and {len(excluded_documents) - 3} more" if len(excluded_documents) > 3 else ""
        which = f" — {named}{more}" if named else ""
        parts.append(f"{excluded['excludedFromRfi']} page(s) belong to a document excluded from RFI analysis{which}. "
                     "If it is a drawing set rather than an issued RFI, tick \"RFI review input\" for it in the Docs "
                     "tab, then close this plan and plan again")
    if excluded.get("superseded"):
        parts.append(f"{excluded['superseded']} page(s) are old revisions replaced by a newer upload (correct to leave out)")
    if not parts:
        return None
    lead = "No page could be read: " if read == 0 else "Kept out of this scan: "
    return lead + "; ".join(parts) + "."


def no_pairs_note(skipped: dict[str, list[str]], top: int = 3) -> str:
    """What the plan says when NOTHING could be paired: the rule, then the
    biggest reasons with their counts, in the red box itself. Pure. The reasons
    used to sit behind a collapsed "Left out (44)", so the box read as a dead
    end on every set that named its floors in words the reader did not know."""
    ranked = sorted(skipped.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:top]
    lines = [
        "No two sheets could be paired and lined up, so there is nothing for the AI to compare. "
        "The full scan compares plans of the same level from two disciplines, or an enlarged plan with its "
        "overall plan."
    ]
    if ranked:
        lines.append("Most were left out because: " + "; ".join(f"{why} ({len(labels)})" for why, labels in ranked) + ".")
    return " ".join(lines)


def candidate_pairs(facts: list[PageFacts]) -> tuple[list[Pair], dict[str, list[str]]]:
    """(pairs to line up, {reason: [sheet labels]} for every plan left out).

    Deterministic order: by level, then by kind, then by sheet."""
    skipped: dict[str, list[str]] = {}
    plans: list[PageFacts] = []
    for f in facts:
        why = _why_left_out(f)
        if why:
            skipped.setdefault(why, []).append(f.label)
        elif f.kind in ("plan", "enlarged_plan"):
            plans.append(f)

    by_level: dict[str, list[PageFacts]] = {}
    for f in plans:
        by_level.setdefault(f.level, []).append(f)

    pairs: list[Pair] = []
    paired: set[str] = set()
    for level in sorted(by_level):
        sheets = sorted(by_level[level], key=lambda f: (f.discipline or "", f.label))
        overall = [f for f in sheets if f.kind == "plan"]
        enlarged = [f for f in sheets if f.kind == "enlarged_plan"]
        for i, a in enumerate(overall):
            for b in overall[i + 1 :]:
                if not a.discipline or not b.discipline or a.discipline == b.discipline:
                    continue
                if not governs(a, b):
                    continue
                if not share_a_scale(a.scales, b.scales):
                    continue
                pairs.append(Pair(
                    "same_level", a, b,
                    f"{level.title()}: the {a.discipline} and {b.discipline} plans, both at a common scale",
                ))
                paired |= {a.page_id, b.page_id}
        # An enlarged plan is a sheet TITLED enlarged, against any overall
        # plan of its level; or an untitled plan drawn at 2x or more of a plan
        # of its OWN discipline. Two disciplines' overall plans at 1/8" and
        # 3/16" are not an enlargement of each other.
        for a in enlarged + overall:
            for b in overall:
                if b is a:
                    continue
                ratio = enlargement(a.scales, b.scales)
                if ratio is None:
                    continue
                if a.kind == "plan" and (ratio < 2 or a.discipline != b.discipline):
                    continue
                if not governs(a, b):
                    continue
                pairs.append(Pair(
                    "enlarged", a, b,
                    f"{level.title()}: {a.label} enlarges part of {b.label} ({ratio:g}x)",
                ))
                paired |= {a.page_id, b.page_id}

    for f in plans:
        if f.page_id not in paired:
            skipped.setdefault(_why_unpaired(f, by_level[f.level]), []).append(f.label)
    if len(pairs) > MAX_PAIRS:
        skipped.setdefault(f"pair beyond the first {MAX_PAIRS} (FULL_SCAN_MAX_PAIRS)", []).extend(
            f"{p.a.label} / {p.b.label}" for p in pairs[MAX_PAIRS:]
        )
        pairs = pairs[:MAX_PAIRS]
    return pairs, skipped


# --- Lining up ------------------------------------------------------------------------


def grid_transform(a: PageFacts, b: PageFacts) -> Transform | None:
    """A -> B at scale 1 from grid-line POSITIONS on both axes (rfi_grid.align:
    unambiguous translation, names ignored). None unless both axes agree."""
    import rfi_grid

    ax, ay = a.grid.get("x") or {}, a.grid.get("y") or {}
    bx, by = b.grid.get("x") or {}, b.grid.get("y") or {}
    if a.grid.get("repeats") or b.grid.get("repeats"):
        return None
    on_x, on_y = _align_axis(ax, bx), _align_axis(ay, by)
    if on_x is None or on_y is None:
        return None
    return Transform(1.0, on_x, on_y)


def _primary(axis: dict[str, float]) -> dict[str, float]:
    """The main grid lines: no secondary label like C.1 or B1.5."""
    return {k: v for k, v in axis.items() if "." not in k}


def _align_axis(a: dict[str, float], b: dict[str, float]) -> float | None:
    """The offset that lays one axis of A onto B, by position. Dense secondary
    lines (A3.01 draws B.1 to B.6 between two primaries) let several offsets
    match almost equally well, and rfi_grid.align rightly refuses that; the
    primary lines alone then decide, under the same unambiguity rule."""
    import rfi_grid

    if not a or not b:
        return None
    found = rfi_grid.align(a, b)
    if found is None:
        pa, pb = _primary(a), _primary(b)
        found = rfi_grid.align(pa, pb) if len(pa) >= 3 and len(pb) >= 3 else None
    return found.offset if found else None


def grid_extent(f: PageFacts) -> list[float] | None:
    """The drawing area: the grid's outermost lines, padded — slab edges and
    cantilevers sit outside the last grid line — and kept on the page."""
    xs = list((f.grid.get("x") or {}).values())
    ys = list((f.grid.get("y") or {}).values())
    w, h = (f.grid.get("size") or [0, 0])[:2]
    if len(xs) < 2 or len(ys) < 2 or not w or not h:
        return None
    pad_x = max(36.0, 0.08 * (max(xs) - min(xs)))
    pad_y = max(36.0, 0.08 * (max(ys) - min(ys)))
    return [max(0.0, min(xs) - pad_x), max(0.0, min(ys) - pad_y), min(w, max(xs) + pad_x), min(h, max(ys) + pad_y)]


def _intersect(r: list[float], s: list[float]) -> list[float] | None:
    out = [max(r[0], s[0]), max(r[1], s[1]), min(r[2], s[2]), min(r[3], s[3])]
    return out if out[2] - out[0] > 1 and out[3] - out[1] > 1 else None


def split(area: list[float], size: float, overlap: float = TILE_OVERLAP) -> list[list[float]]:
    """`area` cut into windows no larger than `size` on a side, overlapping
    their neighbours by `overlap` of a window, covering it exactly."""
    x0, y0, x1, y1 = area
    nx = max(1, math.ceil((x1 - x0) / (size * (1 - overlap))))
    ny = max(1, math.ceil((y1 - y0) / (size * (1 - overlap))))
    w = min(size, x1 - x0)
    h = min(size, y1 - y0)
    out = []
    for j in range(ny):
        for i in range(nx):
            left = x0 + (x1 - x0 - w) * (i / (nx - 1) if nx > 1 else 0)
            top = y0 + (y1 - y0 - h) * (j / (ny - 1) if ny > 1 else 0)
            out.append([left, top, left + w, top + h])
    return out


def windows_for(a_area: list[float], transform: Transform, b_size: list[float], size: float) -> list[tuple[list[float], list[float]]]:
    """Tiles of A's area, each paired with the same area of B. A tile whose
    counterpart falls off sheet B is clipped on both sides together, so the
    two images always show one area."""
    out = []
    page_b = [0.0, 0.0, b_size[0], b_size[1]]
    for wa in split(a_area, size):
        wb = _intersect(transform.rect(wa), page_b)
        if wb is None:
            continue
        # Pull A back to exactly what B could show.
        inv = Transform(1 / transform.scale, -transform.tx / transform.scale, -transform.ty / transform.scale)
        wa2 = _intersect(inv.rect(wb), wa)
        if wa2 is None or (wa2[2] - wa2[0]) < 0.25 * size or (wa2[3] - wa2[1]) < 0.25 * size:
            continue  # a sliver at the edge of the overlap shows nothing whole
        out.append((wa2, wb))
    return out


def same_level_windows(pair: Pair) -> tuple[list[tuple[list[float], list[float]]], str | None]:
    """Tiles of the area both sheets draw, or ([], why not)."""
    transform = grid_transform(pair.a, pair.b)
    if transform is None:
        return [], "the two grids could not be lined up by position"
    ea, eb = grid_extent(pair.a), grid_extent(pair.b)
    if ea is None or eb is None:
        return [], "no drawing area could be read from the grid"
    inv = Transform(1.0, -transform.tx, -transform.ty)
    shared = _intersect(ea, inv.rect(eb))
    if shared is None:
        return [], "the two sheets draw different areas of the level"
    pair.transform = transform
    return windows_for(shared, transform, pair.b.grid.get("size") or [0, 0], tile_pt()), None


WALL_PAD_PT = 36.0


def wall_windows(pair: Pair, shift) -> tuple[list[tuple[list[float], list[float]]], str | None]:
    """Tiles of a same-level pair lined up by the walls both draw
    (wall_match.WallShift), for sheets with no grid to line up by. The area is
    the box of the walls that matched, padded — what the two sheets share."""
    if shift is None:
        return [], "no grid to line up by, and the walls the two sheets draw do not line up either"
    a_size = pair.a.grid.get("size") or [0, 0]
    b_size = pair.b.grid.get("size") or [0, 0]
    if not a_size[0] or not b_size[0]:
        return [], "sheet size unknown"
    e = shift.extent
    area = _intersect([e[0] - WALL_PAD_PT, e[1] - WALL_PAD_PT, e[2] + WALL_PAD_PT, e[3] + WALL_PAD_PT],
                      [0.0, 0.0, a_size[0], a_size[1]])
    if area is None:
        return [], "the walls that line up fall off the sheet"
    transform = Transform(1.0, shift.tx, shift.ty)
    windows = windows_for(area, transform, b_size, tile_pt())
    if not windows:
        return [], "the walls that line up fall off the sheet"
    pair.transform = transform
    pair.reason = f"{pair.reason}; lined up by the walls both draw ({shift.matched} wall lines match), not by a grid"
    return windows, None


def enlarged_windows(
    pair: Pair, alignments, detail_boxes: dict[int, list[float]] | None = None
) -> tuple[list[tuple[list[float], list[float]]], str | None]:
    """One set of tiles per detail of the enlarged sheet that lined up with
    the overall plan (plan_match.Alignment: B = scale * A + t).

    A detail's window is its own drawn region (`detail_boxes`, the ink region
    plan_match segmented it from) when known — padding around the columns
    alone ran into A3.35's title block, which the overall plan has no
    counterpart for and the model would report as "missing". Without a region
    it falls back to the columns' extent, padded."""
    if not alignments:
        return [], "no detail of the enlarged sheet lined up with the plan by the columns both draw"
    out = []
    size = tile_pt()
    b_size = pair.b.grid.get("size") or [0, 0]
    if not b_size[0]:
        return [], "sheet size unknown"
    for al in alignments:
        members = al.members or [a for a, _ in al.inliers]
        if not members:
            continue
        pad = max(48.0, max(max(e.w, e.h) for e in members) * 2)
        area = [
            min(e.cx - e.w / 2 for e in members) - pad, min(e.cy - e.h / 2 for e in members) - pad,
            max(e.cx + e.w / 2 for e in members) + pad, max(e.cy + e.h / 2 for e in members) + pad,
        ]
        box = (detail_boxes or {}).get(al.detail)
        if box:
            area = _intersect(area, box) or box
        a_size = pair.a.grid.get("size") or [0, 0]
        if a_size[0]:
            area = _intersect(area, [0.0, 0.0, a_size[0], a_size[1]]) or area
        transform = Transform(al.scale, al.tx, al.ty)
        pair.transform = pair.transform or transform
        out += windows_for(area, transform, b_size, size)
    return out, (None if out else "the aligned details fall off the overall plan")


# --- The estimate -----------------------------------------------------------------------


def estimate(tiles: int) -> dict:
    """Tokens for `tiles` discovery calls of two images each, plus a RANGE of
    close-up verification calls. The API prices it; the worker never guesses
    a price."""
    low = math.ceil(tiles * VERIFY_SHARE[0])
    high = math.ceil(tiles * VERIFY_SHARE[1])
    return {
        "calls": tiles,
        "images": tiles * 2,
        "inputTokens": tiles * (2 * IMAGE_TOKENS + SYSTEM_TOKENS + WORDS_TOKENS),
        "outputTokens": tiles * DISCOVERY_OUTPUT,
        "verifyCalls": {"low": low, "high": high},
        "verifyInputTokens": {"low": low * VERIFY_INPUT, "high": high * VERIFY_INPUT},
        "verifyOutputTokens": {"low": low * VERIFY_OUTPUT, "high": high * VERIFY_OUTPUT},
    }


def skipped_list(skipped: dict[str, list[str]]) -> list[dict]:
    """RfiFullScanDto.skipped: grouped, biggest group first, a few examples."""
    return sorted(
        ({"reason": reason, "count": len(labels), "examples": labels[:6]} for reason, labels in skipped.items()),
        key=lambda s: -s["count"],
    )
