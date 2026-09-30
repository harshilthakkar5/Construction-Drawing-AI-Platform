"""The unrotated -> displayed box mapping the viewer uses (@cdip/shared
displayBox) is checked against a fixture; this regenerates every case of that
fixture from PyMuPDF itself, so the fixture cannot drift from the library the
stored boxes come from."""

import json
from pathlib import Path

import fitz
import pytest

FIXTURE = Path(__file__).resolve().parents[2] / "packages/shared/fixtures/display-box.json"
CASES = json.loads(FIXTURE.read_text())["cases"]


@pytest.mark.parametrize("case", CASES, ids=lambda c: f"rot{c['rotation']}-{c['pageWidth']:.0f}x{c['pageHeight']:.0f}")
def test_the_fixture_is_what_pymupdf_does(case):
    rot = case["rotation"]
    # The fixture gives the DISPLAY size; the unrotated page is that, turned back.
    w, h = (case["pageWidth"], case["pageHeight"]) if rot in (0, 180) else (case["pageHeight"], case["pageWidth"])
    page = fitz.open().new_page(width=w, height=h)
    page.set_rotation(rot)
    b = case["bbox"]
    r = fitz.Rect(b["x"], b["y"], b["x"] + b["width"], b["y"] + b["height"]) * page.rotation_matrix
    r.normalize()
    d = case["display"]
    assert (r.x0, r.y0, r.width, r.height) == pytest.approx((d["x"], d["y"], d["width"], d["height"]), abs=0.01)
