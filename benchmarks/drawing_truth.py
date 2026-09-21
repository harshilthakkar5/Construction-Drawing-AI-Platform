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
from collections import Counter
from pathlib import Path
from typing import NamedTuple

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
# Every tag this generator can emit. Named here rather than derived from the
# cases, because a tag that emitted nothing has to be reportable and a set
# cannot name what it does not contain.
#
# An INTERSECTION tag is ONE TABLE ROW, and it has to be. The three things a
# tag needs drift apart the moment they are written apart: the pattern that
# finds the label on the PDF, the pattern the SCORER recognises it by, and the
# question. This file kept the first two in separate dicts and the third inline
# in the emission loop, which was survivable while one sheet defined the
# vocabulary and stopped being survivable the moment a second sheet marked its
# foundations PC1 rather than F9 — three regions to edit in step, and a tag
# whose cases carry another tag's shape is a set that scores the wrong
# vocabulary while looking well-formed.
class LabelClass(NamedTuple):
    """One kind of label, and everything a case about it needs."""

    # Run against the PDF's own words, via `fullmatch`. Deliberately LOOSE:
    # whatever it matches is real by construction, because it came off the
    # drawing.
    extract: re.Pattern
    # The shape the SCORER recognises, emitted into every case. The opposite
    # problem — it sees only the model's prose, where a shape is all there is
    # to go on — so it is strict. It is what separates a label the model
    # INVENTED from one it declined to give.
    shape: str
    # Filled per case with {sheet}, {col} and {row}. It must never name a
    # candidate answer. A question that lists the marks to choose from measures
    # the prompt rather than the drawing, which is the same prohibition
    # `test_the_prompt_never_seeds_an_answer_from_the_sheet_under_test` puts on
    # the vision prompt, for the same reason.
    question: str


# A sheet records the SAME geometric fact in one of two notations, and which
# one it uses is a house style rather than a difference in the drawing:
#
#   * the value printed at the intersection — F9, HSS8X8X3/8
#   * a MARK keyed to a schedule elsewhere in the set — PC1, C3
#
# Both are the fact the text layer cannot hold: `page.get_text()` returns every
# mark on the sheet in one run, attached to nothing, and what joins a mark to
# its intersection is drawn rather than written. So a marked sheet asks exactly
# the question this benchmark exists to ask, and the first sheet's vocabulary
# simply could not see it — S101P scored ZERO cases from 329 candidates, every
# footing candidate refused with "nearest label F2 is 444pt away", because the
# only F-marks on it are in a detail a third of a sheet away.
#
# They are four tags and not two on purpose. A mark and a section are different
# ANSWER vocabularies with different majority-class baselines, and pooling two
# vocabularies under one tag is the pooled-baseline mistake this project has
# already paid for once: the null model's score stops describing either half.
INTERSECTION_TAGS: dict[str, LabelClass] = {
    "grid-footing": LabelClass(
        re.compile(r"F\d{1,2}"),
        r"F\d{1,2}",
        "On sheet {sheet}, which footing mark is at the intersection of "
        "column line {col} and row line {row}?",
    ),
    "grid-column": LabelClass(
        re.compile(r"HSS[0-9.].*"),
        (
            r"HSS\s*\d+(?:\.\d+)?\s*X\s*\d+(?:\.\d+)?(?:\s*/\s*\d+)?"
            r"(?:\s*X\s*\d+(?:\.\d+)?(?:\s*/\s*\d+)?)?"
        ),
        "On sheet {sheet}, what column section is called out at the "
        "intersection of column line {col} and row line {row}?",
    ),
    "grid-pilecap": LabelClass(
        re.compile(r"PC\d{1,2}"),
        r"PC\d{1,2}",
        "On sheet {sheet}, which pile cap mark is at the intersection of "
        "column line {col} and row line {row}?",
    ),
    # No `\s*` in the shape, unlike the member size. "HSS 8x8x3/8" is a real
    # way to write a section and "C 3" is not a way to write a schedule key,
    # so allowing the space here would only let the scorer read the "C 3" in
    # prose like "row line C 3 bays over" as a mark.
    "grid-colmark": LabelClass(
        re.compile(r"C\d{1,2}"),
        r"C\d{1,2}",
        "On sheet {sheet}, which column mark is called out at the "
        "intersection of column line {col} and row line {row}?",
    ),
}

