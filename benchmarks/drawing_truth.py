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
from pathlib import Path

import fitz

# Finding the grid is no longer this file's job. The vision pass needs the same
# bubbles to decide what to crop, and a grid detected twice is a grid that
# drifts — see workers/src/grid.py, which also records what sharing it costs.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "workers" / "src"))

import grid  # noqa: E402

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


def labels_of(page: fitz.Page, pattern: re.Pattern) -> list[tuple[str, float, float]]:
    to_display = ~page.derotation_matrix
    out = []
    for x0, y0, x1, y1, word, *_ in page.get_text("words"):
        if pattern.fullmatch(word):
            out.append((word, *grid.centre(fitz.Rect(x0, y0, x1, y1) * to_display)))
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
        columns, rows = grid.axes(grid.bubbles(page))
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
                            # EVERY label of this kind on the page, not just the
                            # two this case turns on. The scorer needs it to
                            # separate "named another mark from this drawing"
                            # (off-target) from "named something that is on no
                            # part of it" (invented), and it cannot build that
                            # from the case set: a set only names the
                            # intersections it asks about, so a real mark
                            # sitting at an intersection nobody asked about
                            # scores INVENTED. That is not a harmless
                            # mislabel — invented is the outcome that says the
                            # model made something up.
                            "sheetLabels": sorted({lab for lab, _, _ in labels}),
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


def backfill_label_patterns(cases: list[dict]) -> int:
    """Add `labelPattern` to cases generated before it existed. Returns the count.

    A set without it still RUNS, and that is the problem: the scorer cannot tell
    a label the model INVENTED from a refusal to answer, so both land in
    "abstained" — the most dangerous outcome filed as the safest. Regenerating
    from the PDF is the real fix, because it re-derives the truth too. This is
    for when the PDF is not to hand, and it is safe precisely because the
    pattern is a function of the TAG alone: it depends on no geometry, so
    writing it here produces byte-identical output to a full regeneration.

    It still comes from LABEL_PATTERN above rather than from a second table —
    the whole point of the field is that the shape of a mark is defined once,
    in Python, and travels with the cases.
    """
    filled = 0
    for case in cases:
        if case.get("labelPattern"):
            continue
        pattern = LABEL_PATTERN.get(case.get("tag", ""))
        if pattern:
            case["labelPattern"] = pattern
            filled += 1
    return filled


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pdf", default=None)
    ap.add_argument("--project", default="", help="projectId the questions are asked against")
    ap.add_argument("--sheet", default=None, help="sheet number, e.g. S-100.0")
    ap.add_argument("--out", default=None)
    ap.add_argument("--explain", action="store_true", help="list refused cases")
    ap.add_argument(
        "--backfill",
        default=None,
        metavar="SET.JSON",
        help="add labelPattern to an existing set instead of deriving one from a PDF; "
        "writes in place unless --out is given. Does NOT re-derive any answer.",
    )
    args = ap.parse_args()

    if args.backfill:
        with open(args.backfill) as fh:
            cases = json.load(fh)
        filled = backfill_label_patterns(cases)
        out = args.out or args.backfill
        with open(out, "w") as fh:
            fh.write(json.dumps(cases, indent=2) + "\n")
        print(f"wrote {out}: {filled} of {len(cases)} cases gained a labelPattern", file=sys.stderr)
        return 0

    if not args.pdf:
        ap.error("--pdf is required unless --backfill is given")

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
