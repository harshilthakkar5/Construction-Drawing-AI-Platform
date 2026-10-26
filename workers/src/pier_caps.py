"""Two columns on one pier cap, and no detail called out for it — read off a
foundation plan's drawing, no model.

Leland RFI 004: the typical pier caps on S0.03 are drawn for ONE column
("corner / perimeter / interior at standard column"). Along the expansion
joint on S2.30-A and S2.30-B, two columns stand side by side on one pier, and
the plan calls out no detail there; the detail that sizes such a cap (9/S4.00,
"PIER CAP AT EXPANSION JOINT") is referenced nowhere on those plans. The team
asked for the cap's dimensions.

Two column fills TOUCHING, of a column's size at the printed scale and of
similar area, are two columns on one support. Only on a FOUNDATION plan: the
same pair on a framing plan is two columns and nothing more. A pair with any
section or detail symbol within NEAR_CALLOUT_FT is left alone — the plan says
where its cap is drawn. The finding claims only what the plan shows: the cap
carries two columns and the plan does not say which detail it follows.
"""

from __future__ import annotations

import re

import fitz

# A column fill's long side at the printed scale; a short side under the
# lower bound is a wall line or a hatch stroke.
MIN_COLUMN_FT = 0.8
MAX_COLUMN_FT = 4.0
MIN_SHORT_FT = 0.6
# Two fills this close are touching (drafted as one pair, a line apart).
TOUCH_PT = 1.5
OVERLAP_PT = 0.5
SIMILAR_AREA = 0.3
NEAR_CALLOUT_FT = 8.0
CAP_PAD_FT = 1.5
MAX_EVIDENCE = 5
FOUNDATION = re.compile(r"\bFOUNDATION\s+PLANS?\b", re.I)


def column_fills(drawings: list[dict], pt_per_ft: float) -> list[fitz.Rect]:
    """Grey filled shapes of a column's size. Pure over get_drawings()."""
    out = []
    for p in drawings:
        fill = p.get("fill")
        if p.get("type") != "f" or not fill or not 0.4 < sum(fill) / 3 < 0.95:
            continue
        r = fitz.Rect(p["rect"])
        long_side, short_side = max(r.width, r.height), min(r.width, r.height)
        if MIN_COLUMN_FT * pt_per_ft <= long_side <= MAX_COLUMN_FT * pt_per_ft and short_side >= MIN_SHORT_FT * pt_per_ft:
            out.append(r)
    return out


def twins(fills: list[fitz.Rect]) -> list[fitz.Rect]:
    """The union of each pair of touching, similar column fills. Pure."""
    out = []
    for i, a in enumerate(fills):
        for b in fills[i + 1:]:
            gap = max(a.x0 - b.x1, b.x0 - a.x1, a.y0 - b.y1, b.y0 - a.y1)
            big = max(a.get_area(), b.get_area())
            if -OVERLAP_PT <= gap <= TOUCH_PT and big and abs(a.get_area() - b.get_area()) <= SIMILAR_AREA * big:
                out.append(a | b)
    # One pair drawn twice (a fill repeated under its outline) is one pair.
    kept: list[fitz.Rect] = []
    for r in out:
        if not any(abs(r.x0 - k.x0) < 2 and abs(r.y0 - k.y0) < 2 for k in kept):
            kept.append(r)
    return kept


def uncalled(pairs: list[fitz.Rect], symbols: list[tuple[float, float]], pt_per_ft: float) -> list[fitz.Rect]:
    """The pairs with no detail symbol within NEAR_CALLOUT_FT. `symbols` are
    centres in the same space as the pairs. Pure."""
    reach = NEAR_CALLOUT_FT * pt_per_ft
    def near(r):
        return any(max(r.x0 - x, 0, x - r.x1) ** 2 + max(r.y0 - y, 0, y - r.y1) ** 2 <= reach ** 2 for x, y in symbols)
    return [r for r in pairs if not near(r)]


def is_foundation(region_text: str | None, page) -> bool:
    """A foundation plan by its title block, or by the sheet's big titles (a
    title-block box drawn round the sheet number alone names nothing)."""
    if FOUNDATION.search(region_text or ""):
        return True
    import sheet_facts

    titles = sheet_facts.page_titles(page)
    # A title broken over two lines ("Enlarged Foundation" / "Plans") reads joined.
    return any(FOUNDATION.search(text) for text in titles + [" ".join(titles)])


def on_page(page) -> tuple[list[fitz.Rect], str | None]:
    """(pair boxes in stored space, note) for one foundation plan."""
    import callouts
    import plan_match

    scales = plan_match.page_scales(page)
    if not scales:
        return [], "no drawing scale printed"
    ppf = min(scales)
    pairs = twins(column_fills(page.get_drawings(), ppf))
    if not pairs:
        return [], None
    symbols = []
    for c in callouts.any_callouts(callouts.display_words(page)):
        r = fitz.Rect(c["rect"])  # stored space, like the drawings
        symbols.append(((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2))
    return uncalled(pairs, symbols, ppf), None


def findings(pages, chunks, open_page) -> tuple[list, list[str]]:
    """twin_column_cap findings: one per foundation plan. (findings, notes)."""
    from rfi_checks import Finding, fingerprint, page_label
    import plan_match

    out, notes = [], []
    for row in pages:
        if row.sheet_kind not in (None, "plan"):
            continue
        page = open_page(row.document_id, row.page_number)
        if page is None or not is_foundation(row.region_text, page):
            continue
        found, note = on_page(page)
        label = page_label(row)
        if note:
            notes.append(f"Pier caps on {label}: {note}")
        if not found:
            continue
        ppf = min(plan_match.page_scales(page))
        pad = CAP_PAD_FT * ppf
        many = len(found) > 1
        evidence = [{
            "documentId": row.document_id,
            "pageNumber": row.page_number,
            "combinedPageNumber": row.combined_page_number,
            "sheetNumber": row.sheet_number,
            "bbox": {"x": round(r.x0 - pad, 2), "y": round(r.y0 - pad, 2),
                     "width": round(r.width + 2 * pad, 2), "height": round(r.height + 2 * pad, 2)},
            "chunkId": None,
            "quote": "two columns on one pier cap",
            "role": "finding",
        } for r in found[:MAX_EVIDENCE]]
        out.append(Finding(
            check_type="twin_column_cap",
            fingerprint=fingerprint("twin_column_cap", row.document_id, str(row.page_number),
                                    *sorted(f"{round(r.x0 / 10)}:{round(r.y0 / 10)}" for r in found)),
            confidence="medium",
            subject=f"Pier cap{'s' if many else ''} carrying two columns on {label}: no detail called out"[:120],
            question=(f"On {label}, {len(found)} pier cap{'s each carry' if many else ' carries'} two columns side by "
                      f"side, and the plan calls out no detail at {'them' if many else 'it'}, so it is not clear whether "
                      f"the typical pier cap detail applies. Which detail gives the size, depth and reinforcement of "
                      f"{'these caps' if many else 'this cap'}?"),
            evidence=evidence,
            facts={"sheet": row.sheet_number, "count": len(found)},
        ))
    return out, notes
