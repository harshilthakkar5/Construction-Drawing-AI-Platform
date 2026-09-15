#!/usr/bin/env python3
"""Derive drawing-geometry ground truth from a sheet, for the Phase 0 eval.

Why this exists
---------------
`retrieval_eval.mjs` measures whether the chunk that answers a question comes
back. On a text-heavy set it now reports 100% recall — and that is the honest
number, because the chunk really is retrieved. It is also the whole story that
retrieval can tell, and it hides the gap this file exists to measure.

A construction drawing carries two kinds of fact:

  * VOCABULARY — every footing mark, every member size, every schedule row.
    `page.get_text()` has all of it, retrieval finds it, chat can quote it.
  * GEOMETRY — which column sits on which footing, at which grid intersection.
    `page.get_text()` does NOT have it. On the sheet this was written against
    the text stream reads:

        ... HSS8X8X1/2  HSS6X6X1/2  HSS8X8X3/8 ...
        ... F13  F13  F10  F9  F9  F12  F9  F8 ...

    Every member size in one run, every footing mark in another. The pairing is
    carried by a diagonal leader line, which is not text at all. No chunker
    recovers it, because there is nothing there to recover.

So the questions that would show a VLM earning its cost are geometry questions,
and the answers have to come from the PDF's coordinates rather than from
someone's reading — including mine. Asked for the footing at grid 7/C by hand I
answered F10, having measured against the drawing frame's zone markers (evenly
spaced letters printed on the sheet border) instead of the building grid
bubbles. The right answer is F12. The frame markers and the grid bubbles look
identical in a text dump; only the geometry separates them.

Hence: ground truth is computed, and a case that cannot be computed with
confidence is REFUSED rather than guessed. Same contract as the retrieval
preflight — a benchmark may report bad news, it may never invent it.

How a case is derived
---------------------
1. Grid bubbles are circles (a curve-drawn shape, roughly square, 30-45pt)
   containing exactly ONE word that looks like a grid label. A detail callout
   is the same circle with TWO words ("6", "S-301.0"), so the token count
   separates them. Frame zone markers are not circled at all.
2. Bubbles sharing a y are the column grid; bubbles sharing an x are the row
   grid. Both need >= MIN_AXIS members, so a stray circle cannot invent an axis.
3. An intersection is (column.x, row.y). The answer is the nearest label of the
   class being asked about.
4. THE REFUSAL. The intersection is jittered by +/- JITTER in eight directions
   and the nearest label recomputed from each. All nine readings must agree. A
   footing whose label is equidistant between two intersections moves under
   jitter and is dropped — which is exactly the case a human would get wrong
   too, and therefore exactly the case a benchmark must not score.

Usage
    python benchmarks/drawing_truth.py --pdf sheet.pdf --project <uuid>
    python benchmarks/drawing_truth.py --pdf sheet.pdf --project <uuid> \
        --out benchmarks/drawing_eval_set.json
    python benchmarks/drawing_truth.py --pdf sheet.pdf --explain   # show refusals

Needs only PyMuPDF; run it with the worker venv.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys

import fitz

# A grid bubble's circle, in points. Detail callouts share these dimensions —
# they are told apart by how many words sit inside, not by size.
BUBBLE_MIN_PT = 30.0
BUBBLE_MAX_PT = 45.0
BUBBLE_ASPECT = (0.85, 1.18)

# A grid label: "7", "4.6", "11.3", "B", "DD". A detail callout's second token
# ("S-301.0") fails this, and so does anything from the drawing body.
GRID_LABEL = re.compile(r"\d+(?:\.\d+)?|[A-Z]{1,2}")

# Bubbles are on an axis if their cross-coordinate agrees within this. Drawn
# grid lines are exact; the tolerance absorbs only the bubble's own centring.
AXIS_TOL_PT = 6.0

# An axis needs this many bubbles. Two points define a line through any pair of
# strays; three is the smallest number that has to be deliberate.
MIN_AXIS = 3

# A label further than this from the intersection is not labelling it. ~1.4in
# on the sheet, comfortably more than the offset a drafter uses and comfortably
# less than the spacing between grid lines.
MAX_LABEL_PT = 100.0

# The refusal radius. Larger than a drafter's label offset, smaller than half a
# bay, so the reading moves only where it was genuinely ambiguous.
JITTER_PT = 20.0

FOOTING_MARK = re.compile(r"F\d{1,2}")
MEMBER_CALLOUT = re.compile(r"HSS[0-9.].*")

# The SHAPE a label of each kind takes, as an anchored token, emitted into every
# case so the scorer can recognise a label the model INVENTED — one formed like
# a real mark but written nowhere on this sheet.
#
# It is separate from the two patterns above, which are extraction patterns run
# against the PDF's own text and are deliberately loose because whatever they
# match is real by construction. A scorer has the opposite problem: it sees only
# the model's prose, where a shape is all there is to go on. And the distinction
# matters — one description of this sheet answered nearly every column question
# with HSS9X9X3/8 — a real AISC square section, which is what makes it plausible,
# and one that appears nowhere on THIS drawing, whose columns are HSS8X8, HSS6X6
# and HSS10X10. Scored against the sheet's own vocabulary that is not a label at
# all, so all 21 cases counted as ABSTAINED: the most dangerous answer a model
# can give about a column, filed as the safest.
#
# The pattern travels WITH the cases rather than being written again in
# JavaScript, so the two languages cannot drift; `\s*` is there because a model
# writes "HSS 8x8x3/8" for a sheet that says "HSS8X8X3/8". Both forms are valid
# in Python's `re` and in JavaScript's RegExp.
LABEL_PATTERN = {
    "grid-footing": r"F\d{1,2}",
    "grid-column": (
        r"HSS\s*\d+(?:\.\d+)?\s*X\s*\d+(?:\.\d+)?(?:\s*/\s*\d+)?"
        r"(?:\s*X\s*\d+(?:\.\d+)?(?:\s*/\s*\d+)?)?"
    ),
}


def _centre(rect: fitz.Rect) -> tuple[float, float]:
    return ((rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2)


def grid_bubbles(page: fitz.Page) -> list[tuple[str, float, float]]:
    """Circled grid labels, in the page's DISPLAYED coordinate space.

    Display space because that is where the user and the renderer both live;
    `get_text` and `get_drawings` report unrotated coordinates, so everything is
    mapped once, here, rather than at each comparison.
    """
    to_display = ~page.derotation_matrix
    words = page.get_text("words")
    out: list[tuple[str, float, float]] = []
    for drawing in page.get_drawings():
        rect = drawing["rect"]
        if rect.width <= 0 or rect.height <= 0:
            continue
        if not BUBBLE_MIN_PT < rect.width < BUBBLE_MAX_PT:
            continue
        if not BUBBLE_ASPECT[0] < rect.width / rect.height < BUBBLE_ASPECT[1]:
            continue
        if not any(item[0] == "c" for item in drawing["items"]):
            continue
        inside = [
            w
            for x0, y0, x1, y1, w, *_ in words
            if rect.x0 <= (x0 + x1) / 2 <= rect.x1 and rect.y0 <= (y0 + y1) / 2 <= rect.y1
        ]
        # Exactly one word: two means a detail callout ("6" + "S-301.0"), which
        # is a reference to another sheet and not a grid line at all.
        if len(inside) == 1 and GRID_LABEL.fullmatch(inside[0]):
            out.append((inside[0], *_centre(rect * to_display)))
    return out


def axes(bubbles: list[tuple[str, float, float]]) -> tuple[dict, dict]:
    """Split bubbles into the column grid (a shared y) and the row grid (a
    shared x). A sheet's angled wing has bubbles on neither and is skipped."""

    def cluster(index: int) -> list[list[tuple[str, float, float]]]:
        groups: list[list[tuple[str, float, float]]] = []
        for bubble in sorted(bubbles, key=lambda b: b[index + 1]):
            value = bubble[index + 1]
            if groups and abs(groups[-1][-1][index + 1] - value) <= AXIS_TOL_PT:
                groups[-1].append(bubble)
            else:
                groups.append([bubble])
        return [g for g in groups if len(g) >= MIN_AXIS]

    # Columns share a y (index 1); rows share an x (index 0).
    col_groups = cluster(1)
    row_groups = cluster(0)
    columns = {b[0]: b[1] for g in col_groups for b in g}
    rows = {b[0]: b[2] for g in row_groups for b in g}
    return columns, rows


