"""OCR for the text a PDF's text layer does not hold.

Two kinds of sheet had text that no check, chat answer or summary could see:

  * TEXT DRAWN AS SHAPES. CAD fonts (SHX) are often exported as vector strokes,
    so the sheet LOOKS lettered and `get_text()` returns nothing for those
    words. JETRIGHT's M0.02 has 164 text-layer words and 10,905 drawings: its
    rooftop-unit and fan schedules — "CF-2 … 2.0" under an HP heading — are
    strokes. Ten of that set's pages have no text layer at all.
  * SCHEDULES PASTED AS PICTURES. E0.05's seven panel schedules are 1-bit
    images placed at 41–58 DPI.

The old OCR path ran only on a page with NO text layer, sent the whole sheet as
one image, and PaddleOCR shrinks its input to 960px — a 36x24 sheet at about
27 DPI, where nothing reads. So this module:

  * decides which pages are worth reading (`plan`): no text layer, few words
    over a lot of linework, or a large picture;
  * renders the sheet in overlapping TILES at `OCR_DPI` and keeps each line
    from the one tile whose core holds its centre — a line up to the overlap
    long is whole in that tile;
  * maps every box to the page's UNROTATED space (the space chunks, evidence
    and annotations use), drops lines the text layer already has, and drops
    lines under `MIN_CONFIDENCE`: a misread mark becomes a false finding, so
    an unread one is the error allowed to fall;
  * measures each picture, and calls one ILLEGIBLE when it was placed too
    coarse to read and OCR found text in it it could not read. E0.05's
    panel schedules are exactly that: the honest output is "this schedule is
    not legible, issue a legible copy", never a value guessed off it.

OCR text is stored as ordinary `kind="text"` chunks with
`sourceModel = SOURCE_MODEL`, so retrieval and the checks read it like the
text layer — and a finding resting on it can say so.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

import fitz

import chunker
import config
import logutil
import ocr

log = logutil.get("page_ocr")

# Bump when what is read changes; pages read under an older version are read
# again by the next scan.
OCR_VERSION = 2  # 2: a picture is illegible only when 1-bit (see Picture.illegible)
SOURCE_MODEL = "ocr:paddleocr"

OCR_DPI = 200          # 8pt CAD text is ~22px tall: PaddleOCR's comfortable range
TILE_PX = 1600         # also the detector's side limit (ocr._get_engine)
OVERLAP = 0.25         # of a tile: a line up to ~144pt long is whole in one tile
MIN_CONFIDENCE = 0.85  # below this, a line is more often a misread than a reading
PICTURE_MIN_PT = 144   # a picture under 2in on a side is a logo or a stamp
ILLEGIBLE_DPI = 100    # a 1-bit picture placed coarser than this loses letter strokes
ILLEGIBLE_MIN_LINES = 10  # it must visibly hold text: a coarse PHOTO is not a schedule
ILLEGIBLE_READ_SHARE = 0.5  # ...most of which OCR could not read


@dataclass
class OcrLine:
    text: str
    confidence: float
    rect: tuple[float, float, float, float]  # unrotated page points


@dataclass
class Picture:
    rect: tuple[float, float, float, float]  # unrotated page points
    dpi: float
    lines: int = 0  # text lines OCR found inside it, at any confidence
    readable: int = 0  # ...of which at MIN_CONFIDENCE or better
    bits: int = 8  # bits per component: 1 is pure black and white

    @property
    def illegible(self) -> bool:
        """Coarse, 1-BIT, full of text and mostly unread. The 1-bit condition
        was paid for: at 72 DPI the first rule also flagged a UL listing, a
        manufacturer's product sheet and a gate-operator table on JETRIGHT —
        grey and colour screenshots whose anti-aliased letters a person reads
        easily. A 1-bit picture has no grey edge to fall back on, and at 67
        DPI E0.05's panel schedules visibly lose strokes."""
        return (
            self.bits <= 1
            and self.dpi < ILLEGIBLE_DPI
            and self.lines >= ILLEGIBLE_MIN_LINES
            and self.readable < ILLEGIBLE_READ_SHARE * self.lines
        )