TAGS = tuple(INTERSECTION_TAGS) + ("grid-spacing",)

# Derived, so a tag cannot exist with a shape the scorer never receives.
LABEL_PATTERN = {tag: cls.shape for tag, cls in INTERSECTION_TAGS.items()}
# A dimension as a drafter writes it: 26' - 2 1/2". Every separator is
# optional-whitespace tolerant for the same reason the member size is — the
# sheet writes "26' - 2 1/2"" and a model writes "26'-2 1/2"".
LABEL_PATTERN["grid-spacing"] = r"\d+\s*'\s*-?\s*\d+(?:\s*\d+\s*/\s*\d+)?\s*\""


# A dimension string as this sheet prints it. Unlike a footing mark or a member
# size, a dimension is NOT one word: `get_text("words")` splits `26' - 2 1/2"`
# into four. It is read off SPANS instead, and only a span whose whole text is
# one dimension is taken — a span holding two would have one bbox and two
# positions, and guessing which half sits where is exactly the kind of
# approximation this file refuses everywhere else.
DIMENSION = re.compile(r"\d+'\s*-?\s*\d+(?:\s+\d+/\d+)?\"")

# Which way a dimension is written, in DISPLAY space: a column gap is a
# horizontal distance, so only a run going ACROSS can measure it.
ACROSS = "across"
DOWN = "down"

# How much the dominant component must beat the other for a run to count as
# along an axis at all. 2 admits about 27 degrees of slop and refuses the rest.
AXIS_RATIO = 2.0


def runs_along(across: float, down: float) -> str | None:
    """Which axis a text run lies along, or None if it is not clearly on one.

    Pure, and separate from `dimensions_of`, because the boundary is the part
    worth pinning and it cannot be reached through a PDF: constructing a page
    whose text sits at exactly `atan(1/AXIS_RATIO)` is a floating-point
    coincidence, not a test. Here the numbers go in directly.

    The threshold is inclusive — twice IS twice — and a run at 30 degrees,
    where the ratio is 1.73, belongs to neither axis.
    """
    across, down = abs(across), abs(down)
    if across >= down * AXIS_RATIO and across > 0:
        return ACROSS
    if down >= across * AXIS_RATIO and down > 0:
        return DOWN
    return None


def dimensions_of(page: fitz.Page) -> list[tuple[str, float, float, str | None]]:
    """Every dimension string on the page: (text, x, y, which way it runs).

    ORIENTATION is the fourth field and it is not decoration. Containment on
    one axis cannot tell a horizontal dimension from a vertical one, so a bay
    dimension written across the top of the plan also sits inside every ROW
    gap it happens to span — and would be emitted as the answer to "what is
    between row lines B and C", which is a horizontal measurement offered for
    a vertical distance. A drafter settles it the way a reader does: a
    dimension measuring a horizontal distance is written horizontally, and one
    measuring a vertical distance is rotated to run with it. `line["dir"]` is
    the writing direction, (1, 0) across and (0, ±1) down.

    If the convention does not hold on some sheet the case REFUSES for want of
    a dimension rather than emitting a wrong one, which is the direction an
    error here has to fall.

    The direction is MAPPED into display space like the bbox is, and that is
    the whole of what this function gets wrong if it is skipped. `get_text`
    reports in the page's unrotated system — the same reason a clip has to be
    multiplied by `derotation_matrix` — so on a /Rotate 90 sheet a line reading
    across the drawing comes back as `dir=(1, 0)` while its bbox, once mapped,
    runs down the display. Position in one space and orientation in the other
    is not an approximation, it is an exact 90-degree inversion: every column
    gap then looks for text that is vertical on the sheet and every row gap for
    text that is horizontal. A `dir` is a VECTOR, so only the matrix's linear
    part applies; translating it would move a direction to a place.

    The fourth field is `"across"`, `"down"` or None, and None is not a
    formality. A run that is not clearly along one axis belongs to NEITHER —
    forcing it onto the nearer one would let a rotated note or a skewed callout
    answer a bay question, and this module refuses everywhere else it cannot
    tell. `runs_along` is what "clearly" means: the dominant component must be
    at least twice the other, so a run within about 27 degrees of an axis
    counts and anything more diagonal is dropped. It stays in `sheetLabels` either way,
    since it IS written on the drawing and the scorer would otherwise call it
    invented.
    """
    to_display = ~page.derotation_matrix
    out = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            dx, dy = line.get("dir", (1.0, 0.0))
            runs = runs_along(
                dx * to_display.a + dy * to_display.c,
                dx * to_display.b + dy * to_display.d,
            )
            for span in line.get("spans", []):
                text = span["text"].strip()
                if not DIMENSION.fullmatch(text):
                    continue
                x, y = grid.centre(fitz.Rect(span["bbox"]) * to_display)
                out.append((text, x, y, runs))
    return out


