"""Title-block region scraping — apply ONE user-drawn box to every page.

The user draws a rectangle over the title block on a single page in the
browser; it is stored as ratios of the rendered page (0-1), so the same box
re-applies to every page of every PDF in the project regardless of each page's
point size or rotation.

Coordinate systems (this is the part that is easy to get wrong):
  * `page.rect` IS rotation-aware. For a page with /Rotate 90 it returns the
    landscape rectangle the user actually sees in the browser — the same thing
    the rendered PNG shows. So building the clip box from `page.rect` is
    correct.
  * `page.get_text()` / `page.get_textpage()` are NOT. PyMuPDF sets the page
    rotation to 0 before building the text page, so a `clip` argument is
    interpreted in the *unrotated* MediaBox space. For a rotated CAD sheet the
    two spaces are transposed (e.g. 3456x2592 vs 2592x3456), so a box near the
    right edge of the visible page lands completely off-page once handed to
    get_text() — and every page comes back empty. The fix is
    `clip * page.derotation_matrix` before extraction.
  * `page.get_pixmap()` IS rotation-aware, so the OCR path keeps the
    display-space rectangle.

Extraction ladder per page:
  1. Native vector text inside the (de-rotated) clip rectangle.
  2. Word-level overlap pass, which also catches words that only partially
     intersect the box (a slightly-too-tight selection).
  3. If the page is a scan/flattened drawing, render just that region at high
     DPI and OCR the crop (PaddleOCR via workers/src/ocr.py — optional, the
     wrapper returns "" when it isn't installed).

Ported from the standalone scraper in docs/reference/pdf-region-scraper/;
keep the coordinate handling identical.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import fitz  # PyMuPDF

import logutil
import ocr

log = logutil.get("region")

# A word must have at least this fraction of its own area inside the selection
# box to count as "inside" during the overlap pass.
MIN_WORD_OVERLAP = 0.30

# Render DPI for the OCR fallback crop. The crop is a title block, not a page,
# so a high DPI is cheap.
OCR_DPI = int(os.environ.get("REGION_OCR_DPI", "300"))


@dataclass(frozen=True)
class Region:
    """The user's box, as ratios of the rendered page (0-1)."""

    rel_x: float
    rel_y: float
    rel_w: float
    rel_h: float

    @classmethod
    def from_row(cls, row: dict) -> "Region":
        return cls(
            rel_x=float(row["relX"]),
            rel_y=float(row["relY"]),
            rel_w=float(row["relW"]),
            rel_h=float(row["relH"]),
        )


def build_display_clip(page: fitz.Page, region: Region) -> fitz.Rect:
    """Clip rectangle in the page's *displayed* (rotation-applied) coordinate
    space — the same space the user drew the box in."""
    rect = page.rect
    x0 = rect.x0 + rect.width * region.rel_x
    y0 = rect.y0 + rect.height * region.rel_y
    x1 = x0 + rect.width * region.rel_w
    y1 = y0 + rect.height * region.rel_h
    clip = fitz.Rect(x0, y0, x1, y1)
    clip.normalize()
    return clip & rect  # never let it spill off the page


def words_in_clip(page: fitz.Page, clip_unrotated: fitz.Rect) -> str:
    """Overlap-tolerant pass: keep words mostly inside the box, reading order."""
    hits = []
    for x0, y0, x1, y1, word, block_no, line_no, word_no in page.get_text("words"):
        wr = fitz.Rect(x0, y0, x1, y1)
        area = abs(wr.get_area())
        if area <= 0:
            continue
        inter = wr & clip_unrotated
        if inter.is_empty:
            continue
        if abs(inter.get_area()) / area >= MIN_WORD_OVERLAP:
            hits.append((block_no, line_no, word_no, word))

    hits.sort()
    return " ".join(h[3] for h in hits).strip()


def extract_region_text(
    page: fitz.Page, region: Region, ocr_dpi: int = OCR_DPI
) -> tuple[str, str]:
    """Scrape the region off one page.

    Returns (text, method) where method is 'vector', 'words', 'ocr' or 'none'.
    Never raises: an unreadable page yields ("", "none") so one bad sheet can
    never fail a whole project's scrape.
    """
    clip_display = build_display_clip(page, region)
    # get_text() works in UNROTATED page space -> map the box back.
    clip_text = (clip_display * page.derotation_matrix).normalize()

    extracted = page.get_text("text", clip=clip_text).strip()
    if extracted:
        return " ".join(extracted.split()), "vector"

    extracted = words_in_clip(page, clip_text)
    if extracted:
        return " ".join(extracted.split()), "words"

    try:
        # get_pixmap IS rotation-aware, so it takes the display-space rectangle.
        pix = page.get_pixmap(clip=clip_display, dpi=ocr_dpi)
        ocr_text = ocr.ocr_png_bytes(pix.tobytes("png")).strip()
        if ocr_text:
            return " ".join(ocr_text.split()), "ocr"
    except Exception as exc:
        log.warning("region OCR failed on page %s: %s", page.number + 1, exc)
    return "", "none"
