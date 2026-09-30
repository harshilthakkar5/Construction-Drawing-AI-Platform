"""Columns two drawings disagree about — found by laying one over the other.

`plan_match` lines a detail of one sheet up with another sheet (an enlarged
plan over its overall plan, or one level's plan over the level below at the
same scale) using the columns both draw. This turns what still disagrees after
that into RFI candidates: a column one sheet shows and the other does not, a
column shown a few feet from where the other sheet puts it, or one shown at a
different size.

Built for the client's RFI 015 ("Discrepancies in dimensions and column
location", A3.27 against A3.35), and that RFI is also the caution. Laid over
each other, A3.35 and A3.27 AGREE — nine columns in two details, every one
where the other sheet has it. What the RFI author had marked were the columns
of the level BELOW (green boxes drawn over A3.27 as annotations), which is
information neither sheet carries. So the same machinery at a 1:1 ratio,
level over level, is what asks RFI 015's real question; this module does not
care which of the two it is given.

Precision first, like every check in rfi_checks:

  * Only a detail that aligned (plan_match.MIN_INLIERS columns agreeing on one
    offset, beating the runner-up) is compared at all.
  * Only elements that look like the columns that aligned — same fill, column
    size at the detail's own scale — take part, so a grey pad or a curb is not
    a missing column.
  * A size difference at the very EDGE of an enlarged detail is not reported:
    the detail's window may simply cut the column off.
  * A detail with more differences than MAX_DIFF_SHARE of its columns has
    probably aligned to the wrong area, and says so instead of reporting them.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import fitz

import grid
import plan_match as pm
from rfi_checks import MAX_EVIDENCE, Finding, Page, fingerprint, page_label

CHECK_TYPE = "column_mismatch"
# Differences listed in one question; the rest are counted.
MAX_LISTED = 8
# More differences than this share of a detail's columns: suspect the
# alignment, not the drawings.
MAX_DIFF_SHARE = 1.0
# A grid line is named in a location only within this many feet.
GRID_NAME_FT = 15.0


@dataclass
class Sheet:
    """One page as the check sees it: its database row and its geometry."""

    page: Page
    geometry: pm.SheetGeometry
    grid_x: dict[str, float]
    grid_y: dict[str, float]
    # {region: "detail 1"}, from plan_match.detail_names
    details: dict[int, str] = field(default_factory=dict)

    @classmethod
    def read(cls, page_row: Page, fitz_page: fitz.Page) -> "Sheet":
        geometry = pm.SheetGeometry.read(fitz_page)
        grid_x, grid_y = ({}, {})
        if geometry.elements:
            try:
                grid_x, grid_y = grid.page_grid(geometry.page)
            except Exception:  # a sheet with no readable grid is still comparable
                pass
        details = pm.detail_names(geometry.page, geometry) if geometry.elements else {}
        return cls(page_row, geometry, grid_x, grid_y, details)

    @property
    def label(self) -> str:
        return page_label(self.page)


def _near_grid(sheet: Sheet, x: float, y: float) -> str:
    """ "near grid 3.3 / B1.6", from the sheet's own grid, or "" when it has
    none close enough to be worth naming."""
    ptft = min(sheet.geometry.scales) if sheet.geometry.scales else 0
    if not ptft:
        return ""
    limit = GRID_NAME_FT * ptft
    names = []
    for axis, pos in ((sheet.grid_x, x), (sheet.grid_y, y)):
        best = min(axis.items(), key=lambda kv: abs(kv[1] - pos), default=None)
        if best is not None and abs(best[1] - pos) <= limit:
            names.append(best[0])
    return f"near grid {' / '.join(names)}" if names else ""


def _evidence(sheet: Sheet, rect: fitz.Rect, quote: str) -> dict:
    return {
        "documentId": sheet.page.document_id,
        "pageNumber": sheet.page.page_number,
        "combinedPageNumber": sheet.page.combined_page_number,
        "sheetNumber": sheet.page.sheet_number,
        "bbox": pm.to_pdf_box(sheet.geometry.page, rect),
        "chunkId": None,
        "quote": quote,
        "role": "finding",
    }


def _pad(r: fitz.Rect, by: float) -> fitz.Rect:
    return fitz.Rect(r.x0 - by, r.y0 - by, r.x1 + by, r.y1 + by)


def describe(
    diff: pm.Difference, al: pm.Alignment, a: Sheet, b: Sheet
) -> tuple[str, list[tuple[Sheet, fitz.Rect, str]]]:
    """One line of the question, and where to point on each sheet."""
    ptft_b = min(b.geometry.scales)
    ptft_a = ptft_b / al.scale
    if diff.b is not None:
        bx, by = diff.b.cx, diff.b.cy
    else:
        bx, by = al.to_b(diff.a.cx, diff.a.cy)
    where = _near_grid(b, bx, by) or _near_grid(a, *al.to_a(bx, by))
    where = f" ({where})" if where else ""
    spots: list[tuple[Sheet, fitz.Rect, str]] = []
    if diff.kind == "only_a":
        size = pm.size_text(diff.a, ptft_a)
        line = f"{a.label} shows a {size} column{where} that {b.label} does not show"
        spots.append((a, _pad(diff.a.rect(), 2), f"{a.label}: {size} column"))
        spots.append((b, _pad(al.rect_to_b(diff.a.rect()), 2), f"{b.label}: no column drawn here"))
    elif diff.kind == "only_b":
        size = pm.size_text(diff.b, ptft_b)
        line = f"{b.label} shows a {size} column{where} that {a.label} does not show"
        spots.append((b, _pad(diff.b.rect(), 2), f"{b.label}: {size} column"))
        ax0, ay0 = al.to_a(diff.b.x0, diff.b.y0)
        ax1, ay1 = al.to_a(diff.b.x1, diff.b.y1)
        spots.append((a, _pad(fitz.Rect(ax0, ay0, ax1, ay1), 2), f"{a.label}: no column drawn here"))
    elif diff.kind == "moved":
        size = pm.size_text(diff.a, ptft_a)
        inches = round(diff.offset_ft * 12)
        line = (
            f"a {size} column{where} is {inches // 12}'-{inches % 12}\" from where "
            f"{b.label} shows it on {a.label}"
        )
        spots.append((a, _pad(diff.a.rect(), 2), f"{a.label}: {size} column"))
        spots.append((b, _pad(diff.b.rect(), 2), f"{b.label}: the same column, {inches // 12}'-{inches % 12}\" away"))
    else:  # size
        size_a, size_b = pm.size_text(diff.a, ptft_a), pm.size_text(diff.b, ptft_b)
        line = f"the column{where} is {size_a} on {a.label} and {size_b} on {b.label}"
        spots.append((a, _pad(diff.a.rect(), 2), f"{a.label}: {size_a} column"))
        spots.append((b, _pad(diff.b.rect(), 2), f"{b.label}: {size_b} column"))
    return line, spots


def _position_key(diff: pm.Difference, al: pm.Alignment, b: Sheet) -> str:
    """Where a difference is, in whole feet on the other sheet — stable across
    re-reads, so a re-review finds the same candidate."""
    ptft_b = min(b.geometry.scales)
    x, y = (diff.b.cx, diff.b.cy) if diff.b is not None else al.to_b(diff.a.cx, diff.a.cy)
    return f"{diff.kind}@{round(x / ptft_b)},{round(y / ptft_b)}"


def column_mismatches(a: Sheet, b: Sheet, alignments: list[pm.Alignment]) -> tuple[list[Finding], list[str]]:
    """Findings for sheet A (the larger-scale one) laid over sheet B, one per
    aligned detail that disagrees with B. Also notes for what was compared and
    what was set aside, so "found nothing" can be told from "could not look"."""
    findings: list[Finding] = []
    notes: list[str] = []
    ratio_text = "at the same scale" if not alignments or abs(alignments[0].scale - 1) < 1e-6 else "enlarged"
    for al in alignments:
        diffs = pm.differences(al, a.geometry, b.geometry)
        matched = len(al.inliers)
        detail = a.details.get(al.detail)
        where = f"{a.label} {detail}" if detail else a.label
        notes.append(
            f"Column comparison: {where} laid over {b.label} ({ratio_text}, scale ratio "
            f"{al.scale:g}) — {matched} columns line up, {len(diffs)} differ."
        )
        if not diffs:
            continue
        if len(diffs) > MAX_DIFF_SHARE * matched:
            notes.append(
                f"Column comparison: {where} over {b.label} was set aside — {len(diffs)} differences "
                f"against {matched} matching columns means the two probably do not show the same area."
            )
            continue
        lines, evidence = [], []
        for diff in diffs:
            line, spots = describe(diff, al, a, b)
            lines.append(line)
            for sheet, rect, quote in spots:
                if len(evidence) < MAX_EVIDENCE:
                    evidence.append(_evidence(sheet, rect, quote))
        shown = "; ".join(lines[:MAX_LISTED])
        if len(lines) > MAX_LISTED:
            shown += f"; and {len(lines) - MAX_LISTED} more"
        same_scale = abs(al.scale - 1) < 1e-6
        subject = f"Column locations differ between {where} and {b.label}"
        question = (
            f"{a.label} and {b.label} show the same area"
            + (" at the same scale" if same_scale else f" ({a.label} enlarged)")
            + f", and {matched} columns line up, but {len(diffs)} do not: {shown}. "
            "Please confirm the correct column locations and sizes, and which drawing will be revised."
        )
        confidence = "high" if matched >= 4 and len(diffs) <= matched // 2 else "medium"
        if same_scale:
            # Two plans at one scale are often two LEVELS, and a column that
            # does not stack may be a transfer the structure intends.
            confidence = "medium" if confidence == "high" else "low"
        fp = fingerprint(
            CHECK_TYPE, *sorted([a.label, b.label]), *sorted(_position_key(d, al, b) for d in diffs)
        )
        findings.append(
            Finding(
                check_type=CHECK_TYPE,
                fingerprint=fp,
                confidence=confidence,
                subject=subject,
                question=question,
                evidence=evidence,
                facts={
                    "sheets": [a.label, b.label],
                    "scaleRatio": al.scale,
                    "matchedColumns": matched,
                    "differences": lines[:20],
                },
            )
        )
    return findings, notes


def pair_windows(
    al: pm.Alignment, a: Sheet, b: Sheet, diffs: list[pm.Difference], count: int, side_ft: float = 16.0
) -> list[tuple[fitz.Rect, fitz.Rect]]:
    """The same area cut out of both sheets, `count` times: first around every
    difference, then spread over the columns that lined up, so a model shown
    the pair compares like with like at a readable size. Display rects."""
    if count <= 0:
        return []
    ptft_a = min(b.geometry.scales) / al.scale
    half = side_ft * ptft_a / 2
    centres: list[tuple[float, float]] = []
    for d in diffs:
        centres.append((d.a.cx, d.a.cy) if d.a is not None else al.to_a(d.b.cx, d.b.cy))
    rest = [(x.cx, x.cy) for x, _ in al.inliers] + [(e.cx, e.cy) for e in al.members]
    while rest and len(centres) < count * 4:
        if not centres:
            centres.append(rest.pop(0))
            continue
        # Farthest from everything chosen so far: spread, don't cluster.
        far = max(rest, key=lambda p: min(math.hypot(p[0] - c[0], p[1] - c[1]) for c in centres))
        rest.remove(far)
        centres.append(far)
    out: list[tuple[fitz.Rect, fitz.Rect]] = []
    page_a, page_b = a.geometry.page.rect, b.geometry.page.rect
    for cx, cy in centres:
        if any(abs(cx - r.x0 - half) < half and abs(cy - r.y0 - half) < half for r, _ in out):
            continue  # already inside a window
        ra = fitz.Rect(cx - half, cy - half, cx + half, cy + half) & page_a
        rb = al.rect_to_b(ra) & page_b
        if ra.is_empty or rb.is_empty:
            continue
        out.append((ra, rb))
        if len(out) == count:
            break
    return out
