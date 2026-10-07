"""Shrinking a finding's chunk box to the words it is about.

Placement is checked the way CLAUDE.md asks: clip the page's words out of the
STORED box and look at what comes back — never by comparing a box with the box
it was made from, the green test that hid the rotation bugs.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import fitz  # noqa: E402
import pytest  # noqa: E402

import rfi_pinpoint  # noqa: E402


def _sheet(rotation: int) -> fitz.Document:
    doc = fitz.open()
    page = doc.new_page(width=800, height=500)
    page.insert_text((50, 60), "GENERAL NOTES", fontsize=12)
    page.insert_text((50, 100), "1. GROUT COLOR: TBD", fontsize=10)
    page.insert_text((50, 130), "2. ALL WORK PER CODE", fontsize=10)
    page.insert_text((500, 300), "UP TO", fontsize=10)
    page.insert_text((500, 314), "RTU-3", fontsize=10)
    page.insert_text((500, 328), "100MBH", fontsize=10)
    page.insert_text((700, 450), "RTU-3", fontsize=10)  # the same tag elsewhere, no rating
    page.set_rotation(rotation)
    return doc


def _whole(page) -> dict:
    r = page.rect * page.derotation_matrix  # the unrotated page
    r.normalize()
    return {"x": r.x0, "y": r.y0, "width": r.width, "height": r.height}


def _words_in(page, box: dict) -> str:
    clip = fitz.Rect(box["x"], box["y"], box["x"] + box["width"], box["y"] + box["height"])
    words = [w for w in page.get_text("words") if fitz.Rect(w[:4]) in clip]
    return " ".join(w[4] for w in words)


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_a_note_box_holds_the_note_and_nothing_else(rotation):
    page = _sheet(rotation)[0]
    item = {"bbox": _whole(page), "quote": "1. GROUT COLOR: TBD", "_term": "TBD"}
    box = rfi_pinpoint.locate(page, item)
    assert _words_in(page, box) == "1. GROUT COLOR: TBD"


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_a_tag_is_found_beside_its_rating_not_elsewhere(rotation):
    page = _sheet(rotation)[0]
    item = {"bbox": _whole(page), "quote": "UP TO RTU-3 100MBH", "_term": "RTU-3", "_near": "100MBH"}
    box = rfi_pinpoint.locate(page, item)
    assert _words_in(page, box) == "RTU-3 100MBH"


def test_words_not_in_the_box_leave_it_alone():
    page = _sheet(0)[0]
    chunk_box = {"x": 40, "y": 40, "width": 200, "height": 100}  # the notes only
    assert rfi_pinpoint.locate(page, {"bbox": chunk_box, "quote": "", "_term": "RTU-3"}) is None
    evidence = [{"documentId": "d", "pageNumber": 1, "bbox": dict(chunk_box), "_term": "RTU-3", "_near": "X"}]
    assert rfi_pinpoint.pinpoint(evidence, lambda d, n: page) == 0
    assert evidence[0]["bbox"] == chunk_box
    assert not [k for k in evidence[0] if k.startswith("_")]


def test_internal_keys_are_stripped_even_when_opening_fails():
    evidence = [{"documentId": "d", "pageNumber": 1, "bbox": None, "_term": "TBD"}]
    with pytest.raises(RuntimeError):
        rfi_pinpoint.pinpoint(evidence, lambda d, n: (_ for _ in ()).throw(RuntimeError("gone")))
    assert evidence[0] == {"documentId": "d", "pageNumber": 1, "bbox": None}


def test_items_without_a_term_are_untouched():
    box = {"x": 0, "y": 0, "width": 9, "height": 9}
    evidence = [{"documentId": "d", "pageNumber": 1, "bbox": dict(box)}]
    assert rfi_pinpoint.pinpoint(evidence, lambda d, n: pytest.fail("no page needed")) == 0
    assert evidence[0]["bbox"] == box
