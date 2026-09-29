"""Which mark is printed at which grid crossing — read from the PDF, no model.

Why this exists
---------------
A structural sheet that marks its elements against schedules (`C-6`, `SR-8`,
`PC1`) holds BOTH halves of "what is at 3/C" in its own data: the mark is text
with a position, and the grid is circles with positions (`grid.page_grid`).
The pairing is geometry — which crossing is the mark printed nearest to — and
geometry is exact where a vision model is not.

It was measured on the client's S2.105 (level-5 forming plan, 360 crossings
counting secondary lines): the crop pass, 48 images and a minute of model
time, placed the right mark at about 7 of the 19 primary crossings that carry
one, and put secondary-line marks at primary crossings (C-30 on 3.4/F reported
at 3/F). This reads the same sheet in under half a second and places each of
those marks where the drawing prints it, secondary lines included.

What it claims, exactly
-----------------------
"This mark is printed nearest to this crossing", and nothing stronger. A mark
is assigned only when that crossing is nearer than every other by `MARGIN_PT`
— a mark printed about equally close to two crossings is reported as BETWEEN
them and assigned to neither, because picking one is how a real label lands at
the wrong intersection. A mark further than `MAX_PT` from every crossing (a
schedule row, a note) is not a grid mark at all. It cannot see a leader line:
a mark drawn far away and pointed at its column is assigned to wherever it is
printed, which is why the chat is told the rule rather than handed a fact.

What counts as a mark is deliberately generic (`MARK`) rather than a list of
one sheet's vocabulary — that mistake has been made twice in this repository
(the eval generator's footing tags, the crop prompt's "footing") and both times
the second sheet used a notation the first did not.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import fitz

import chunker
import grid

# A short schedule mark (C-6, SR-8, PC1, F12, SW-3, B3A) or a printed member
# size (HSS8X8X1/4, W12X26). No grid label `grid.page_grid` accepts can match
# it (those are numbers, letters, or letters with a decimal like B1.5), so a
# bubble's own name is never read as a mark.
MARK = re.compile(
    r"^(?:[A-Z]{1,3}-?\d{1,3}[A-Z]?"
    r"|HSS\d+(?:\.\d+)?X\d+(?:\.\d+)?X\d+(?:/\d+)?(?:\.\d+)?"
    r"|W\d{1,2}X\d{1,3})$"
)
# A size printed on the line under a mark: "(14 x 30)", "(22" DIA)".
_SIZE = re.compile(r"^\(.*\)$")

# Further than this from every crossing and it is a schedule row or a note.
MAX_PT = 90.0
# The nearest crossing must win by this much, or the mark is "between".
MARGIN_PT = 12.0

# Recorded on the chunk (`sourceModel`) so a stored reading names its reader.
SOURCE = "geometry/gridmarks-v1"


@dataclass
class GridMarks:
    columns: dict[str, float]
    rows: dict[str, float]
    pairs: list[tuple[str, str, float, float]]
    # crossing label -> marks printed nearest to it, in reading order, unique
    at: dict[str, list[str]] = field(default_factory=dict)
    # (mark, crossing a, crossing b) for marks equally near two crossings
    between: list[tuple[str, str, str]] = field(default_factory=list)

    @property
    def extent(self) -> dict:
        xs = [x for _, _, x, _ in self.pairs]
        ys = [y for _, _, _, y in self.pairs]
        return {"x": min(xs), "y": min(ys), "width": max(xs) - min(xs), "height": max(ys) - min(ys)}


def _size_lines(words) -> list[tuple[str, fitz.Rect]]:
    """Every text line that is a parenthetical size, with its box."""
    lines: dict[tuple[int, int], list] = {}
    for w in words:
        lines.setdefault((w[5], w[6]), []).append(w)
    out = []
    for members in lines.values():
        members.sort(key=lambda w: w[0])
        text = " ".join(w[4] for w in members)
        if _SIZE.match(text):
            box = fitz.Rect(members[0][:4])
            for w in members[1:]:
                box |= fitz.Rect(w[:4])
            out.append((text, box))
    return out


def _size_under(mark: fitz.Rect, sizes: list[tuple[str, fitz.Rect]]) -> str | None:
    """The size printed DIRECTLY under a mark: starting within one line height
    below it and overlapping it horizontally.

    By position, not by line number. The first version took the next line of
    the same text block, and the client's sheet numbers its lines in no visual
    order — C-13 at 4/E came out as "C-13 (14 x 30)", the size of the C-6 beside
    it, where the drawing prints (22 x 22). A wrong size attached to a real
    mark is worse than no size, so anything not plainly under the mark is left
    off. Measured in the page's own (unrotated) text frame, where "the line
    under" is what the drafter wrote.
    """
    height = mark.height or 1.0
    best = None
    for text, box in sizes:
        gap = box.y0 - mark.y1
        if not -0.25 * height <= gap <= 0.9 * height:
            continue
        if box.x1 < mark.x0 - height or box.x0 > mark.x1 + height:
            continue
        if best is None or gap < best[0]:
            best = (gap, text)
    return best[1] if best else None


def _words_with_sizes(page: fitz.Page) -> list[tuple[str, fitz.Rect]]:
    """Mark-shaped words, each with the size printed under it when there is one."""
    words = page.get_text("words")
    sizes = _size_lines(words)
    to_display = ~page.derotation_matrix
    out = []
    for x0, y0, x1, y1, text, *_ in words:
        text = text.strip(",;:")
        if not MARK.match(text):
            continue
        box = fitz.Rect(x0, y0, x1, y1)
        size = _size_under(box, sizes)
        out.append((f"{text} {size}" if size else text, box * to_display))
    return out


def _add(marks: list[str], text: str) -> None:
    """Keep one entry per mark: the text layer often draws a mark twice, and
    sometimes only one copy has its size under it — "C-3 (14 x 30), C-3" is one
    column, not two."""
    base = text.split(" ", 1)[0]
    if text in marks:
        return
    if text == base and any(m.split(" ", 1)[0] == base for m in marks):
        return
    if text != base and base in marks:
        marks[marks.index(base)] = text
        return
    marks.append(text)


def read(page: fitz.Page) -> GridMarks | None:
    """The sheet's marks placed on its grid, or None when it has no grid."""
    # Text first: it is cheap, and a page with no mark-shaped word cannot
    # produce anything. The grid read (a vector scan) cost 2.3s on the
    # client's architectural sheet, and most pages of a set are notes,
    # schedules and details that never needed it.
    found = _words_with_sizes(page)
    if not found:
        return None
    columns, rows = grid.page_grid(page)
    if not columns or not rows:
        return None
    pairs = grid.intersections(columns, rows)
    if len(pairs) < 2:
        return None
    result = GridMarks(columns, rows, pairs)
    # Pairs are reported in grid order, never string order ("3.5/A" < "3/A").
    order = {f"{c}/{r}": i for i, (c, r, _, _) in enumerate(pairs)}
    between: dict[tuple[str, str], list[str]] = {}
    for text, rect in found:
        cx, cy = (rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2
        ranked = sorted((math.hypot(cx - x, cy - y), f"{c}/{r}") for c, r, x, y in pairs)
        (d1, home), (d2, runner_up) = ranked[0], ranked[1]
        if d1 > MAX_PT:
            continue
        if d2 - d1 < MARGIN_PT:
            _add(between.setdefault(tuple(sorted((home, runner_up), key=order.get)), []), text)
            continue
        _add(result.at.setdefault(home, []), text)
    result.between = [(text, a, b) for (a, b), texts in between.items() for text in texts]
    return result


def describe(marks: GridMarks) -> str | None:
    """The reading as lines the chat can cite, or None when no mark was placed."""
    if not marks.at and not marks.between:
        return None
    columns = list(dict.fromkeys(c for c, _, _, _ in marks.pairs))
    rows = list(dict.fromkeys(r for _, r, _, _ in marks.pairs))
    lines = [
        "Grid marks, read from where each mark is PRINTED on this sheet relative to its grid "
        "(the PDF's own text positions, not a vision model). A mark listed at a crossing is "
        "printed nearer to that crossing than to any other; a mark drawn elsewhere with a "
        "leader line would not be caught. Crossings not listed have no mark printed at them.",
        f"Column lines: {', '.join(columns)}",
        f"Row lines: {', '.join(rows)}",
        *grid.orientation(marks.pairs),
    ]
    for col, row, _, _ in marks.pairs:
        label = f"{col}/{row}"
        if label in marks.at:
            lines.append(f"At {label}: {', '.join(marks.at[label])}.")
    for text, a, b in marks.between:
        lines.append(f"{text} is printed between {a} and {b}, equally near both; it is not assigned to either.")
    return "\n".join(lines)


def chunks_for(page: fitz.Page) -> list[chunker.Chunk]:
    """The chunks to store for this page — empty for a page with no grid or no
    marks on it, which is most pages. Only the body is split, and every piece
    carries the explanation line, because a piece without it reads as an
    unexplained list and the chat would not know it was measured."""
    marks = read(page)
    text = describe(marks) if marks else None
    if not text:
        return []
    header, body = text.split("\n", 1)
    pieces = chunker.split_description(
        body,
        marks.extent,
        kind="gridmarks",
        source_model=SOURCE,
        source_settings={"reader": SOURCE, "maxPt": MAX_PT, "marginPt": MARGIN_PT},
    )
    for i, piece in enumerate(pieces):
        piece.text = f"{header}\n{'(continued)' + chr(10) if i else ''}{piece.text}"
        piece.token_count = chunker.estimate_tokens(piece.text)
    return pieces