def dimension_between(dimensions, low: float, high: float, axis: int):
    """The single dimension printed inside the gap (low, high) on one axis.

    Returns (value, "") or (None, reason). `axis` is 0 for the x coordinate
    (a column bay) and 1 for y (a row bay).

    Containment rather than nearest-neighbour, because a dimension is not a
    LABEL of the gap — it is written ALONG it, anywhere on a dimension line
    that may sit far above or below the plan. The distance from the two grid
    lines says nothing; being between them says everything.

    Ambiguity is refused rather than resolved. A structural sheet carries
    several dimension chains at once — the bay run, an overall dimension, a
    partial to a slab edge — and more than one distinct value inside one gap
    means the question "what is between 7 and 8" has more than one true answer.
    Two spans reading the SAME value are one answer written twice and are fine.
    """
    # Items may be (text, x, y) or (text, x, y, horizontal) — the orientation
    # filter belongs to the CALLER, which knows what the sheet's conventions
    # are, and not to this arithmetic.
    inside = {
        item[0]
        for item in dimensions
        if low < (item[1], item[2])[axis] < high
    }
    if not inside:
        return None, "no dimension printed inside this gap"
    if len(inside) > 1:
        return None, f"{len(inside)} different dimensions inside this gap: {', '.join(sorted(inside))}"
    value = inside.pop()
    # The same jitter test the label readings get, applied to the boundary
    # rather than to a point: widen and narrow the gap by JITTER_PT and the
    # answer must not change. A dimension sitting within 20pt of a grid line
    # belongs to whichever side the rounding fell on, which is not an answer.
    for grow in (-JITTER_PT, JITTER_PT):
        moved = {
            item[0]
            for item in dimensions
            if low - grow < (item[1], item[2])[axis] < high + grow
        }
        if moved != {value}:
            return None, (
                f"the reading moves under {JITTER_PT:.0f}pt of boundary jitter: "
                f"{value} -> {', '.join(sorted(moved)) or 'nothing'}"
            )
    return value, ""


def adjacent_pairs(positions: dict[str, float]) -> list[tuple[str, str, float, float]]:
    """Neighbouring grid lines on one axis, ordered by position.

    Only ADJACENT pairs: "between 7 and 9" spans a line and has no single
    dimension, and asking it would score a model for refusing to answer a
    question the drawing does not answer either.
    """
    ordered = sorted(positions.items(), key=lambda kv: kv[1])
    return [
        (a[0], b[0], a[1], b[1])
        for a, b in zip(ordered, ordered[1:])
    ]


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


def names_its_own_intersection(label: str, col: str, row: str) -> bool:
    """True when a mark cannot be told apart from its own intersection's name.

    S101P marks its columns C1..C4 against a schedule, names its row lines A..H
    and its column lines 1..19 — so the mark `C1`, at the intersection of
    column line 1 and row line C, is the same two characters as the shorthand
    anyone would write for that intersection. An answer of "C1" is then both
    the truth and a restatement of the question, and nothing in the reply
    separates them: the case would score CORRECT for a model that read the
    question and never looked at the drawing.

    So it is refused, for the same reason a label sitting equidistant between
    two intersections is. Neither refusal says the drawing is unclear — the
    drawing is fine, and a person reading it has the leader line to follow.
    What is ambiguous is the ANSWER, and a benchmark cannot score an answer it
    cannot read. Both orders are checked because a sheet may write either.
    """
    return label in (f"{row}{col}", f"{col}{row}")


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