def labels_of(page: fitz.Page, pattern: re.Pattern) -> list[tuple[str, float, float]]:
    to_display = ~page.derotation_matrix
    out = []
    for x0, y0, x1, y1, word, *_ in page.get_text("words"):
        if pattern.fullmatch(word):
            out.append((word, *_centre(fitz.Rect(x0, y0, x1, y1) * to_display)))
    return out


def nearest(labels, x: float, y: float) -> tuple[str, float] | None:
    best = None
    for label, lx, ly in labels:
        d = math.hypot(lx - x, ly - y)
        if best is None or d < best[1]:
            best = (label, d)
    return best


def stable_reading(labels, x: float, y: float) -> tuple[str, float, str] | None:
    """The label at (x, y), or None with a reason if it is not unambiguous.

    Returns (label, distance, "") on success and (None, 0, reason) on refusal —
    the reason is reported so a dropped case can be inspected rather than
    silently vanishing.
    """
    here = nearest(labels, x, y)
    if here is None:
        return None, 0.0, "no label of this kind on the sheet"
    label, distance = here
    if distance > MAX_LABEL_PT:
        return None, 0.0, f"nearest label {label} is {distance:.0f}pt away (limit {MAX_LABEL_PT:.0f})"
    for dx in (-JITTER_PT, 0.0, JITTER_PT):
        for dy in (-JITTER_PT, 0.0, JITTER_PT):
            moved = nearest(labels, x + dx, y + dy)
            if moved is None or moved[0] != label:
                other = moved[0] if moved else "nothing"
                return None, 0.0, f"reading moves {label} -> {other} under {JITTER_PT:.0f}pt jitter"
    return label, distance, ""


