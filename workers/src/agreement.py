"""When the code and the AI find the same problem, and what that is worth.

The one-pass scan has two independent readers of a sheet pair: the code
(geometry_checks: grid spacing, columns, wall lines measured 3 in to 2 ft
apart) and the AI comparison (fullscan_run). They fail differently — the code
cannot read a drawing, the AI cannot measure one — so a problem BOTH report at
the same place is far likelier to be real than one either reports alone:

  * the AI confirms a code FINDING   -> that finding becomes HIGH, and no second
                                        candidate is written for the same problem;
  * the AI says a WALL or edge differs where the code MEASURED a wall line
    drawn in two places (which the code alone did not make a finding)
                                     -> the AI finding is HIGH, and says what the
                                        code measured;
  * the AI alone                     -> never above MEDIUM, and the review list
                                        says "check this on the sheet".

"The same place" is a box test, deliberately strict: a box covering a quarter
of the window or more agrees with everything and so with nothing, and a code
finding must be the same KIND of problem (a column claim never confirms a grid
finding). Pure: fullscan_run.settle does the I/O.
"""

from __future__ import annotations

import re

# The AI's catalogue questions each code check answers (RFI_REVIEW_CHECKS ids).
CODE_FAMILIES: dict[str, frozenset[str]] = {
    "grid_spacing": frozenset({"G01"}),
    "grid_mismatch": frozenset({"G01"}),
    "column_mismatch": frozenset({"C01", "C03"}),
}
# A box this large a share of its window is not a location.
MAX_BOX_SHARE = 0.25
# Two boxes are one place within this much drawing distance.
PAD_FT = 1.0
DEFAULT_PT_PER_FT = 9.0  # 1/8" = 1'-0" when no scale is known

AI_ONLY_NOTE = "Found by the AI comparison only; the code did not measure it. Check it on the sheet before accepting."


def cap_ai_only(confidence: str) -> str:
    """An AI-only finding is never high: nothing measured it."""
    return "medium" if confidence == "high" else confidence


def _rect(box: dict | list | None) -> list[float] | None:
    if box is None:
        return None
    if isinstance(box, dict):
        try:
            x, y, w, h = float(box["x"]), float(box["y"]), float(box["width"]), float(box["height"])
        except (KeyError, TypeError, ValueError):
            return None
        return [x, y, x + w, y + h]
    if len(box) == 4:
        return [float(v) for v in box]
    return None


def _touch(a: list[float], b: list[float], pad: float) -> bool:
    return a[0] - pad <= b[2] and b[0] - pad <= a[2] and a[1] - pad <= b[3] and b[1] - pad <= a[3]


def _too_big(box: list[float], window: list[float]) -> bool:
    area = max(0.0, window[2] - window[0]) * max(0.0, window[3] - window[1])
    own = max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])
    return area <= 0 or own >= MAX_BOX_SHARE * area


# What the code measures is a WALL LINE drawn in two places, so only a claim
# about a wall, partition or edge can be the same problem. The first real run
# matched "a column here, nothing there" to a wall measured 11 in off nearby:
# near each other, and two different claims.
WALL_CLAIM_CHECKS = frozenset({"C02", "FL01"})
_WALL_WORDS = re.compile(r"\b(?:walls?|partitions?|edges?|faces?|curbs?|parapets?)\b", re.I)


def is_wall_claim(issue: dict) -> bool:
    if issue.get("checkId") in WALL_CLAIM_CHECKS:
        return True
    return bool(_WALL_WORDS.search(" ".join(str(issue.get(k) or "") for k in ("element", "whatA", "whatB"))))


def measured_agreement(issue: dict, box_b: list[float], window_b: list[float], measured_at: list[dict] | None) -> str | None:
    """What the code measured where the AI put its problem on sheet B, or
    None. Both are display-space rects on sheet B; the claim must be about a
    wall or an edge, which is the only thing the code measured."""
    if not measured_at or _too_big(box_b, window_b) or not is_wall_claim(issue):
        return None
    for m in measured_at:
        rect = _rect(m.get("rect"))
        if rect is None:
            continue
        pad = PAD_FT * float(m.get("ptPerFt") or DEFAULT_PT_PER_FT)
        if _touch(box_b, rect, pad):
            return f"The code measured the same place: {m.get('note')}."
    return None


def code_finding_agreement(check_id: str, ai_evidence: list[dict], candidates: list[dict],
                           windows: dict, pt_per_ft: float | None) -> dict | None:
    """The code finding the AI's problem confirms, or None.

    `ai_evidence` is the AI finding's two evidence entries (documentId,
    pageNumber, bbox — unrotated, like every stored box) and `windows` its
    tile's windows, used only for the box-size rule. A candidate confirms when
    it is a code check of the same kind, its own evidence is on BOTH pages of
    the pair, and the AI's box touches its evidence on at least one of them.
    """
    pages = {(e.get("documentId"), e.get("pageNumber")) for e in ai_evidence}
    if len(pages) < 2:
        return None
    sides = []
    for e, side in zip(ai_evidence, ("a", "b")):
        box = _rect(e.get("bbox"))
        # The box is unrotated and the window display space; only their AREAS
        # are compared, which a page rotation does not change.
        window = (windows.get(side) or {}).get("rect")
        if box is None or (window and _too_big(box, list(window))):
            continue
        sides.append(((e.get("documentId"), e.get("pageNumber")), box))
    if not sides:
        return None
    pad = PAD_FT * (pt_per_ft or DEFAULT_PT_PER_FT)
    for c in candidates:
        if check_id not in CODE_FAMILIES.get(c.get("checkType") or "", ()):
            continue
        evidence = c.get("evidence") or []
        on = {(e.get("documentId"), e.get("pageNumber")) for e in evidence}
        if not pages <= on:
            continue
        for key, box in sides:
            if any((e.get("documentId"), e.get("pageNumber")) == key and (r := _rect(e.get("bbox"))) is not None
                   and _touch(box, r, pad) for e in evidence):
                return c
    return None