@dataclass
class PageOcr:
    reason: str | None
    lines: list[OcrLine] = field(default_factory=list)
    pictures: list[Picture] = field(default_factory=list)
    ran: bool = False  # False: no engine — nothing was read, which is not "nothing there"

    def as_json(self) -> dict:
        return {
            "version": OCR_VERSION,
            "reason": self.reason,
            "ran": self.ran,
            "lines": [{"t": l.text, "c": round(l.confidence, 3), "b": [round(v, 1) for v in l.rect]} for l in self.lines],
            "pictures": [
                {**{k: v for k, v in asdict(p).items() if k != "rect"}, "b": [round(v, 1) for v in p.rect],
                 "dpi": round(p.dpi, 1), "illegible": p.illegible}
                for p in self.pictures
            ],
        }


def pictures(page) -> list[Picture]:
    """Large raster images on the page, with the resolution they were PLACED
    at. An image can be placed rotated (M0.02's certificate is 792x592 pixels
    in a 592x792pt box), so long side is matched to long side."""
    out = []
    for info in page.get_image_info():
        r = fitz.Rect(info["bbox"])
        if min(r.width, r.height) < PICTURE_MIN_PT or not info.get("width"):
            continue
        px = sorted((info["width"], info["height"]))
        pt = sorted((r.width, r.height))
        dpi = min(px[0] / pt[0], px[1] / pt[1]) * 72
        out.append(Picture(tuple(r), dpi, bits=int(info.get("bpc") or 8)))
    return out


def plan(page) -> str | None:
    """Why this page should be OCR'd, or None. Cheap: counts only."""
    words = len(page.get_text("words"))
    if words == 0:
        return "no text layer"
    if not config.OCR_SHAPE_TEXT:
        return None
    # Shapes first: a whole-page read covers the pictures too, and M0.02 has
    # both — read for its pictures alone, its stroke-drawn schedules were missed.
    if words <= config.OCR_SHAPE_TEXT_MAX_WORDS and len(page.get_cdrawings()) >= config.OCR_SHAPE_TEXT_MIN_DRAWINGS:
        return "text drawn as shapes"
    if pictures(page):
        return "pictures on the sheet"
    return None


def tiles(display: fitz.Rect, zoom: float) -> list[tuple[fitz.Rect, fitz.Rect]]:
    """(clip, core) pairs in DISPLAY space. Tiles step by `TILE_PX` less the
    overlap; a tile's core reaches halfway into each overlap it shares, so the
    cores tile the page exactly once."""
    size = TILE_PX / zoom
    step = size * (1 - OVERLAP)
    half = (size - step) / 2
    xs = _starts(display.x0, display.x1, size, step)
    ys = _starts(display.y0, display.y1, size, step)
    out = []
    for i, y in enumerate(ys):
        for j, x in enumerate(xs):
            clip = fitz.Rect(x, y, min(x + size, display.x1), min(y + size, display.y1))
            core = fitz.Rect(
                display.x0 if j == 0 else x + half,
                display.y0 if i == 0 else y + half,
                display.x1 if j == len(xs) - 1 else x + step + half,
                display.y1 if i == len(ys) - 1 else y + step + half,
            )
            out.append((clip, core))
    return out


def _starts(lo: float, hi: float, size: float, step: float) -> list[float]:
    if hi - lo <= size:
        return [lo]
    n = math.ceil((hi - lo - size) / step) + 1
    return [lo + k * step for k in range(n)]