def _neighbour_bay(dimensions, positions, first, second, axis, exclude):
    """The dimension in a gap ADJOINING this one, as the distractor.

    Chosen the same way `runner_up` chooses one for a label: the most plausible
    wrong answer, not an arbitrary other value. A model that reads the
    dimension chain and loses its place by one names its neighbour.
    """
    pairs = adjacent_pairs(positions)
    for index, (a, b, _, _) in enumerate(pairs):
        if (a, b) != (first, second):
            continue
        for step in (index - 1, index + 1):
            if not 0 <= step < len(pairs):
                continue
            value, _ = dimension_between(dimensions, pairs[step][2], pairs[step][3], axis)
            if value and value != exclude:
                return value
    return None


def tally(cases: list[dict], refusals: list[str], expected_tags) -> str:
    """The emitted count, broken down by TAG, naming any tag that died.

    A total is a liveness signal only if you already know what it should be.
    "40 cases emitted, 24 refused" looked healthy on a run where the spacing
    tag produced ZERO — 40 is exactly the footing-plus-column count, so the
    number that should have raised the alarm was the one that looked normal.
    A tag with no cases is not a smaller set, it is a question the benchmark
    has stopped asking, and it is invisible in every figure the report prints
    afterwards.
    """
    counts = Counter(case["tag"] for case in cases)
    breakdown = ", ".join(f"{tag} {counts.get(tag, 0)}" for tag in expected_tags)
    lines = [
        f"{len(cases)} cases emitted ({breakdown}), {len(refusals)} refused "
        f"(run with --explain to see why)"
    ]
    dead = [tag for tag in expected_tags if counts.get(tag, 0) == 0]
    if dead:
        lines.append(
            f"  NO CASES for {', '.join(dead)} — that tag asks nothing of this set, "
            f"and nothing downstream will say so again."
        )
    return "\n".join(lines)


def build(pdf: str, project_id: str, sheet: str | None, explain: bool) -> list[dict]:
    doc = fitz.open(pdf)
    cases: list[dict] = []
    refusals: list[str] = []

    for page_index, page in enumerate(doc):
        columns, rows = grid.axes(grid.bubbles(page))
        if not columns or not rows:
            refusals.append(f"page {page_index + 1}: no orthogonal grid found")
            continue
        found = {
            tag: labels_of(page, cls.extract)
            for tag, cls in INTERSECTION_TAGS.items()
        }
        dimensions = dimensions_of(page)
        sheet_name = sheet or f"page {page_index + 1}"

        # The spacing tag asks what the crop pass cannot answer. A crop
        # description holds a grid header and one line per intersection and
        # says nothing about what lies BETWEEN them, so a bay dimension is the
        # narrowest question that separates the two vision modes. It is a
        # geometry question in exactly the sense the footing tag is: the value
        # is in the text layer, and WHICH gap it belongs to is not — the text
        # stream returns every dimension on the sheet in one run, associated
        # with nothing. A chunker recovers the numbers and never the pairing.
        for axis, (name, positions, other) in enumerate(
            (("column line", columns, "row"), ("row line", rows, "column")),
        ):
            # A column gap is a horizontal distance, so only horizontally
            # written dimensions can measure it, and vice versa.
            on_axis = [d for d in dimensions if d[3] == (ACROSS if axis == 0 else DOWN)]
            for first, second, low, high in adjacent_pairs(positions):
                value, reason = dimension_between(on_axis, low, high, axis)
                if value is None:
                    refusals.append(f"{sheet_name} {first}-{second} grid-spacing: {reason}")
                    continue
                cases.append(
                    {
                        "projectId": project_id,
                        "tag": "grid-spacing",
                        "question": (
                            f"On sheet {sheet_name}, what is the dimension between "
                            f"{name} {first} and {name} {second}?"
                        ),
                        "expected": value,
                        # The neighbouring bay: the value a model reaching one
                        # gap over would name, which is the drift this tag can
                        # see and the one a crop run cannot commit at all.
                        "distractor": _neighbour_bay(on_axis, positions, first, second, axis, value),
                        "labelPattern": LABEL_PATTERN["grid-spacing"],
                        "sheetLabels": sorted({d[0] for d in dimensions}),
                        "derivation": {
                            "sheet": sheet_name,
                            "axis": name,
                            "between": [first, second],
                            "gapPt": round(high - low, 1),
                        },
                    }
                )

        # Via grid.intersections rather than a nested loop here, so the
        # generator and the vision pass cannot disagree about what "4/B" means.
        for col, row, cx, cy in grid.intersections(columns, rows):
            for kind, cls in INTERSECTION_TAGS.items():
                labels = found[kind]
                question = cls.question.format(sheet=sheet_name, col=col, row=row)
                label, distance, reason = stable_reading(labels, cx, cy)
                if label is None:
                    refusals.append(f"{sheet_name} {col}/{row} {kind}: {reason}")
                    continue
                if names_its_own_intersection(label, col, row):
                    refusals.append(
                        f"{sheet_name} {col}/{row} {kind}: the mark {label} is also how "
                        f"this intersection is written"
                    )
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
    print(tally(cases, refusals, TAGS), file=sys.stderr)
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


