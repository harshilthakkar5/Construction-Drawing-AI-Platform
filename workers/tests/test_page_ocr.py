"""page_ocr: which pages are read, where each read line lands, and when a
picture is too coarse to read.

No OCR model runs here: a stand-in "engine" finds the solid black boxes drawn
on a synthetic page and reports each as one line. That is enough to test what
this module owns — tiling, the core rule, and mapping a box from a rendered
tile back to the page's UNROTATED space at 0/90/180/270 — and says nothing
about how well PaddleOCR reads.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import fitz  # noqa: E402
import numpy as np  # noqa: E402
import pytest  # noqa: E402

import ocr  # noqa: E402
import page_ocr  # noqa: E402


def _boxes_engine(pix):
    """Each connected run of black pixels -> one 'line' reading BOX."""
    img = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)[:, :, 0]
    dark = img < 60
    out = []
    seen = np.zeros_like(dark)
    ys, xs = np.nonzero(dark)
    for y, x in zip(ys, xs):
        if seen[y, x]:
            continue
        # flood the rectangle (they are axis-aligned and solid)
        x1 = x
        while x1 + 1 < dark.shape[1] and dark[y, x1 + 1]:
            x1 += 1
        y1 = y
        while y1 + 1 < dark.shape[0] and dark[y1 + 1, x]:
            y1 += 1
        seen[y : y1 + 1, x : x1 + 1] = True
        out.append(([[x, y], [x1 + 1, y], [x1 + 1, y1 + 1], [x, y1 + 1]], "BOX", 0.99))
    return out


@pytest.fixture
def engine(monkeypatch):
    monkeypatch.setattr(ocr, "available", lambda: True)
    monkeypatch.setattr(ocr, "image_lines", _boxes_engine)


def _sheet(rotation=0, boxes=((100, 100, 160, 112),), size=(1200, 800)):
    doc = fitz.open()
    page = doc.new_page(width=size[0], height=size[1])
    for b in boxes:
        page.draw_rect(fitz.Rect(b), color=(0, 0, 0), fill=(0, 0, 0))
    page.set_rotation(rotation)
    return doc, page


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_a_read_line_lands_where_it_is_drawn_in_unrotated_space(engine, rotation):
    doc, page = _sheet(rotation, boxes=((100, 100, 160, 112), (900, 600, 1000, 640)))
    result = page_ocr.read(page)
    got = sorted(l.rect for l in result.lines)
    # Within a point and a half: the stand-in engine sees anti-aliased edges.
    assert len(got) == 2
    for rect, want in zip(got, [(100, 100, 160, 112), (900, 600, 1000, 640)]):
        assert rect == pytest.approx(want, abs=1.5)


def test_a_line_in_the_overlap_of_two_tiles_is_kept_once(engine):
    zoom = page_ocr.OCR_DPI / 72
    size = page_ocr.TILE_PX / zoom
    step = size * (1 - page_ocr.OVERLAP)
    # Straddles the first tile boundary, inside both tiles' clips.
    x = step + 5
    doc, page = _sheet(boxes=((x, 50, x + 40, 60),), size=(2 * size, 200))
    assert len(page_ocr.tiles(page.rect, zoom)) > 1
    assert len(page_ocr.read(page).lines) == 1


def test_tile_cores_cover_the_page_exactly_once():
    display = fitz.Rect(0, 0, 2592, 1728)
    pairs = page_ocr.tiles(display, page_ocr.OCR_DPI / 72)
    area = sum(core.width * core.height for _, core in pairs)
    assert area == pytest.approx(display.width * display.height)
    for clip, core in pairs:
        assert clip.contains(core)


def test_text_the_text_layer_already_has_is_not_read_again(engine):
    doc, page = _sheet(boxes=((100, 100, 160, 112), (300, 300, 340, 312)))
    page.insert_text((302, 310), "AB", fontsize=8)  # a text-layer word over the second box
    [line] = page_ocr.read(page).lines
    assert line.rect == pytest.approx((100, 100, 160, 112), abs=1.5)


def test_without_an_engine_nothing_is_claimed(monkeypatch):
    monkeypatch.setattr(ocr, "available", lambda: False)
    doc, page = _sheet()
    result = page_ocr.read(page)
    assert result.ran is False and result.lines == []


def _with_picture(px, rect, rotation=0):
    doc = fitz.open()
    page = doc.new_page(width=1200, height=800)
    pm = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, *px), False)
    pm.clear_with(255)
    page.insert_image(fitz.Rect(rect), pixmap=pm, rotate=rotation)
    return doc, page


def test_a_picture_reports_the_resolution_it_was_placed_at():
    doc, page = _with_picture((300, 200), (0, 0, 432, 288))  # 6 x 4 in
    [pic] = page_ocr.pictures(page)
    assert pic.dpi == pytest.approx(50, abs=0.5)
    assert pic.bits == 8


def test_a_picture_placed_rotated_is_measured_long_side_to_long_side():
    doc, page = _with_picture((300, 200), (0, 0, 288, 432), rotation=90)
    [pic] = page_ocr.pictures(page)
    assert pic.dpi == pytest.approx(50, abs=0.5)


def test_small_pictures_are_logos_not_schedules():
    doc, page = _with_picture((300, 200), (0, 0, 100, 80))
    assert page_ocr.pictures(page) == []


@pytest.mark.parametrize(
    "dpi,lines,readable,bits,illegible",
    [
        (67, 175, 42, 1, True),     # E0.05's panel schedules: 1-bit, strokes lost
        (96, 61, 36, 8, False),     # M0.02's certificate: coarse, but it reads
        (67, 3, 0, 1, False),       # a coarse photo, not a page of text
        (67, 100, 80, 1, False),    # coarse, and it still reads
        (72, 44, 0, 8, False),      # A0.01's product table: grey, readable by eye, OCR or not
        (72, 50, 17, 8, False),     # C-901's product sheet
    ],
)
def test_illegible_means_coarse_one_bit_full_of_text_and_mostly_unread(dpi, lines, readable, bits, illegible):
    assert page_ocr.Picture((0, 0, 1, 1), dpi, lines, readable, bits).illegible is illegible


def test_a_line_is_dropped_below_the_confidence_floor(monkeypatch):
    monkeypatch.setattr(ocr, "available", lambda: True)
    monkeypatch.setattr(ocr, "image_lines", lambda pix: [(q, t, 0.5) for q, t, _ in _boxes_engine(pix)])
    doc, page = _sheet()
    assert page_ocr.read(page).lines == []


def test_the_detector_splitting_one_reading_keeps_the_longer():
    a = page_ocr.OcrLine("80", 0.99, (10, 10, 30, 20))
    b = page_ocr.OcrLine("8", 0.99, (11, 10, 19, 20))
    assert [l.text for l in page_ocr._dedupe([b, a])] == ["80"]


def test_plan_reads_shape_text_before_pictures(monkeypatch):
    # M0.02 has both: read for its pictures alone, its stroke-drawn
    # schedules were missed.
    monkeypatch.setattr(page_ocr.config, "OCR_SHAPE_TEXT_MIN_DRAWINGS", 5)
    doc, page = _with_picture((300, 200), (0, 0, 432, 288))
    page.insert_text((600, 600), "FEW WORDS", fontsize=8)
    for i in range(10):
        page.draw_line((600, 100 + i * 5), (700, 100 + i * 5))
    assert page_ocr.plan(page) == "text drawn as shapes"


def test_plan_leaves_an_ordinary_sheet_alone():
    doc = fitz.open()
    page = doc.new_page(width=1200, height=800)
    for i in range(500):
        page.insert_text((20 + (i % 20) * 55, 20 + (i // 20) * 25), f"W{i}", fontsize=6)
    assert page_ocr.plan(page) is None


def test_a_page_with_no_text_layer_is_always_read():
    doc, page = _sheet()
    assert page_ocr.plan(page) == "no text layer"


def test_ocr_chunks_carry_their_source(engine):
    doc, page = _sheet(boxes=((100, 100, 160, 112),))
    result = page_ocr.read(page)
    [c] = page_ocr.to_chunks(result, page)
    assert c.source_model == page_ocr.SOURCE_MODEL and c.kind == "text"
    assert c.source_settings["ocrVersion"] == page_ocr.OCR_VERSION
    assert c.bbox["x"] == pytest.approx(100, abs=1)
