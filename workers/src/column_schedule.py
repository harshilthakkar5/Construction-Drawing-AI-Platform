"""A column the schedule lists but never sizes — Leland BIM RFI 005: S8.02's
column for "B6-BA, B6-BB, B7-BA, B7-BB" draws the column at every level and
prints no size or reinforcement for it anywhere, while every neighbour does.

Read off the PDF because the fact is a table's SHAPE, not its words: Revit's
graphical column schedule prints one table column per group of locations,
its "Column Locations" cell at the BOTTOM and the levels stacked above, and a
size tag ("18" X 24"") wherever the column changes. A column with locations
and no size anywhere above them is the RFI. The table's own vertical lines
split it into columns; nothing is guessed from text positions alone.

Precision before recall: a table where fewer than MIN_SIZED columns carry a
size, or more than a third are empty, was not read as a schedule, and says
so in a note. Pure but for `on_page` and `findings`.
"""

from __future__ import annotations

import re

LABEL = r"[A-Z]{1,2}\d*(?:\.\d+)?"
# A location as Revit prints it: B6-BA, or A4(-9")-BB(7' - 3/4") for a
# column off its grid lines.
LOCATION = re.compile(rf"\b({LABEL})(\([^)]*\))?-({LABEL})(\([^)]*\))?")
# A column size: 24"x24", 24" ø, round 24" DIA. / Ø30", HSS, W-shape, PIPE 8.
# The x form still needs its closing inch mark, so "2 X 4 BLOCKING" is not one.
SIZE = re.compile(r"\d+\"?\s*[Xx]\s*\d+\"|\d+\"\s*(?:[øØ]|(?i:DIA)\b)|[øØ]\s*\d+\"|\bHSS\s*\d|\bW\d+\s*[Xx]\s*\d+|\bPIPE\s*\d")
MIN_SIZED = 3
HEADER_PT = 100.0  # the location lines sit within this of the "Column Locations" label

Word = tuple[float, float, float, float, str]


def _text(words: list[Word]) -> str:
    return " ".join(w[4] for w in sorted(words, key=lambda w: (round(w[1] / 3), w[0])))


def unsized(words: list[Word], vlines: list[tuple[float, float, float]]) -> tuple[list[dict], list[str]]:
    """({"locations": [...], "box": (x0, y0, x1, y1)} per schedule column
    with locations and no size, notes). Pure: `vlines` are (x, y0, y1) of the
    page's vertical strokes."""
    found, notes, _ = read(words, vlines)
    return found, notes


def read(words: list[Word], vlines: list[tuple[float, float, float]]) -> tuple[list[dict], list[str], int]:
    """`unsized`, plus how many columns the reliably read tables DO size. Pure."""
    labels = sorted((a for a in words if a[4] == "Locations"
                     and any(b[4] == "Column" and abs(b[0] - a[0]) < 40 and -3 < a[1] - b[1] < 25 for b in words)),
                    key=lambda a: a[1])
    found, notes, sized_total = [], [], 0
    for i, label in enumerate(labels):
        y = label[1]
        top = labels[i - 1][1] + HEADER_PT if i else 0.0  # the table above ends with its own header
        xs = sorted({round(x) for x, y0, y1 in vlines if y0 < y + 20 and y1 > y + 60 and x > label[2]})
        xs = [x for j, x in enumerate(xs) if j == 0 or x - xs[j - 1] > 20]
        cells = []
        for x0, x1 in zip(xs, xs[1:]):
            inside = [w for w in words if x0 <= (w[0] + w[2]) / 2 < x1]
            head = _text([w for w in inside if y - 20 <= w[1] <= y + HEADER_PT])
            locations = [" ".join(m.group(0).split()) for m in LOCATION.finditer(head)]
            if locations:
                sized = bool(SIZE.search(_text([w for w in inside if top <= w[1] < y - 20])))
                cells.append((locations, sized, (x0, y - 20, x1, y + HEADER_PT)))
        empty = [c for c in cells if not c[1]]
        if len(cells) - len(empty) < MIN_SIZED or len(empty) * 3 > len(cells):
            if empty:
                notes.append(f"A column schedule table was not read reliably ({len(empty)} of {len(cells)} "
                             "columns showed no size), so none of its columns is reported.")
            continue
        found += [{"locations": c[0], "box": c[2]} for c in empty]
        sized_total += len(cells) - len(empty)
    return found, notes, sized_total


def on_page(page) -> tuple[list[dict], list[str]]:
    """`unsized` over a fitz page, in its unrotated space (get_text and
    get_cdrawings both report it)."""
    found, notes, _ = read_page(page)
    return found, notes


def sizes_columns(page) -> bool:
    """Whether this page carries a column schedule, read reliably, that gives
    columns their sizes — the place a "SEE COL SCHEDULE" note points to."""
    return read_page(page)[2] > 0


def read_page(page) -> tuple[list[dict], list[str], int]:
    words = [tuple(w[:5]) for w in page.get_text("words")]
    if not any(w[4] == "Locations" for w in words):
        return [], [], 0
    vlines = []
    for d in page.get_cdrawings():
        for item in d["items"]:
            if item[0] == "l":
                (ax, ay), (bx, by) = item[1], item[2]
                if abs(ax - bx) < 0.6 and abs(ay - by) > 30:
                    vlines.append((ax, min(ay, by), max(ay, by)))
            elif item[0] == "re":  # cdrawings give a rect as a tuple
                x0, y0, x1, y1 = item[1]
                vlines += [(x0, y0, y1), (x1, y0, y1)]
    return read(words, vlines)


def findings(pages, chunks, open_page) -> tuple[list, list[str]]:
    """unsized_column findings for the project: (findings, notes)."""
    from rfi_checks import Finding, fingerprint, page_label

    with_label = {c.page_id for c in chunks if re.search(r"column\s+locations", c.text, re.I)}
    out, notes = [], []
    for row in pages:
        if row.id not in with_label:
            continue
        page = open_page(row.document_id, row.page_number)
        if page is None:
            continue
        found, more = on_page(page)
        label = page_label(row)
        notes += [f"Column schedule on {label}: {n}" for n in more]
        for f in found:
            where = ", ".join(f["locations"])
            many = len(f["locations"]) > 1
            x0, y0, x1, y1 = f["box"]
            out.append(Finding(
                check_type="unsized_column",
                fingerprint=fingerprint("unsized_column", row.document_id, str(row.page_number), *sorted(f["locations"])),
                confidence="high",
                subject=f"No column size in the schedule for {where}"[:120],
                question=(f"The column schedule on {label} lists the column{'s' if many else ''} at {where} but gives "
                          f"no size or reinforcement for {'them' if many else 'it'} at any level. Please provide the "
                          f"column size{'s' if many else ''} and reinforcement."),
                evidence=[{
                    "documentId": row.document_id,
                    "pageNumber": row.page_number,
                    "combinedPageNumber": row.combined_page_number,
                    "sheetNumber": row.sheet_number,
                    "bbox": {"x": round(x0, 2), "y": round(y0, 2), "width": round(x1 - x0, 2), "height": round(y1 - y0, 2)},
                    "chunkId": None,
                    "quote": f"Column Locations: {where}",
                    "role": "finding",
                }],
                facts={"sheet": label, "locations": f["locations"]},
            ))
    return out, notes
