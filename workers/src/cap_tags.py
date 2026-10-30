"""A pile cap drawn with no tag and no size — read off a foundation plan, no model.

UT Law RFI 008: S1.101 tags its pile caps (PC-A … PC-D, each with a TOF box),
but the caps under some piers — P-36 at C-13 among them — carry no tag and no
size, only a section symbol to 3/S3.100, "Section at Pile Cap". The team asked
for the cap dimensions and tag.

The caps are drawn as raster tiles on that sheet, so geometry cannot see them;
the plan's own symbols can. A section or detail symbol whose target detail is
TITLED as a pile or pier cap marks a cap. On a plan that tags its caps at all,
such a symbol with no cap tag within NEAR_TAG_FT is a cap drawn without one.
A plan that tags no cap says nothing about which caps should be tagged, so it
is left alone.
"""

from __future__ import annotations

import re

import fitz

# PC-A, PC1, CAP-2 — never a bare word ("CAPS").
CAP_TAG = re.compile(r"^(?:PC-[A-Z0-9]{1,3}|PC\d{1,3}[A-Z]?|CAP-[A-Z0-9]{1,3})$")
CAP_TITLE = re.compile(r"\b(?:PILE|PIER)\s+CAPS?\b", re.I)
PIER_MARK = re.compile(r"^P-?\d{1,3}[A-Z]?$")
COLUMN_MARK = re.compile(r"^C-?\d{1,3}[A-Z]?$")
# A tag sits beside its cap; the untagged symbols on S1.101 are 30-51 ft from any tag.
NEAR_TAG_FT = 15.0
NEAR_MARK_FT = 10.0
PAD_FT = 4.0
MAX_EVIDENCE = 5


def _centre(w) -> tuple[float, float]:
    return (w[0] + w[2]) / 2, (w[1] + w[3]) / 2


def _dist(a: tuple[float, float], b: tuple[float, float]) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def untagged(symbols: list[dict], tags: list[tuple[float, float]], pt_per_ft: float) -> list[dict]:
    """The cap symbols with no cap tag within NEAR_TAG_FT. Display space. Pure."""
    reach = NEAR_TAG_FT * pt_per_ft
    return [s for s in symbols if not any(_dist((s["x"], s["y"]), t) <= reach for t in tags)]


def nearest(words: list[tuple], at: tuple[float, float], pattern: re.Pattern, reach: float) -> tuple | None:
    """The nearest word matching `pattern` within `reach` points of `at`. Pure."""
    hits = sorted(((_dist(_centre(w), at), i) for i, w in enumerate(words) if pattern.fullmatch(w[4])))
    return words[hits[0][1]] if hits and hits[0][0] <= reach else None


def cap_details(page) -> set[str]:
    """The numbers of the details on a sheet titled as a pile or pier cap."""
    import review_aids

    return {n for n, title, _ in review_aids.detail_titles(review_aids._display_lines(page))
            if n and CAP_TITLE.search(title)}


def findings(pages, chunks, open_page, every=None) -> tuple[list, list[str]]:
    """untagged_pile_cap findings, one per foundation plan. `pages` are the plans
    to check; `every` the project's pages, where the symbols' target sheets are
    looked up (a review checks its own sheets but S3.100 need not be one).
    (findings, notes)."""
    import callouts
    import pier_caps
    import plan_match
    from rfi_checks import Finding, fingerprint, page_label

    sheets = {r.sheet_number.upper(): r for r in (every or pages) if r.sheet_number}
    titled: dict[str, set[str]] = {}

    def caps_on(sheet: str) -> set[str]:
        if sheet not in titled:
            row = sheets.get(sheet.upper())
            page = open_page(row.document_id, row.page_number) if row else None
            titled[sheet] = cap_details(page) if page is not None else set()
        return titled[sheet]

    out, notes = [], []
    for row in pages:
        if row.sheet_kind not in (None, "plan"):
            continue
        page = open_page(row.document_id, row.page_number)
        if page is None or not pier_caps.is_foundation(row.region_text, page):
            continue
        words = callouts.display_words(page)
        tags = [w for w in words if CAP_TAG.fullmatch(w[4])]
        if not tags:
            continue
        scales = plan_match.page_scales(page)
        if not scales:
            notes.append(f"Untagged pile caps on {page_label(row)}: no drawing scale printed")
            continue
        ppf = min(scales)
        symbols = [c for c in callouts.any_callouts(words) if c["detail"] in caps_on(c["sheet"])]
        lone = untagged(symbols, [_centre(t) for t in tags], ppf)
        if not lone:
            continue
        label = page_label(row)
        reach, pad = NEAR_MARK_FT * ppf, PAD_FT * ppf
        places = []
        for s in lone:
            pier = nearest(words, (s["x"], s["y"]), PIER_MARK, reach)
            # The column is the one beside the pier's own label, which sits at the cap; the
            # symbol sits off to one side and can be nearer the next column.
            column = nearest(words, _centre(pier) if pier else (s["x"], s["y"]), COLUMN_MARK, reach)
            names = [w[4] for w in (pier, column) if w]
            under = f"{names[0]} ({names[1]})" if len(names) == 2 else (names[0] if names else None)
            places.append(f"{'under ' + under + ', ' if under else ''}cut by section {s['detail']}/{s['sheet']}")
        tag_names = ", ".join(sorted({t[4] for t in tags}))
        many = len(lone) > 1
        evidence = []
        for s in lone[:MAX_EVIDENCE]:
            r = fitz.Rect(s["rect"])  # stored space
            evidence.append({
                "documentId": row.document_id, "pageNumber": row.page_number,
                "combinedPageNumber": row.combined_page_number, "sheetNumber": row.sheet_number,
                "bbox": {"x": round(r.x0 - pad, 2), "y": round(r.y0 - pad, 2),
                         "width": round(r.width + 2 * pad, 2), "height": round(r.height + 2 * pad, 2)},
                "chunkId": None, "quote": f"section {s['detail']}/{s['sheet']}", "role": "finding",
            })
        out.append(Finding(
            check_type="untagged_pile_cap",
            fingerprint=fingerprint("untagged_pile_cap", row.document_id, str(row.page_number),
                                    *sorted(f"{s['detail']}/{s['sheet']}@{round(s['x'] / 10)}:{round(s['y'] / 10)}"
                                            for s in lone)),
            confidence="medium",
            subject=f"Pile cap{'s' if many else ''} with no tag or size on {label}"[:120],
            question=(f"On {label}, the plan tags its pile caps ({tag_names}), but {len(lone)} cap"
                      f"{'s are' if many else ' is'} drawn with no tag and no size: {'; '.join(places)}. "
                      f"Which tag, size and reinforcement apply to {'these caps' if many else 'this cap'}?"),
            evidence=evidence,
            facts={"sheet": row.sheet_number, "count": len(lone), "tags": tag_names},
        ))
    return out, notes
