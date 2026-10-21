"""Elements drawn with no type mark — "the pile type mark is not indicated at
grid A1/BK" (Leland BIM RFI 001), the most common RFI a detailer writes and
one no text check can see: the missing thing is a WORD, so the text layer has
nothing to find.

So the check learns, per sheet, what a marked element LOOKS like and then
looks for copies of that drawing nobody labelled. Nothing is told what a pile
or a pier is; it is learned from the sheet's own marks, so it works for any
family a drafter tags (P30-12, PC1, F3, C-6):

  1. A FAMILY is one mark shape repeated on the sheet (P##-## is P30-12,
     P24-10 …), at least MIN_MARKS times.
  2. Its SYMBOL STYLE is the stroke style (colour, width, dashes) drawn
     beside those marks and rarely anywhere else — a pier symbol is stroked
     0.36pt where the grid behind it is 0.24pt. A style spread evenly over the
     sheet is linework, not a symbol, and the family is skipped.
  3. Strokes of that style are merged into SYMBOLS by proximity, and the
     symbol nearest each mark is that mark's. The stroke counts and sizes the
     marked symbols span are learned as a RANGE (a pier at a wall corner is
     drawn a little differently from one in open floor), and the range counts
     only when most symbols in it on the sheet are marked.
  4. A symbol in that range with no mark of the family within reach, and no
     word inside it, is an unmarked element.

Precision before recall, as every check here: a sheet producing more than
MAX_UNMARKED for one family has taught the check the wrong shape, and says so
in a note instead of proposing a list of RFIs. A mark printed INSIDE its
symbol is a bubble or a tag (a grid bubble, a detail callout) — such a symbol
cannot be drawn without its label, so that family is never checked. Pure
except `unmarked_on_page`, which reads a fitz page.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass

# A type mark: letters then digits, optionally a dash group (P30-12, C-6, PC1).
# Dotted labels are grid lines and sheet numbers, never marks.
MARK = re.compile(r"[A-Z]{1,3}-?\d{1,3}(?:-\d{1,3})?[A-Z]?")
MIN_MARKS = 8            # marks of one family before a sheet can teach its symbol
SYMBOL_MAX_PT = 40.0     # one path larger than this is linework, not part of a symbol
NEAR_PT = 45.0           # a stroke this close to a mark counts as drawn beside it
GAP_PT = 3.0             # strokes this close belong to one symbol (wider chains dashes)
# Of the style's strokes that sit beside a mark. S2.31-A's pier style is 67%:
# a sheet whose unmarked piles run along a wall has more bare copies than most.
MIN_STYLE_SHARE = 0.6
MIN_SYMBOL_STROKES = 4   # fewer is a tick or a dash, never an element
MIN_SYMBOL_PT = 6.0      # a marked symbol smaller than this is a tick
MIN_SHAPE_SHARE = 0.75   # of the symbols in the learned range that are marked
MAX_UNMARKED = 5         # more than this per family: the wrong shape was learned


def family(word: str) -> str | None:
    """The mark's shape: P30-12 -> "P##-##". None for a word that is not a mark."""
    return re.sub(r"\d", "#", word) if MARK.fullmatch(word) else None


Rect = tuple[float, float, float, float]


def centre(r: Rect) -> tuple[float, float]:
    return (r[0] + r[2]) / 2, (r[1] + r[3]) / 2


