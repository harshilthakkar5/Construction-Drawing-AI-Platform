"""
PDF Region Sheet Scraper API
-----------------------------
Accepts a PDF + a relative bounding-box region (drawn by the user on ONE page
in the frontend) and applies that SAME region to every page in the document,
extracting whatever text sits inside the box on each page (e.g. sheet numbers
like "A-101", "S-003", "QTO4").

Coordinate systems (this is the part that used to be broken):
  * `page.rect` IS rotation-aware. For a page with /Rotate 90 it returns the
    landscape rectangle the user actually sees in the browser -- the same thing
    pdf.js draws. So building the clip box from `page.rect` is correct.
  * `page.get_text()` / `page.get_textpage()` are NOT. Internally PyMuPDF sets
    the page rotation to 0 before building the text page, so the `clip` argument
    is interpreted in the *unrotated* MediaBox space.
  For a rotated CAD sheet those two spaces are transposed (e.g. 3456x2592 vs
  2592x3456), so a box near the right edge of the visible page lands completely
  off-page once it is handed to get_text() -- and every page comes back
  NOT_FOUND. The fix is `clip * page.derotation_matrix` before extraction.
  `page.get_pixmap()` IS rotation-aware, so the OCR path keeps the display-space
  rectangle.

Extraction strategy per page:
  1. Native vector text inside the (de-rotated) clip rectangle.
  2. Word-level overlap pass, which also catches words that only partially
     intersect the box (a slightly-too-tight selection).
  3. If the page is a scan/flattened drawing, render just that region at high
     DPI and run Tesseract OCR on it.

Run:
    pip install -r requirements.txt
    uvicorn main:app --reload --port 8000
"""

import io
import json
import logging

import fitz  # PyMuPDF
from PIL import Image
from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

try:
    import pytesseract

    _PYTESSERACT_IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover
    pytesseract = None
    _PYTESSERACT_IMPORT_ERROR = exc

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("pdf-region-scraper")

app = FastAPI(title="PDF Region Sheet Scraper API", version="1.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# A word must have at least this fraction of its own area inside the selection
# box to count as "inside" during the overlap pass.
MIN_WORD_OVERLAP = 0.30


class RegionPayload(BaseModel):
    rel_x: float = Field(..., ge=0.0, le=1.0, description="Relative X of box top-left (0-1)")
    rel_y: float = Field(..., ge=0.0, le=1.0, description="Relative Y of box top-left (0-1)")
    rel_w: float = Field(..., gt=0.0, le=1.0, description="Relative width of box (0-1)")
    rel_h: float = Field(..., gt=0.0, le=1.0, description="Relative height of box (0-1)")


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "ocr_available": _tesseract_status()[0],
        "ocr_detail": _tesseract_status()[1],
    }


def _tesseract_status() -> tuple[bool, str]:
    """Is the Tesseract *binary* actually callable? (pip install is not enough.)"""
    if pytesseract is None:
        return False, f"pytesseract not importable: {_PYTESSERACT_IMPORT_ERROR}"
    try:
        version = pytesseract.get_tesseract_version()
        return True, f"tesseract {version}"
    except Exception as exc:
        return False, f"tesseract binary not found: {exc}"


def build_display_clip(page: fitz.Page, region: RegionPayload) -> fitz.Rect:
    """
    Clip rectangle in the page's *displayed* (rotation-applied) coordinate
    space -- i.e. the same space the user drew the box in, in the browser.
    """
    rect = page.rect
    x0 = rect.x0 + rect.width * region.rel_x
    y0 = rect.y0 + rect.height * region.rel_y
    x1 = x0 + rect.width * region.rel_w
    y1 = y0 + rect.height * region.rel_h
    clip = fitz.Rect(x0, y0, x1, y1)
    clip.normalize()
    return clip & rect  # never let it spill off the page


def words_in_clip(page: fitz.Page, clip_unrotated: fitz.Rect) -> str:
    """Overlap-tolerant pass: keep words mostly inside the box, in reading order."""
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


def extract_text_or_ocr(
    page: fitz.Page, clip_display: fitz.Rect, ocr_dpi: int = 300
) -> tuple[str, str]:
    """Returns (text, method) where method is 'vector', 'words', 'ocr' or 'none'."""
    # get_text() works in UNROTATED page space -> map the box back.
    clip_text = (clip_display * page.derotation_matrix).normalize()

    extracted = page.get_text("text", clip=clip_text).strip()
    if extracted:
        return " ".join(extracted.split()), "vector"

    extracted = words_in_clip(page, clip_text)
    if extracted:
        return " ".join(extracted.split()), "words"

    ok, detail = _tesseract_status()
    if not ok:
        logger.warning("OCR unavailable on page %s (%s)", page.number + 1, detail)
        return "", "none"

    try:
        # get_pixmap IS rotation-aware, so it takes the display-space rectangle.
        pix = page.get_pixmap(clip=clip_display, dpi=ocr_dpi)
        img = Image.open(io.BytesIO(pix.tobytes("png")))
        # PSM 6: assume a single uniform block of text (title-block label)
        ocr_text = pytesseract.image_to_string(img, config="--psm 6").strip()
        if ocr_text:
            return " ".join(ocr_text.split()), "ocr"
        return "", "none"
    except Exception as exc:
        logger.warning("OCR failed on page %s: %s", page.number + 1, exc)
        return "", "none"


@app.post("/api/extract-sheet-numbers")
async def extract_sheet_numbers(
    file: UploadFile = File(...),
    region: str = Form(..., description="JSON stringified RegionPayload"),
):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

    try:
        region_data = RegionPayload(**json.loads(region))
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid region payload: {exc}")

    pdf_bytes = await file.read()
    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to open PDF: {exc}")

    if doc.page_count == 0:
        doc.close()
        raise HTTPException(status_code=400, detail="PDF has no pages.")

    ocr_available, ocr_detail = _tesseract_status()

    results = []
    for page_idx in range(doc.page_count):
        page = doc[page_idx]
        clip_display = build_display_clip(page, region_data)
        text, method = extract_text_or_ocr(page, clip_display)

        results.append(
            {
                "page": page_idx + 1,
                "sheet_number": text if text else "NOT_FOUND",
                "extraction_method": method,
                "page_rotation": page.rotation,
                "page_size": [round(page.rect.width, 1), round(page.rect.height, 1)],
                "clip": [round(v, 1) for v in clip_display],
            }
        )

    doc.close()

    found = sum(1 for r in results if r["sheet_number"] != "NOT_FOUND")
    return {
        "filename": file.filename,
        "total_pages": len(results),
        "found_count": found,
        "not_found_count": len(results) - found,
        "ocr_available": ocr_available,
        "ocr_detail": ocr_detail,
        "data": results,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)