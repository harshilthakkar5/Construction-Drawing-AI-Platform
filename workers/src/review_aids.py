"""Measured aids a targeted review hands the model — facts no model is asked for.

C03 asks "what are the column offsets from the grids". A model reading a
drawing picture estimates an offset; the PDF knows it. `column_grid_offsets`
measures every column-sized element against the nearest grid crossing, in
the drawing's own printed scale, and writes the result as one evidence item
(kind "aid").

G02 asks which levels and elevations the drawings name. `level_index` lists
the ones printed in the reviewed text, sheet by sheet, so a model comparing
levels does not have to find them in forty chunks first.

An aid is DERIVED, and the review treats it that way (rfi_review):

  * it is never the sole support of a finding — a measurement against a
    misread grid would otherwise become an RFI on its own;
  * its numbers are NOT grounding material: a question may state an offset
    only if a printed dimension in the cited evidence carries it. A computed
    "1'-2\"" appearing in an RFI as though the drawing said it is exactly the
    invented fact the grounding rule exists to stop.

Everything is in DISPLAY space, like grid.page_grid and plan_match: "right"
and "below" mean what a person looking at the sheet sees.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# A column further than this from every grid crossing is not "offset from"
# any of them — it belongs to no crossing, and reporting its distance to the
# nearest one would invent a relationship.
MAX_OFFSET_FT = 4.0
# Without a printed scale, the same cut-off in points (a 1/8" plan's 4 ft).
MAX_OFFSET_PT = 36.0
# Closer than this is "on the line": drafting tolerance, not an offset.
ON_LINE_IN = 1.0


@dataclass(frozen=True)
class ColumnOffset:
    crossing: str  # "3/C"
    dx: float  # display points, + is right of the column line
    dy: float  # display points, + is below the row line
    width: float
    height: float


def column_grid_offsets(columns: dict[str, float], rows: dict[str, float], elements, pt_per_ft: float | None) -> list[ColumnOffset]:
    """Each column-sized element's offset from its nearest grid crossing.

    `columns` / `rows` are grid.page_grid's {label: x} / {label: y};
    `elements` are plan_match Elements (cx, cy, w, h in display points). An
    element with no crossing within MAX_OFFSET_FT is left out, not reported
    against a far one.
    """
    if not columns or not rows:
        return []
    limit = MAX_OFFSET_FT * pt_per_ft if pt_per_ft else MAX_OFFSET_PT
    out = []
    for e in elements:
        col, x = min(columns.items(), key=lambda kv: abs(e.cx - kv[1]))
        row, y = min(rows.items(), key=lambda kv: abs(e.cy - kv[1]))
        if abs(e.cx - x) > limit or abs(e.cy - y) > limit:
            continue
        out.append(ColumnOffset(f"{col}/{row}", e.cx - x, e.cy - y, e.w, e.h))
    return sorted(out, key=lambda o: o.crossing)


def _length(pt: float, pt_per_ft: float | None) -> str:
    if not pt_per_ft:
        return f"{abs(pt):.0f} pt"
    inches = round(abs(pt) / pt_per_ft * 12)
    return f"{inches // 12}'-{inches % 12}\""


def _on_line(pt: float, pt_per_ft: float | None) -> bool:
    return abs(pt) < (ON_LINE_IN / 12 * pt_per_ft if pt_per_ft else 1.0)


def describe_offsets(sheet: str, offsets: list[ColumnOffset], pt_per_ft: float | None) -> str:
    """The aid's text. States its own provenance and limits in the first
    line, because it travels to the model as evidence."""
    head = (
        f"MEASURED on {sheet} from the PDF geometry, not printed on the drawing: each concrete column's centre "
        f"against the nearest grid crossing"
        + (". Distances are converted at the sheet's printed scale." if pt_per_ft else "; no drawing scale was read, so distances are in PDF points and cannot be compared with printed dimensions.")
        + " Directions are as the sheet is viewed."
    )
    if not offsets:
        return head + "\nNo column stands within 4 ft of a grid crossing on this sheet."
    lines = [head]
    for o in offsets:
        parts = []
        # The first label names a line drawn up the sheet (a position in x),
        # the second one drawn across it — said as directions, because which
        # is a "column line" is a naming convention the drawing may not share.
        for pt, pos, neg, label in ((o.dx, "right of", "left of", o.crossing.split("/")[0]), (o.dy, "below", "above", o.crossing.split("/")[1])):
            if _on_line(pt, pt_per_ft):
                parts.append(f"on grid line {label}")
            else:
                parts.append(f"{_length(pt, pt_per_ft)} {pos if pt > 0 else neg} grid line {label}")
        size = f" ({_length(o.width, pt_per_ft)} x {_length(o.height, pt_per_ft)})" if pt_per_ft else ""
        lines.append(f"Column{size} near {o.crossing}: centre {', '.join(parts)}.")
    return "\n".join(lines)


def column_offsets_for_page(page, sheet: str) -> tuple[str | None, str]:
    """(aid text or None, note). Reads the grid and the columns off a fitz
    page; None when either is missing, with the note saying which."""
    import grid
    import plan_match

    clean = grid.without_markup(page)
    columns, rows = grid.page_grid(clean)
    if not columns or not rows:
        return None, f"C03 aid: no grid bubbles read on {sheet}, so no column offsets were measured."
    geometry = plan_match.SheetGeometry.read(clean)
    if not geometry.elements:
        return None, f"C03 aid: no concrete columns recognised on {sheet}" + ("" if geometry.scales else " (no drawing scale printed)") + "."
    ptft = min(geometry.scales) if geometry.scales else None
    offsets = column_grid_offsets(columns, rows, geometry.elements, ptft)
    return describe_offsets(sheet, offsets, ptft), f"C03 aid: {len(offsets)} column offset(s) measured on {sheet}."


def column_occurrences_for_page(page, sheet: str) -> tuple[list, str | None, str]:
    """(occurrences, aid text or None, note) for C03.

    Column MARKS first (`column_locate`): each printed occurrence of a mark,
    its own body found in a high-resolution render beside the label, and that
    body measured against the grid in BOTH directions, minor lines included.
    That is what the first real C03 run lacked: `column_offsets_for_page`
    finds columns as vector filled boxes with stipple (an architectural
    convention), so on a structural forming plan — whose column fills are
    raster tiles — it found nothing and the model judged offsets from a
    whole-sheet picture. A sheet with no column marks keeps the old reading.
    """
    import column_locate
    import grid

    clean = grid.without_markup(page)
    occurrences, note = column_locate.locate(clean)
    if occurrences:
        measured = sum(1 for o in occurrences if o.body is not None)
        return (
            occurrences,
            column_locate.describe(sheet, occurrences, note),
            f"C03 aid: {len(occurrences)} column mark occurrence(s) on {sheet}, {measured} measured against the grid"
            + (f" ({note})" if note else "")
            + ".",
        )
    text, old_note = column_offsets_for_page(page, sheet)
    return [], text, old_note


# --- G02 --------------------------------------------------------------------------

_LEVEL = re.compile(r"\b(?:LEVEL|LVL\.?)\s*([0-9]{1,3}|[A-Z]{1,2}\d?|ROOF|BASEMENT|GROUND|MEZZANINE)\b", re.I)
_ELEVATION = re.compile(
    r"\b((?:T\.?O\.?[SWBCF]\.?|B\.?O\.?[SF]\.?|TOP OF (?:SLAB|STEEL|FOOTING|WALL|CONCRETE)|FIN(?:ISH)?\.? FL(?:OO)?R\.?|F\.?F\.?(?:E|L)\.?|EL(?:EV)?\.?)\s*(?:EL(?:EV)?\.?\s*)?[=:]?\s*[+-]?\s*\d+\s*'\s*-?\s*\d+(?:\s*\d/\d+)?\s*\"?)",
    re.I,
)


def level_index(sources: list[tuple[str, str]]) -> str | None:
    """(sheet, text) pairs -> the levels and elevations they print, quoted,
    each with the sheets it appears on. None when nothing is printed."""
    levels: dict[str, list[str]] = {}
    elevations: dict[str, list[str]] = {}
    for sheet, text in sources:
        for m in _LEVEL.finditer(text or ""):
            key = f"LEVEL {m.group(1).upper()}"
            levels.setdefault(key, [])
            if sheet not in levels[key]:
                levels[key].append(sheet)
        for m in _ELEVATION.finditer(text or ""):
            key = " ".join(m.group(1).upper().split())
            elevations.setdefault(key, [])
            if sheet not in elevations[key]:
                elevations[key].append(sheet)
    if not levels and not elevations:
        return None
    lines = ["INDEX of levels and elevations printed in the reviewed text (quoted; not a finding):"]
    for name, sheets in sorted(levels.items()):
        lines.append(f"{name}: {', '.join(sheets)}")
    for value, sheets in sorted(elevations.items())[:40]:
        lines.append(f"{value}: {', '.join(sheets)}")
    return "\n".join(lines)


# A dimension whose text is a pointer instead of a length: "SEE PLAN" standing
# alone on a dimension line. A note's pointer carries a dash ("-SEE PLAN"),
# and an elevation's sits under its label ("T.O.C." / "SEE PLAN").
_DEFERRED = re.compile(r"^SEE\s+(PLANS?|CIVIL|ARCH(?:ITECTURAL)?)$", re.I)
_ELEVATION_LABEL = re.compile(r"^(?:T\.?\s*O\.?|TOP\s+OF|B\.?\s*O\.?|BOTTOM\s+OF|EL\.?\b|ELEV)", re.I)
# "SCALE: 1/4" = 1'-0"", or the bare ratio a Revit title prints under itself (UT Law S3.100: "3/4" = 1'-0"").
_BARE_SCALE = re.compile(r"^\s*\d+(?:\s+\d+)?(?:/\d+)?\s*\"\s*=\s*\d+'\s*-?\s*\d+\"\s*$")
_SCALE = re.compile(rf"^\s*SCALE\b|{_BARE_SCALE.pattern}", re.I)
MAX_DEFERRED_DETAILS = 12


def _display_lines(page) -> list[tuple[str, object, bool]]:
    """(text, display-space rect, runs vertically) per text line."""
    import fitz

    m = page.rotation_matrix
    out = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            text = " ".join(s["text"] for s in line["spans"]).strip()
            if not text:
                continue
            dx, dy = line["dir"]
            vx, vy = dx * m.a + dy * m.c, dx * m.b + dy * m.d  # a direction turns, it does not move
            out.append((text, fitz.Rect(line["bbox"]) * m, abs(vy) > abs(vx)))
    return out


_OWN_REFERENCE = re.compile(r"^\s*\d{1,3}[A-Z]?\s*/\s*[A-Z]{1,2}-?\d+(?:\.\d+)*[A-Z]?\s*$", re.I)


def detail_titles(lines) -> list[tuple[str | None, str, object]]:
    """(number, title, display rect) for every detail title on a page: a line
    with its SCALE line right below, and the detail number printed to its left
    when there is one. Pure over `_display_lines`."""
    scales = [(r, bool(_BARE_SCALE.match(t))) for t, r, _ in lines if _SCALE.match(t)]
    out = []
    for text, box, vertical in lines:
        # A title is words (Revit writes them in title case), or only the detail's own reference ("10/S4.10").
        if vertical or _SCALE.match(text) or not (re.search(r"[A-Za-z]{3,}", text) or _OWN_REFERENCE.match(text)):
            continue
        under = [bare for s, bare in scales if -15 <= s.y0 - box.y1 <= 40 and s.x0 < box.x1 and s.x1 > box.x0]
        if under:
            number = next((t for t, r, _ in lines if re.fullmatch(r"\d{1,2}", t) and 0 <= box.x0 - r.x1 <= 80
                           and r.y0 < box.y1 + 30 and r.y1 > box.y0 - 10), None)
            # A bare ratio ("3/4\" = 1'-0\"", Revit) also sits under notes inside a detail: it marks a
            # title only with the detail number beside it.
            if number is None and all(under):
                continue
            out.append((number, text, box))
    return out


def title_of(cx: float, cy: float, titles: list[tuple]) -> tuple | None:
    """The title a point belongs to: a detail's title is printed UNDER it
    (detail_regions' convention); a sheet titled above falls back to the
    nearest one. Pure."""
    column = [t for t in titles if t[2].x0 - 60 <= cx <= t[2].x0 + 900]
    below = [(t[2].y0 - cy, i, t) for i, t in enumerate(column) if t[2].y0 >= cy]
    near = below or [(abs(cy - t[2].y1), i, t) for i, t in enumerate(column)]
    return min(near)[2] if near else None


# A pointer to the plan, alone on its line or as a note's dash ("-SEE PLAN").
_SEE_PLAN = re.compile(r"^-?\s*SEE\s+PLANS?$", re.I)


def deferred_details(page, box: dict | None) -> set[str]:
    """The numbers of the details on this sheet that print "SEE PLAN" for a
    value inside `box` (stored, unrotated space; None = the whole page)."""
    import fitz

    lines = _display_lines(page)
    titles = [t for t in detail_titles(lines) if t[0]]
    if not titles:
        return set()
    area = None if box is None else fitz.Rect(box["x"], box["y"], box["x"] + box["width"], box["y"] + box["height"]) * page.rotation_matrix
    out = set()
    for text, rect, _ in lines:
        if not _SEE_PLAN.match(text) or (area is not None and not area.normalize().intersects(rect)):
            continue
        title = title_of((rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2, titles)
        if title:
            out.add(title[0])
    return out


def deferred_dimensions(page, sheet: str) -> str | None:
    """The dimensions a detail sheet prints as "SEE PLAN" where a length
    belongs, grouped by the detail they sit in. Leland: 6/S4.01 gives the
    wall face to grid line as "SEE PLAN" and the plan never dimensions it;
    one review read that and one did not. Listed by code, every review sees
    each one. None when the page has none."""
    lines = _display_lines(page)
    titles = [(f"detail {number} {text}" if number else text, box) for number, text, box in detail_titles(lines)]
    found: dict[str, list[bool]] = {}
    for text, box, vertical in lines:
        if not _DEFERRED.match(text):
            continue
        label = [r for t, r, _ in lines if _ELEVATION_LABEL.match(t) and 0 <= box.y0 - r.y1 <= 25
                 and r.x0 < box.x1 and r.x1 > box.x0]
        if label:
            continue  # "T.O.C. / SEE PLAN" is an elevation, read by G02
        cx, cy = (box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2
        title = title_of(cx, cy, [(None, name, r) for name, r in titles])
        where = title[1] if title else "no detail title found"
        found.setdefault(where, []).append(vertical)
    if not found:
        return None
    out = [f"DIMENSIONS PRINTED AS A REFERENCE on {sheet} (found by code; not a finding): a dimension line whose "
           "text reads 'SEE PLAN' (or civil/architectural) where a length belongs. The detail does NOT give these "
           "lengths. For each, say whether the sheet it points to prints that length at this element; if it does not, "
           "it is missing. Horizontal is a distance across the detail (a wall face or pier centre to a grid line); vertical is a height or thickness."]
    for where, dirs in list(found.items())[:MAX_DEFERRED_DETAILS]:
        v = sum(dirs)
        out.append(f"- {where}: {len(dirs)} ({len(dirs) - v} horizontal, {v} vertical)")
    if len(found) > MAX_DEFERRED_DETAILS:
        out.append(f"- and {len(found) - MAX_DEFERRED_DETAILS} more detail(s)")
    return "\n".join(out)