def _reach_to(point, rect) -> float:
    """How far a label must sit from `point` before it can fall inside `rect`.

    Zero if the point is already inside. A label's position is not recorded —
    only its DISTANCE from its intersection — so this is the honest form of the
    question: a label `d` away lies somewhere on a circle of radius `d`, and if
    `d` reaches the neighbour's rectangle then the neighbour's crop MAY contain
    it. That is an upper bound on contamination, which is the right direction
    for a warning: it can rule contamination out and never in.
    """
    px, py = point
    dx = max(rect.x0 - px, 0.0, px - rect.x1)
    dy = max(rect.y0 - py, 0.0, py - rect.y1)
    return (dx * dx + dy * dy) ** 0.5


def _report_crop_overlap(boxes_by_page, by_label) -> None:
    """Whose OTHER labels a crop can contain — the half `--against` never asked.

    The existing check asks "is my label inside my crop?" and a sheet can pass
    it completely while every crop also contains its neighbour's labels. Those
    are different questions with different consequences, and the second one is
    the whole point of cropping: a crop exists to stop a model answering 2/C
    with row F's member size, which is exactly what the 68% run did at 2/C,
    4.6/C and 7/C.

    On the sheet this was written against the answer is that it CANNOT be
    avoided. Labels sit up to 83.7pt from their intersection, so a clean
    separation needs every grid gap above 167.4pt, and the tightest here are
    129.9pt (columns 4.6 to 4) and 137.2pt (rows C to F). Any crop large enough
    to contain its own furthest label reaches into its neighbour. Sizing is not
    the lever and `VLM_CROP_BAYS` cannot fix it.

    So this REPORTS and never fails. Failing would block a run that is as good
    as this sheet allows, and the number it prints is the one that says how much
    weight the prompt's "a neighbour's label is not yours to report" rule is
    carrying — on this sheet, all of it.
    """
    points, rects = {}, {}
    for boxes in boxes_by_page:
        for label, rect in boxes:
            rects[label] = rect
            points[label] = ((rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2)
    if not rects:
        return
    exposed = []
    for label, cases in sorted(by_label.items()):
        if label not in points:
            continue
        reach = max((c["derivation"].get("labelDistancePt", 0) for c in cases), default=0)
        nearest = None
        for other, rect in rects.items():
            if other == label:
                continue
            need = _reach_to(points[label], rect)
            if reach >= need and (nearest is None or need < nearest[1]):
                nearest = (other, need)
        if nearest:
            exposed.append((label, reach, nearest))
    if not exposed:
        print("\n  No crop can contain another intersection's label.", file=sys.stderr)
        return
    tightest = min(exposed, key=lambda e: e[2][1])
    print(
        f"\n  {len(exposed)} of {len(by_label)} intersections have a label that can reach a "
        f"NEIGHBOURING crop. Tightest: {tightest[0]}'s label is {tightest[1]}pt out and "
        f"{tightest[2][0]}'s crop starts {tightest[2][1]:.1f}pt away.\n"
        "  This is not a sizing bug and VLM_CROP_BAYS cannot fix it: a crop must be big "
        "enough to hold its OWN furthest label, and where the grid gap is under twice that "
        "distance the crop necessarily reaches into its neighbour. Nothing here is wrong — "
        "it says the crop alone does not separate these intersections, so the prompt's rule "
        "that a neighbour's label is not yours to report is what has to, and a confusion "
        "between exactly these pairs is the first thing to look for in the run.",
        file=sys.stderr,
    )


def dump_crops(pdf: str, against: str | None) -> int:
    """Print the crop the vision pass would take at each intersection.

    No model call and no rendering — this is the geometry, on its own, so it
    can be read by a person and checked against a set that was derived
    independently. `--against` does that check: every case in the set names an
    intersection and how far its label sits from it (`labelDistancePt`), so a
    crop that does not contain its own label is a crop that cannot be answered,
    and a case with no crop at all is a grid the two sides disagree about.

    The check is worth more than it looks. Both sides now read `grid.py`, so
    they can be wrong together — that is the blind spot the module's docstring
    states. What this cannot verify, it deliberately does not claim: run
    `--explain` and look at the sheet once.
    """
    # Deferred: generating a set must not need the worker's dependency chain
    # (config, llm, chunker). Only the crop dump reads the vision pass.
    import vlm

    doc = fitz.open(pdf)
    cases = []
    if against:
        with open(against) as fh:
            cases = json.load(fh)
    by_label = {}
    for case in cases:
        d = case.get("derivation") or {}
        if d.get("gridColumn") and d.get("gridRow"):
            by_label.setdefault(f"{d['gridColumn']}/{d['gridRow']}", []).append(case)

    problems = 0
    seen = set()
    boxes_by_page = []
    for page_index, page in enumerate(doc):
        boxes = vlm.crops(page)
        if not boxes:
            continue
        boxes_by_page.append(boxes)
        print(f"page {page_index + 1}: {len(boxes)} crops")
        for label, rect in boxes:
            seen.add(label)
            cx, cy = (rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2
            note = ""
            for case in by_label.get(label, []):
                d = case["derivation"]
                want = d["intersectionPt"]
                if abs(want[0] - cx) > 1 or abs(want[1] - cy) > 1:
                    note = f"  <- set says ({want[0]}, {want[1]})"
                    problems += 1
                    break
                reach = d["labelDistancePt"]
                if reach > min(rect.width, rect.height) / 2:
                    note = f"  <- label is {reach}pt away, outside this crop"
                    problems += 1
                    break
            print(
                f"  {label:>8}  ({cx:7.1f}, {cy:7.1f})  "
                f"{rect.width:5.1f} x {rect.height:5.1f} pt{note}"
            )

    for label in sorted(set(by_label) - seen):
        print(f"  {label:>8}  NO CROP — the set asks about it and the grid does not have it")
        problems += 1
    _report_crop_overlap(boxes_by_page, by_label)
    doc.close()
    if against:
        print(
            f"{len(seen)} crops, {len(by_label)} intersections in the set, {problems} problems",
            file=sys.stderr,
        )
    return 1 if problems else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pdf", default=None)
    ap.add_argument(
        "--project",
        default="",
        help="projectId STAMPED onto every case so the scorer knows which corpus to ask. "
        "It changes no answer: the truth is derived from the PDF and this script never "
        "opens a database, so generating twice with two ids gives byte-identical cases "
        "apart from that field. To compare two ingests, generate ONCE and pass "
        "--project to drawing_eval.mjs per run.",
    )
    ap.add_argument("--sheet", default=None, help="sheet number, e.g. S-100.0")
    ap.add_argument("--out", default=None)
    ap.add_argument("--explain", action="store_true", help="list refused cases")
    ap.add_argument(
        "--crops",
        action="store_true",
        help="print the crop the vision pass would take at each intersection and exit; "
        "no model call, no rendering",
    )
    ap.add_argument(
        "--against",
        default=None,
        metavar="SET.JSON",
        help="with --crops, check each crop against a set's intersectionPt and "
        "labelDistancePt. Exits non-zero on a disagreement.",
    )
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

    if args.crops:
        return dump_crops(args.pdf, args.against)

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