def runner_up(labels, x: float, y: float, exclude: str) -> str | None:
    """The nearest label carrying a DIFFERENT value — the distractor a wrong
    answer would most plausibly reach for, and what the runner scores against."""
    best = None
    for label, lx, ly in labels:
        if label == exclude:
            continue
        d = math.hypot(lx - x, ly - y)
        if best is None or d < best[1]:
            best = (label, d)
    return best[0] if best else None


def build(pdf: str, project_id: str, sheet: str | None, explain: bool) -> list[dict]:
    doc = fitz.open(pdf)
    cases: list[dict] = []
    refusals: list[str] = []

    for page_index, page in enumerate(doc):
        bubbles = grid_bubbles(page)
        columns, rows = axes(bubbles)
        if not columns or not rows:
            refusals.append(f"page {page_index + 1}: no orthogonal grid found")
            continue
        footings = labels_of(page, FOOTING_MARK)
        members = labels_of(page, MEMBER_CALLOUT)
        sheet_name = sheet or f"page {page_index + 1}"

        for col, cx in sorted(columns.items()):
            for row, cy in sorted(rows.items()):
                for kind, labels, question in (
                    (
                        "grid-footing",
                        footings,
                        f"On sheet {sheet_name}, which footing mark is at the intersection of "
                        f"column line {col} and row line {row}?",
                    ),
                    (
                        "grid-column",
                        members,
                        f"On sheet {sheet_name}, what column section is called out at the "
                        f"intersection of column line {col} and row line {row}?",
                    ),
                ):
                    label, distance, reason = stable_reading(labels, cx, cy)
                    if label is None:
                        refusals.append(f"{sheet_name} {col}/{row} {kind}: {reason}")
                        continue
                    cases.append(
                        {
                            "projectId": project_id,
                            "tag": kind,
                            "question": question,
                            "expected": label,
                            "distractor": runner_up(labels, cx, cy, label),
                            "labelPattern": LABEL_PATTERN[kind],
                            "derivation": {
                                "sheet": sheet_name,
                                "gridColumn": col,
                                "gridRow": row,
                                "intersectionPt": [round(cx, 1), round(cy, 1)],
                                "labelDistancePt": round(distance, 1),
                            },
                        }
                    )

    if explain:
        print(f"refused {len(refusals)} candidate cases:", file=sys.stderr)
        for line in refusals:
            print(f"  - {line}", file=sys.stderr)
    print(
        f"{len(cases)} cases emitted, {len(refusals)} refused "
        f"(run with --explain to see why)",
        file=sys.stderr,
    )
    return cases


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--project", default="", help="projectId the questions are asked against")
    ap.add_argument("--sheet", default=None, help="sheet number, e.g. S-100.0")
    ap.add_argument("--out", default=None)
    ap.add_argument("--explain", action="store_true", help="list refused cases")
    args = ap.parse_args()

    cases = build(args.pdf, args.project, args.sheet, args.explain)
    text = json.dumps(cases, indent=2) + "\n"
    if args.out:
        with open(args.out, "w") as fh:
            fh.write(text)
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
