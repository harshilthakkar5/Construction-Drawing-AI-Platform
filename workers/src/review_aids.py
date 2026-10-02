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