def _overlaps(a: tuple, b: tuple) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def read(page, clips: list[fitz.Rect] | None = None) -> PageOcr:
    """OCR the page (or just `clips`, display rects) and return what the text
    layer does not already hold. With no OCR engine installed, `ran` is False
    and nothing is claimed about the page."""
    result = PageOcr(reason=None, pictures=pictures(page))
    if not ocr.available():
        return result
    result.ran = True
    zoom = OCR_DPI / 72
    derotate = page.derotation_matrix
    raw: list[OcrLine] = []
    regions = [page.rect] if clips is None else clips
    for region in regions:
        for clip, core in tiles(region, zoom):
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip, colorspace=fitz.csRGB, alpha=False)
            if pix.width < 8 or pix.height < 8 or _blank(pix):
                continue
            for quad, text, conf in ocr.image_lines(pix, pad_to=TILE_PX):
                xs = [clip.x0 + p[0] / zoom for p in quad]
                ys = [clip.y0 + p[1] / zoom for p in quad]
                shown = fitz.Rect(min(xs), min(ys), max(xs), max(ys))
                centre = fitz.Point((shown.x0 + shown.x1) / 2, (shown.y0 + shown.y1) / 2)
                if not core.contains(centre) or not text.strip():
                    continue
                r = shown * derotate
                r.normalize()
                raw.append(OcrLine(text.strip(), float(conf), tuple(r)))
    # Pictures: how much text is in each, and how much of it reads.
    for pic in result.pictures:
        inside = [l for l in raw if _centre_in(l.rect, pic.rect)]
        pic.lines = len(inside)
        pic.readable = sum(l.confidence >= MIN_CONFIDENCE for l in inside)
    words = [tuple(w[:4]) for w in page.get_text("words")]
    illegible = [p.rect for p in result.pictures if p.illegible]
    kept = []
    for line in raw:
        if line.confidence < MIN_CONFIDENCE:
            continue
        if any(_overlaps(line.rect, w) for w in words):
            continue  # the text layer already has it, exactly
        if any(_centre_in(line.rect, r) for r in illegible):
            continue  # the few lines that "read" in an illegible picture are its luckiest guesses
        kept.append(line)
    result.lines = _dedupe(kept)
    return result


def _centre_in(rect: tuple, area: tuple) -> bool:
    cx, cy = (rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2
    return area[0] <= cx <= area[2] and area[1] <= cy <= area[3]


def _blank(pix) -> bool:
    """A tile with no dark pixel holds nothing to read: skip the model call."""
    samples = pix.samples
    return min(samples[:: max(1, len(samples) // 200000)]) > 200


def _dedupe(lines: list[OcrLine]) -> list[OcrLine]:
    """Two readings of one spot (the detector can split "80" and "8"): keep
    the longer, then the more confident."""
    out: list[OcrLine] = []
    for line in sorted(lines, key=lambda l: (-len(l.text), -l.confidence)):
        if any(_mostly_inside(line.rect, o.rect) for o in out):
            continue
        out.append(line)
    return sorted(out, key=lambda l: (l.rect[1], l.rect[0]))


def _mostly_inside(a: tuple, b: tuple) -> bool:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    area = max(1e-6, (a[2] - a[0]) * (a[3] - a[1]))
    return ix * iy / area >= 0.6


def to_chunks(result: PageOcr, page) -> list[chunker.Chunk]:
    """The read lines as chunks, through the same spatial chunker as the
    text layer, marked with their source so nothing downstream mistakes a
    reading for the text layer."""
    if not result.lines:
        return []
    blocks = [(*l.rect, l.text, i, 0) for i, l in enumerate(result.lines)]
    out = chunker.chunk_page(blocks, page_width=page.rect.width, page_height=page.rect.height)
    settings = {"ocrVersion": OCR_VERSION, "dpi": OCR_DPI, "minConfidence": MIN_CONFIDENCE, "reason": result.reason}
    for c in out:
        c.source_model = SOURCE_MODEL
        c.source_settings = settings
    return out


def read_page(page) -> PageOcr:
    """`plan` then `read`: a whole-page read for text drawn as shapes or a
    page with no text layer, the pictures alone when they are all there is."""
    reason = plan(page)
    if reason is None:
        return PageOcr(reason=None)
    if reason == "pictures on the sheet":
        to_display = page.rotation_matrix  # unrotated -> display
        clips = []
        for p in pictures(page):
            r = fitz.Rect(p.rect) * to_display
            r.normalize()
            clips.append(r & page.rect)
        result = read(page, clips)
    else:
        result = read(page)
    result.reason = reason
    return result
