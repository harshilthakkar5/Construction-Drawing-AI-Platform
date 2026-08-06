"""Region scraping is the correctness-critical path of discipline detection:
one box drawn on one page has to read the right text on every other page,
including rotated CAD sheets. These tests build synthetic PDFs so the
coordinate math is exercised without any fixture files.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import fitz  # noqa: E402
import pytest  # noqa: E402

from region import (  # noqa: E402
    MIN_WORD_OVERLAP,
    Region,
    build_display_clip,
    extract_region_text,
    words_in_clip,
)

# A landscape sheet with its number printed in the bottom-right title block —
# the same place a real drawing set puts it.
PAGE_W, PAGE_H = 1224.0, 792.0
SHEET_NUMBER = "S-003.0"
SHEET_POS = (1010, 745)


def _sheet(rotation: int = 0, text: str = SHEET_NUMBER) -> fitz.Document:
    doc = fitz.open()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    page.insert_text(SHEET_POS, text, fontsize=20)
    if rotation:
        page.set_rotation(rotation)
    return doc


def _title_block_region(page: fitz.Page) -> Region:
    """The box a user would drag over the title block on THIS page — computed
    from the displayed page box, exactly as the browser does."""
    rect = page.rect
    # Unrotated geometry: the number sits near (1010, 745) of a 1224x792 page.
    # Convert that to display-space ratios via the page's own rotation matrix.
    point = fitz.Point(SHEET_POS[0], SHEET_POS[1] - 15) * page.rotation_matrix
    other = fitz.Point(SHEET_POS[0] + 120, SHEET_POS[1] + 8) * page.rotation_matrix
    box = fitz.Rect(point, other)
    box.normalize()
    box = box & rect
    return Region(
        rel_x=(box.x0 - rect.x0) / rect.width,
        rel_y=(box.y0 - rect.y0) / rect.height,
        rel_w=box.width / rect.width,
        rel_h=box.height / rect.height,
    )


class TestRotation:
    """The bug this ports around: get_text() ignores page rotation, so the clip
    must be mapped with derotation_matrix. Without that, 90/270 return
    nothing."""

    @pytest.mark.parametrize("rotation", [0, 90, 180, 270])
    def test_same_box_reads_the_sheet_number_at_any_rotation(self, rotation):
        doc = _sheet(rotation)
        page = doc[0]
        text, method = extract_region_text(page, _title_block_region(page))
        assert SHEET_NUMBER in text, f"rotation {rotation} returned {text!r}"
        assert method == "vector"
        doc.close()

    @pytest.mark.parametrize("rotation", [90, 270])
    def test_display_clip_lives_inside_the_rotated_page(self, rotation):
        doc = _sheet(rotation)
        page = doc[0]
        clip = build_display_clip(page, _title_block_region(page))
        assert not clip.is_empty
        assert clip in page.rect  # never spills off the visible page
        doc.close()


class TestExtractionLadder:
    def test_blank_area_returns_none_method(self):
        doc = _sheet()
        # Top-left corner of the sheet is empty.
        text, method = extract_region_text(doc[0], Region(0.02, 0.02, 0.1, 0.1))
        assert text == ""
        assert method == "none"
        doc.close()

    def test_word_pass_catches_a_partially_covered_word(self):
        """A slightly-too-tight box: get_text(clip=…) drops the word, the
        overlap pass keeps it."""
        doc = _sheet()
        page = doc[0]
        words = page.get_text("words")
        assert words, "fixture should have vector text"
        x0, y0, x1, y1 = words[0][:4]
        # Cover ~60% of the word — above MIN_WORD_OVERLAP, below full coverage.
        clip = fitz.Rect(x0, y0, x0 + (x1 - x0) * 0.6, y1)
        assert words_in_clip(page, clip) == words[0][4]
        doc.close()

    def test_word_pass_drops_a_barely_touched_word(self):
        doc = _sheet()
        page = doc[0]
        x0, y0, x1, y1 = page.get_text("words")[0][:4]
        sliver = (x1 - x0) * (MIN_WORD_OVERLAP / 2)
        assert words_in_clip(page, fitz.Rect(x0, y0, x0 + sliver, y1)) == ""
        doc.close()

    def test_no_text_layer_falls_through_to_ocr_without_raising(self):
        """PaddleOCR is optional locally; the wrapper returns "" when it is not
        installed, and the scrape must degrade instead of failing the job."""
        doc = fitz.open()
        doc.new_page(width=PAGE_W, height=PAGE_H)  # nothing on it at all
        text, method = extract_region_text(doc[0], Region(0.8, 0.9, 0.15, 0.05))
        assert (text, method) in {("", "none")} or method == "ocr"
        doc.close()


class TestClipGeometry:
    def test_box_running_off_the_page_is_clamped(self):
        doc = _sheet()
        clip = build_display_clip(doc[0], Region(0.9, 0.9, 0.5, 0.5))
        assert clip.x1 <= doc[0].rect.x1 + 1e-6
        assert clip.y1 <= doc[0].rect.y1 + 1e-6
        doc.close()

    def test_ratios_map_to_the_displayed_page_box(self):
        doc = _sheet()
        clip = build_display_clip(doc[0], Region(0.25, 0.5, 0.25, 0.25))
        assert clip.x0 == pytest.approx(PAGE_W * 0.25)
        assert clip.y0 == pytest.approx(PAGE_H * 0.5)
        assert clip.width == pytest.approx(PAGE_W * 0.25)
        assert clip.height == pytest.approx(PAGE_H * 0.25)
        doc.close()

    def test_rotated_page_reports_transposed_dimensions(self):
        """Guards the assumption the whole module rests on: page.rect is
        rotation-aware, so a 90° sheet is portrait in display space."""
        doc = _sheet(90)
        assert doc[0].rect.width == pytest.approx(PAGE_H)
        assert doc[0].rect.height == pytest.approx(PAGE_W)
        doc.close()
