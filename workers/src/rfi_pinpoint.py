"""Shrink a project-check finding's evidence box to the words it is about.

A text check knows the exact words it found — "F6.0", "RTU-3" beside
"100 MBH", "GROUT COLOR: TBD" — but its evidence box is the CHUNK the words
sit in, and on a CAD sheet a chunk can span most of the sheet. JETRIGHT's
project checks produced ten packages and nine of them said "the cited
evidence covers a broad area, so the exact location is NOT pinpointed": the
RTU-3 outline was 2686 x 543pt for a label a few points wide.

So after the checks run, the scan opens each cited page and searches for the
evidence's `_term` inside its chunk box. A `_near` word (the value of a tag)
picks the right one of several hits and joins the box. Nothing found leaves
the chunk box exactly as it was: a broad box that is honest beats a tight one
in the wrong place. Boxes stay in the page's UNROTATED space, the space
`get_text`, `search_for` and PDF annotations all use.
"""

from __future__ import annotations

import fitz

PAD_PT = 3.0
NEAR_PT = 80.0  # how far a tag's value may sit from the tag
QUOTE_MAX_CHARS = 80  # a quote short enough to be one line is searched whole


def _rect(bbox: dict | None) -> fitz.Rect | None:
    if not bbox:
        return None
    return fitz.Rect(bbox["x"], bbox["y"], bbox["x"] + bbox["width"], bbox["y"] + bbox["height"])


def _box(rect: fitz.Rect) -> dict:
    r = fitz.Rect(rect.x0 - PAD_PT, rect.y0 - PAD_PT, rect.x1 + PAD_PT, rect.y1 + PAD_PT)
    return {"x": round(r.x0, 2), "y": round(r.y0, 2), "width": round(r.width, 2), "height": round(r.height, 2)}


def _gap(a: fitz.Rect, b: fitz.Rect) -> float:
    dx = max(0.0, max(a.x0, b.x0) - min(a.x1, b.x1))
    dy = max(0.0, max(a.y0, b.y0) - min(a.y1, b.y1))
    return (dx * dx + dy * dy) ** 0.5


def locate(page, item: dict) -> dict | None:
    """The tight box for one evidence item, or None when its words cannot be
    found inside its own box (the caller keeps the original)."""
    term = (item.get("_term") or "").strip()
    clip = _rect(item.get("bbox"))
    if not term or clip is None:
        return None
    # A little slack: chunk boxes are block unions, and a glyph's box can
    # poke out of them by a point or two.
    clip = fitz.Rect(clip.x0 - 2, clip.y0 - 2, clip.x1 + 2, clip.y1 + 2)
    hits: list[fitz.Rect] = []
    quote = " ".join((item.get("quote") or "").split())
    near = (item.get("_near") or "").strip()
    # With a partner word the TERM is what gets paired: a quote spanning the
    # tag's lines returns one rect per line, and pairing those picks a line.
    if not near and quote and len(quote) <= QUOTE_MAX_CHARS and term.upper() in quote.upper():
        hits = page.search_for(quote, clip=clip)
    if not hits:
        hits = page.search_for(term, clip=clip)
    if not hits:
        return None
    if near:
        wide = fitz.Rect(clip.x0 - NEAR_PT, clip.y0 - NEAR_PT, clip.x1 + NEAR_PT, clip.y1 + NEAR_PT)
        partners = page.search_for(near, clip=wide)
        best = min(
            ((_gap(h, p), h, p) for h in hits for p in partners),
            key=lambda t: t[0],
            default=None,
        )
        if best is not None and best[0] <= NEAR_PT:
            return _box(best[1] | best[2])
    # Several hits of the same words in one chunk ("TBD" twice): the first,
    # unless they sit together, when their union is still a tight box.
    union = fitz.Rect(hits[0])
    for h in hits[1:]:
        union |= h
    if union.width * union.height <= 4 * max(h.width * h.height for h in hits) * len(hits):
        return _box(union)
    return _box(hits[0])


def pinpoint(evidence: list[dict], open_page) -> int:
    """Tighten every item that names its words, in place; strip the internal
    keys whatever happens. `open_page(document_id, page_number)` returns the
    fitz page or None. Returns how many boxes were tightened."""
    tightened = 0
    for item in evidence:
        try:
            if item.get("_term") and item.get("documentId"):
                page = open_page(item["documentId"], item["pageNumber"])
                if page is not None:
                    box = locate(page, item)
                    if box is not None:
                        item["bbox"] = box
                        tightened += 1
        finally:
            for key in [k for k in item if k.startswith("_")]:
                item.pop(key)
    return tightened
