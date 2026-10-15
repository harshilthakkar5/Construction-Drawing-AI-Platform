"""Sections cut through ONE element must agree — Leland BIM RFI 007: S8.13
cuts sections 3/S4.00 and 4/S4.00 through the same elevator pit, both titled
"ELEVATOR PIT DETAIL", and one draws the pit slab 8" thick where the other
draws it 1'-4". No plan pair shows that: the two drawings that disagree sit
side by side on one detail sheet.

Code decides WHICH two drawings must agree; the full scan's AI only compares
them (fullscan_run, pair kind "sections"). Two details agree-worthy when:
  * they are on one sheet under the SAME title ("ELEVATOR PIT DETAIL"); and
  * one other sheet calls both out within CALLOUT_NEAR_FT of each other at
    that sheet's printed scale — two cuts through the same element.
The title alone is not enough: "GRADE BEAM AT CONCRETE WALL" twice on a sheet
is one standard condition drawn at two different walls, and those may differ
on purpose. Pure but for `find`, which reads pages.
"""

from __future__ import annotations

import itertools
import re
import statistics
from dataclasses import dataclass

# Two callouts this close, in DRAWN feet at the calling plan's scale, cut one
# element. In points it was wrong both ways: RFI 007's two cuts are 81pt apart
# on a 3/8" plan (3 ft), while edge details called out along a slab edge on a
# 3/32" plan sit 100pt apart at different places (15 ft) — 30 false pairs.
CALLOUT_NEAR_FT = 5.0
TITLE_GAP_PT = 30.0   # a detail title sits just above its SCALE line
NUMBER_GAP_PT = 70.0  # the detail number bubble sits just left of the title
CALLOUT_GAP_PT = 14.0  # a callout's number sits just above its sheet number
MAX_PAIRS = 40        # per project: more means the titles are generic, not one element

_SCALE = re.compile(r"^SCALE\b", re.I)
_NUMBER = re.compile(r"^(?:\d{1,3}|[A-Z]\d{0,2})$")
_TITLE = re.compile(r"^[A-Z0-9][A-Z0-9 &/.,'\"()-]*[A-Z]")

Rect = tuple[float, float, float, float]


@dataclass(frozen=True)
class Detail:
    number: str
    title: str
    anchor: Rect  # number + title + scale line


def detail_titles(lines: list[tuple[Rect, str]]) -> list[Detail]:
    """Each detail's number and title, read off the lines above its SCALE
    line (the CAD convention: number bubble, title, then "SCALE: ..."). Pure."""
    out = []
    for scale_box, text in lines:
        if not _SCALE.match(text):
            continue
        sx0, sy0, sx1, sy1 = scale_box
        # Title lines stacked above the scale line, left edges aligned.
        above = sorted((b for b, t in lines if t and abs(b[0] - sx0) <= 12 and b[3] <= sy0 + 2
                        and sy0 - b[3] <= TITLE_GAP_PT * 3 and _TITLE.match(t) and not _SCALE.match(t)),
                       key=lambda b: -b[3])
        title_boxes, bottom = [], sy0
        for b in above:
            if bottom - b[3] > TITLE_GAP_PT:
                break
            title_boxes.append(b)
            bottom = b[1]
        if not title_boxes:
            continue
        texts = {b: t for b, t in lines}
        title = " ".join(texts[b] for b in reversed(title_boxes)).strip()
        top = min(b[1] for b in title_boxes)
        numbers = [(b, t) for b, t in lines if _NUMBER.match(t.strip()) and b[2] <= sx0 + 2
                   and sx0 - b[2] <= NUMBER_GAP_PT and b[1] < sy1 and b[3] > top]
        if not numbers:
            continue
        nb, number = max(numbers, key=lambda bt: bt[0][2])
        out.append(Detail(number.strip(), " ".join(title.split()),
                          (min(nb[0], sx0), min(top, nb[1]), max(sx1, *(b[2] for b in title_boxes)), sy1)))
    return out


def cells(details: list[Detail], page: Rect) -> dict[str, Rect]:
    """The drawing area of each detail: the cell above-right of its title,
    bounded by the next title to its right and the title above it (the page
    top for the top row). Pure."""
    if not details:
        return {}
    xs = sorted({round(d.anchor[0]) for d in details})
    gaps_x = [b - a for a, b in zip(xs, xs[1:]) if b - a > 100]
    col_w = statistics.median(gaps_x) if gaps_x else (page[2] - page[0]) / 3
    row_h = 2 * TITLE_GAP_PT * 3  # titles within this height of each other share a row
    out = {}
    for d in details:
        x0, _, _, y1 = d.anchor
        right = [o.anchor[0] for o in details if o is not d and o.anchor[0] > x0 + 20 and abs(o.anchor[3] - y1) < row_h / 2]
        above = [o.anchor[3] for o in details if o is not d and o.anchor[3] < y1 - 20 and abs(o.anchor[0] - x0) < col_w / 2]
        out[d.number] = (max(page[0], x0 - 20), (max(above) + 10) if above else page[1] + 0.02 * (page[3] - page[1]),
                         min(page[2], (min(right) if right else x0 + col_w) - 20), min(page[3], y1 + 10))
    return out