def symbols(rects: list[Rect], gap: float = GAP_PT) -> list[tuple[Rect, int]]:
    """Strokes merged into symbols: (union box, stroke count). Two strokes
    belong together when their boxes are within `gap`. Pure."""
    parent = list(range(len(rects)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    cell = SYMBOL_MAX_PT
    buckets: dict[tuple[int, int], list[int]] = defaultdict(list)
    for i, r in enumerate(rects):
        buckets[(int(r[0] // cell), int(r[1] // cell))].append(i)
    for i, r in enumerate(rects):
        gx, gy = int(r[0] // cell), int(r[1] // cell)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for j in buckets.get((gx + dx, gy + dy), ()):
                    if j > i:
                        e = rects[j]
                        if max(e[0] - r[2], r[0] - e[2], 0) <= gap and max(e[1] - r[3], r[1] - e[3], 0) <= gap:
                            parent[find(i)] = find(j)
    groups: dict[int, list[Rect]] = defaultdict(list)
    for i, r in enumerate(rects):
        groups[find(i)].append(r)
    return [((min(r[0] for r in g), min(r[1] for r in g), max(r[2] for r in g), max(r[3] for r in g)), len(g))
            for g in groups.values()]


def _dims(sym: tuple[Rect, int]) -> tuple[float, float]:
    (x0, y0, x1, y1), _ = sym
    return tuple(sorted((x1 - x0, y1 - y0)))  # short side, long side: a rotated copy is a copy


def _pct(values: list[float], q: float) -> float:
    values = sorted(values)
    return values[int(q * (len(values) - 1))]


def symbol_range(owned: list[tuple[Rect, int]]) -> tuple[float, float, float, float, float] | None:
    """(fewest strokes, most strokes, shortest long side, longest long side,
    shortest short side) the marked symbols span, trimmed of the outer 10%
    (a mark's nearest symbol is sometimes a stray tick). Pure. None when the
    marked symbols are too small to be anything but ticks."""
    owned = [o for o in owned if o[1] >= MIN_SYMBOL_STROKES]
    if len(owned) < MIN_MARKS:
        return None
    counts = [n for _, n in owned]
    longs = [_dims(s)[1] for s in owned]
    shorts = [_dims(s)[0] for s in owned]
    lo_n = _pct(counts, 0.1)
    lo_long = _pct(longs, 0.1)
    if lo_long < MIN_SYMBOL_PT:
        return None
    # A symbol drawn against a wall merges with the wall's corner ticks: the
    # top end is generous, and the share-marked test below keeps it honest.
    return lo_n, 2.5 * _pct(counts, 0.9), 0.8 * lo_long, 1.4 * _pct(longs, 0.9), max(2.0, 0.8 * _pct(shorts, 0.1))


def in_range(sym: tuple[Rect, int], span) -> bool:
    lo_n, hi_n, lo_long, hi_long, lo_short = span
    short, long_ = _dims(sym)
    return lo_n <= sym[1] <= hi_n and lo_long <= long_ <= hi_long and short >= lo_short


@dataclass(frozen=True)
class Unmarked:
    family: str
    box: Rect                 # the unmarked symbol, in the page's unrotated space
    examples: tuple[str, ...]  # marks of the family on this sheet
    marked: int               # how many marked symbols like it the sheet has


def _near(points, cell: float):
    grid: dict[tuple[int, int], list[tuple[float, float]]] = defaultdict(list)
    for x, y in points:
        grid[(int(x // cell), int(y // cell))].append((x, y))

    def within(x: float, y: float, reach: float) -> bool:
        gx, gy = int(x // cell), int(y // cell)
        span = int(reach // cell) + 1
        return any(math.hypot(x - a, y - b) <= reach
                   for dx in range(-span, span + 1) for dy in range(-span, span + 1)
                   for a, b in grid.get((gx + dx, gy + dy), ()))
    return within


def find_unmarked(marks: list[tuple[str, Rect]], strokes: list[tuple[tuple, Rect]],
                  words: list[Rect]) -> tuple[list[Unmarked], list[str]]:
    """(unmarked elements, notes) for one sheet. Pure: `marks` are (text, box)
    of every mark-shaped word, `strokes` (style, box) of every drawn path no
    larger than SYMBOL_MAX_PT, `words` the box of every word on the sheet."""
    by_family: dict[str, list[tuple[str, Rect]]] = defaultdict(list)
    for text, box in marks:
        fam = family(text)
        if fam:
            by_family[fam].append((text, box))
    found: list[Unmarked] = []
    notes: list[str] = []
    for fam, members in sorted(by_family.items()):
        if len(members) < MIN_MARKS:
            continue
        centres = [centre(b) for _, b in members]
        near_mark = _near(centres, NEAR_PT)
        beside: Counter = Counter()
        total: Counter = Counter()
        for style, box in strokes:
            total[style] += 1
            if near_mark(*centre(box), NEAR_PT):
                beside[style] += 1
        styles = [s for s in beside if beside[s] >= len(members) and beside[s] / total[s] >= MIN_STYLE_SHARE]
        if not styles:
            continue
        style = max(styles, key=lambda s: (beside[s] / total[s], beside[s]))
        syms = symbols([box for s, box in strokes if s == style])
        if not syms:
            continue
        # The symbol nearest each mark, within NEAR_PT, is that mark's. A
        # sheet draws one element a few ways (a pier in open floor, a pier at
        # a wall corner), so what is learned is the RANGE its marked symbols
        # span — stroke count and size — not one exact shape.
        owned = []
        inside = 0
        for _, box in members:
            mx, my = centre(box)
            sym = min(syms, key=lambda s: math.hypot(centre(s[0])[0] - mx, centre(s[0])[1] - my))
            (x0, y0, x1, y1), _ = sym
            d = math.hypot(centre(sym[0])[0] - mx, centre(sym[0])[1] - my)
            if x0 <= mx <= x1 and y0 <= my <= y1:
                inside += 1
            if d <= NEAR_PT:
                owned.append((sym, d))
        if inside * 2 >= len(members):
            continue  # a bubble or a tag: drawn with its label, never without
        if len(owned) < MIN_MARKS:
            continue
        span = symbol_range([s for s, _ in owned])
        if span is None:
            continue
        dists = sorted(d for _, d in owned)
        # Twice the usual distance: an element is often drawn as two pieces
        # (a pier's circle split by its column) and only one is nearest.
        reach = min(2 * NEAR_PT, 2 * dists[int(0.9 * (len(dists) - 1))])
        has_mark = _near(centres, reach)
        word_centres = _near([centre(w) for w in words], SYMBOL_MAX_PT)
        alike = [s for s in syms if in_range(s, span)]
        bare = [s for s in alike if not has_mark(*centre(s[0]), reach)]
        if not alike or (len(alike) - len(bare)) / len(alike) < MIN_SHAPE_SHARE:
            continue
        candidates: list[Unmarked] = []
        for s in bare:
            (x0, y0, x1, y1), _ = s
            # A word inside the symbol labels it (a callout, a tag of another
            # family): only an EMPTY copy is unmarked.
            cx, cy = centre(s[0])
            if word_centres(cx, cy, min(x1 - x0, y1 - y0) / 2):
                continue
            candidates.append(Unmarked(fam, s[0], tuple(sorted({t for t, _ in members}))[:4], len(alike) - len(bare)))
        # One element can be several symbols (a pile's circle cut in two by
        # the wall line over it): pieces closer than one symbol are one element.
        candidates = _merge(candidates, span[3])
        if len(candidates) > MAX_UNMARKED:
            notes.append(f"{len(candidates)} symbols look like the {fam} elements but carry no mark — "
                         "too many to be omissions, so none is reported.")
            continue
        found += candidates
    return found, notes


def _merge(found: list[Unmarked], within: float) -> list[Unmarked]:
    """Pieces whose boxes are closer than `within` merged into one. Pure."""
    groups = [s[0] for s in symbols([u.box for u in found], within)]
    return [Unmarked(found[0].family, g, found[0].examples, found[0].marked) for g in groups] if found else []


def unmarked_on_page(page) -> tuple[list[Unmarked], list[str]]:
    """`_unmarked_on_page`, reused for a page a review already read (page_memo)."""
    import page_memo

    return page_memo.memo("unmarked_on_page@1", page, lambda: _unmarked_on_page(page))


def _unmarked_on_page(page) -> tuple[list[Unmarked], list[str]]:
    """`find_unmarked` over a fitz page, in its unrotated space (what
    get_text and get_cdrawings both report)."""
    words = page.get_text("words")
    marks = [(w[4], tuple(w[:4])) for w in words if MARK.fullmatch(w[4])]
    counts = Counter(family(t) for t, _ in marks)
    if not counts or counts.most_common(1)[0][1] < MIN_MARKS:
        return [], []
    strokes = []
    for d in page.get_cdrawings():
        if d.get("type") == "f":
            continue  # fills are masks and poché, not a symbol's outline
        r = d["rect"]
        if max(r[2] - r[0], r[3] - r[1]) > SYMBOL_MAX_PT:
            continue
        style = (tuple(round(c, 2) for c in (d.get("color") or ())), round(d.get("width") or 0, 2),
                 str(d.get("dashes")))
        strokes.append((style, tuple(r)))
    return find_unmarked(marks, strokes, [tuple(w[:4]) for w in words])


# --- The scan ---------------------------------------------------------------------

GRID_NAME_PT = 30.0  # a grid line this close to the symbol names where it is
# Type marks name ELEMENTS (piles, piers, footings, columns) on these sheets.
# An architectural sheet's repeated marks are tags (wall types, doors), and on
# Leland's A-sheets a wall drawn in the tag's line style read as two unmarked
# "W-###" elements. None: a sheet whose number was never read.
DISCIPLINES = {"structural", "civil", None}


def candidate_page_ids(pages, chunks) -> set[str]:
    """Pages whose TEXT already shows MIN_MARKS marks of one family — the only
    ones worth opening. Pure."""
    text: dict[str, list[str]] = defaultdict(list)
    for c in chunks:
        text[c.page_id].append(c.text)
    out = set()
    for page in pages:
        counts = Counter(f for f in (family(t) for t in re.findall(r"[A-Z0-9-]+", " ".join(text[page.id]))) if f)
        if counts and counts.most_common(1)[0][1] >= MIN_MARKS:
            out.add(page.id)
    return out


def _grid_names(page, box: Rect) -> str:
    """'grid line BK' / 'grid A1/BK' for the symbol, or '' when no grid line is
    near it (an angled grid is not read)."""
    import fitz
    import grid

    try:
        columns, rows = grid.page_grid(page)
    except Exception:
        return ""
    p = fitz.Point(*centre(box)) * page.rotation_matrix  # grid reports display space
    col = min(columns.items(), key=lambda kv: abs(kv[1] - p.x), default=None)
    row = min(rows.items(), key=lambda kv: abs(kv[1] - p.y), default=None)
    col = col[0] if col and abs(col[1] - p.x) <= GRID_NAME_PT else None
    row = row[0] if row and abs(row[1] - p.y) <= GRID_NAME_PT else None
    if col and row:
        return f"grid {col}/{row}"
    return f"grid line {col or row}" if (col or row) else ""


def findings(pages, chunks, open_page) -> tuple[list, list[str]]:
    """unmarked_element findings for the project: (findings, notes)."""
    from rfi_checks import MAX_EVIDENCE, Finding, fingerprint, page_label

    wanted = candidate_page_ids(pages, chunks)
    out, notes = [], []
    opened = 0
    for row in pages:
        if row.id not in wanted or row.discipline not in DISCIPLINES:
            continue
        page = open_page(row.document_id, row.page_number)
        if page is None:
            continue
        opened += 1
        found, more = unmarked_on_page(page)
        label = page_label(row)
        notes += [f"Unmarked elements on {label}: {n}" for n in more]
        by_family: dict[str, list[Unmarked]] = defaultdict(list)
        for u in found:
            by_family[u.family].append(u)
        for fam, items in by_family.items():
            # One issue per sheet and family: piles left unmarked along one
            # wall are one RFI, as the team wrote Leland RFI 003.
            wheres = [_grid_names(page, u.box) for u in items]
            places = sorted({w for w in wheres if w})
            at = f" at {', '.join(places)}" if places else ""
            examples = ", ".join(items[0].examples)
            n = len(items)
            what = "an element is" if n == 1 else f"{n} elements are"
            out.append(Finding(
                check_type="unmarked_element",
                fingerprint=fingerprint("unmarked_element", row.document_id, str(row.page_number), fam,
                                        *sorted(f"{round(centre(u.box)[0] / 10)},{round(centre(u.box)[1] / 10)}" for u in items)),
                # Learned from the sheet's own symbols, never read: medium at most.
                confidence="medium",
                subject=(f"Element with no type mark on {label}{at}" if n == 1
                         else f"{n} elements with no type mark on {label}{at}")[:120],
                question=(f"On {label}{at}, {what} drawn the same way as the {items[0].marked} elements marked "
                          f"{examples} on this sheet, but {'carries' if n == 1 else 'carry'} no mark. "
                          f"Please confirm {'its' if n == 1 else 'their'} type mark."),
                evidence=[{
                    "documentId": row.document_id,
                    "pageNumber": row.page_number,
                    "combinedPageNumber": row.combined_page_number,
                    "sheetNumber": row.sheet_number,
                    "bbox": {"x": round(u.box[0], 2), "y": round(u.box[1], 2),
                             "width": round(u.box[2] - u.box[0], 2), "height": round(u.box[3] - u.box[1], 2)},
                    "chunkId": None,
                    # The marks themselves, never the family pattern: the wording
                    # model echoed "P##-## symbol format" into the RFI.
                    "quote": f"Drawn like the elements marked {examples}, with no mark beside it",
                    "role": "finding",
                } for u in items[:MAX_EVIDENCE]],
                facts={"sheet": label, "where": places, "examples": list(items[0].examples),
                       "markedLikeIt": items[0].marked, "count": n},
            ))
    notes.append(f"Unmarked elements: read {opened} sheet(s) whose marks repeat often enough to learn their symbol.")
    return out, notes