def callouts(words: list[tuple[Rect, str]], sheet: str) -> list[tuple[str, float, float]]:
    """(detail number, x, y) of each callout to `sheet`: a number printed just
    above the sheet number, centred on it (the "3 / S4.00" bubble). Pure."""
    want = re.sub(r"[^A-Z0-9.]", "", sheet.upper())
    out = []
    sheets = [b for b, t in words if re.sub(r"[^A-Z0-9.]", "", t.upper()) == want]
    for b, t in words:
        if not _NUMBER.match(t):
            continue
        cx = (b[0] + b[2]) / 2
        for s in sheets:
            if -3 <= s[1] - b[3] <= CALLOUT_GAP_PT and abs((s[0] + s[2]) / 2 - cx) <= 12:
                out.append((t, cx, (b[1] + b[3]) / 2))
                break
    return out


def same_cuts(details: list[Detail], calls: list[tuple[str, float, float]], near_pt: float) -> list[tuple[Detail, Detail]]:
    """Pairs of same-title details that one plan calls out within `near_pt`
    of each other. Pure."""
    by_number = {d.number: d for d in details}
    pairs = []
    for a, b in itertools.combinations(sorted(by_number.values(), key=lambda d: d.number), 2):
        if a.title != b.title:
            continue
        near = any(ca[0] == a.number and cb[0] == b.number
                   and ((ca[1] - cb[1]) ** 2 + (ca[2] - cb[2]) ** 2) ** 0.5 <= near_pt
                   for ca in calls for cb in calls)
        if near:
            pairs.append((a, b))
    return pairs


def _lines(page) -> list[tuple[Rect, str]]:
    import plan_match

    return [((r.x0, r.y0, r.x1, r.y1), t) for r, t in plan_match.text_lines(page)]


def find(facts, texts: dict[str, str], open_page) -> list:
    """Section pairs for the full scan's plan (fullscan_plan.Pair, kind
    "sections"). `texts` is each page's indexed text, used to open only the
    sheets that can matter: a sheet with two titles over SCALE lines, and the
    pages that name it."""
    import fitz
    import fullscan_plan as plan_rules
    import plan_match

    by_sheet = {f.sheet_number: f for f in facts if f.sheet_number}
    out = []
    for sheet, f in sorted(by_sheet.items()):
        if len(re.findall(r"\bSCALE\b", texts.get(f.page_id, ""), re.I)) < 2:
            continue
        page = open_page(f.document_id, f.page_number)
        if page is None:
            continue
        every = detail_titles(_lines(page))
        counts: dict[str, int] = {}
        for d in every:
            counts[d.title] = counts.get(d.title, 0) + 1
        details = [d for d in every if counts[d.title] >= 2]
        if not details:
            continue
        r = page.rect * page.rotation_matrix
        cell = cells(every, (r.x0, r.y0, r.x1, r.y1))
        seen = set()
        for g in facts:
            if g.page_id == f.page_id or sheet not in texts.get(g.page_id, ""):
                continue
            plan = open_page(g.document_id, g.page_number)
            if plan is None:
                continue
            # The cautious scale when a sheet prints several: fewest points a foot.
            scales = g.scales or plan_match.page_scales(plan)
            if not scales:
                continue  # no printed scale: "the same place" cannot be measured
            m = plan.rotation_matrix  # get_text is unrotated; windows are display space
            words = []
            for w in plan.get_text("words"):
                r = fitz.Rect(w[:4]) * m
                words.append(((r.x0, r.y0, r.x1, r.y1), w[4]))
            for a, b in same_cuts(details, callouts(words, sheet), CALLOUT_NEAR_FT * min(scales)):
                if (a.number, b.number) in seen or a.number not in cell or b.number not in cell:
                    continue
                seen.add((a.number, b.number))
                pair = plan_rules.Pair(
                    kind="sections", a=f, b=f,
                    reason=(f"Sections {a.number}/{sheet} and {b.number}/{sheet} (both \"{a.title}\") are cut through "
                            f"one element at the same place on {g.label}, so they must agree"),
                    windows=[(list(cell[a.number]), list(cell[b.number]))],
                    labels=(f"section {a.number}/{sheet}", f"section {b.number}/{sheet}"),
                )
                out.append(pair)
            if len(out) >= MAX_PAIRS:
                return out
    return out
