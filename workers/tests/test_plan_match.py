"""plan_match + rfi_columns: lay an enlarged plan over its overall plan and
report the columns they disagree about.

The sheets are drawn here, in the shape of the client's RFI 015 pair (A3.35 at
1/4" over A3.27 at 1/8"): white concrete columns with stipple, standing in a
grey slab, with a scale line and a numbered detail title. Every look-alike the
real sheets produced is drawn too — a dimension's white text mask, the two legs
of a concrete wall corner, a grey pad — because each one was once reported as
a missing column.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import fitz  # noqa: E402
import pytest  # noqa: E402

import plan_match as pm  # noqa: E402
import rfi_columns as rc  # noqa: E402
from rfi_checks import Page  # noqa: E402

OVERALL = 9.0  # 1/8" = 1'-0", points per foot
ENLARGED = 18.0  # 1/4"
# Columns of the level, in FEET from the building's corner: (x, y, w, h).
COLUMNS = [(4, 2, 2, 1), (26, 2, 3, 1), (48, 2, 3, 1), (70, 2, 2, 1), (20, 20, 2, 2), (50, 22, 2, 2)]


def column(page: fitz.Page, x: float, y: float, w: float, h: float, dots: int = 5, outline: bool = False):
    r = fitz.Rect(x, y, x + w, y + h)
    page.draw_rect(r, fill=(1, 1, 1), color=None if outline else (0, 0, 0), width=0.5)
    if outline:  # drawn the other way: a fill, and its edges as separate strokes
        for a, b in ((r.tl, r.tr), (r.tr, r.br), (r.br, r.bl), (r.bl, r.tl)):
            page.draw_line(a, b, color=(0, 0, 0), width=0.5)
    for i in range(dots):
        px = x + w * (i + 1) / (dots + 1)
        page.draw_line((px, y + h / 2), (px + 1, y + h / 2 + 0.6), color=(0.3, 0.3, 0.3), width=0.3)


def plan(page: fitz.Page, ox: float, oy: float, ptft: float, columns, extra=None, width_ft: float = 80):
    """A slab with columns on it, the corner at (ox, oy)."""
    page.draw_rect(fitz.Rect(ox - 10, oy - 10, ox + width_ft * ptft, oy + 30 * ptft), fill=(0.91, 0.91, 0.91), color=None)
    for x, y, w, h in columns:
        column(page, ox + x * ptft, oy + y * ptft, w * ptft, h * ptft)
    if extra:
        extra(page, ox, oy, ptft)


def title(page: fitz.Page, x: float, y: float, number: str, text: str, scale: str):
    page.insert_text((x - 30, y + 14), number, fontsize=14)
    page.insert_text((x, y), text, fontsize=11)
    page.insert_text((x, y + 16), scale, fontsize=9)


def build(enlarged_columns, overall_columns=COLUMNS, rotate=0, extra=None, enlarged_width_ft=80):
    doc = fitz.open()
    overall = doc.new_page(width=1728, height=1152)
    plan(overall, 300, 200, OVERALL, overall_columns)
    title(overall, 300, 1000, "1", "CONCRETE EXHIBIT - LEVEL 14", '1/8" = 1\'-0"')
    enlarged = doc.new_page(width=1728, height=1152)
    plan(enlarged, 100, 150, ENLARGED, enlarged_columns, extra, enlarged_width_ft)
    title(enlarged, 300, 760, "1", "ENLARGED CONCRETE EXHIBIT - LEVEL 14", '1/4" = 1\'-0"')
    if rotate:
        # Rotating the page keeps the drawing: rebuild so display space is
        # what was drawn, as it is on a CAD sheet exported rotated.
        for number in range(len(doc)):
            doc[number].set_rotation(rotate)
    return doc


def sheets(doc):
    a = rc.Sheet.read(Page("a", "d", 2, 2, "A3.35", None, "architectural"), doc[1])
    b = rc.Sheet.read(Page("b", "d", 1, 1, "A3.27", None, "architectural"), doc[0])
    return a, b


def compare(doc):
    a, b = sheets(doc)
    alignments = pm.align_sheets(a.geometry, b.geometry)
    return a, b, alignments, rc.column_mismatches(a, b, alignments)


# --- pure pieces ------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ('1/4" = 1\'-0"', [18.0]),
        ('3/4" = 1\'-0"', [54.0]),
        ('1 1/2" = 1\'-0"', [108.0]),
        # A date on the line before must not become part of the number.
        ('2026.02.27 1/4" = 1\'-0"', [18.0]),
        ("4'-2 1/8\"", []),
        ('@ AT AMENITY DECKS: 1/8" PER FT. MINIMUM', []),
    ],
)
def test_scales_are_read_off_the_scale_line(text, expected):
    assert pm.scales_in(text) == expected


def test_the_ratio_of_an_enlarged_plan_onto_its_overall_plan():
    assert pm.ratio_candidates([18.0], [9.0]) == [0.5]


# --- lining up ---------------------------------------------------------------------


def test_identical_sheets_line_up_and_agree():
    a, b, alignments, (findings, notes) = compare(build(COLUMNS))
    assert len(alignments) == 1
    al = alignments[0]
    assert al.scale == 0.5 and len(al.inliers) == len(COLUMNS)
    assert findings == []
    assert any("6 columns line up, 0 differ" in n for n in notes)
    assert a.details == {al.detail: "detail 1"}


def test_a_missing_a_moved_and_a_resized_column_are_each_reported():
    changed = list(COLUMNS)
    changed[3] = (73, 2, 2, 1)  # moved 3' along the wall
    changed[5] = (50, 22, 3, 2)  # 2'x2' drawn 3'x2'
    changed.append((36, 20, 2, 2))  # a column the overall plan does not show
    _, _, _, (findings, _) = compare(build(changed))
    assert len(findings) == 1
    f = findings[0]
    q = f.question
    assert "A3.35 shows a 2'-0\" x 2'-0\" column" in q and "that A3.27 does not show" in q
    assert "is 3'-0\" from where A3.27 shows it" in q
    assert "is 3'-0\" x 2'-0\" on A3.35 and 2'-0\" x 2'-0\" on A3.27" in q
    # Three differences against four matching columns: real, but enough
    # disagreement that the alignment itself deserves a look.
    assert f.check_type == "column_mismatch" and f.confidence == "medium"
    # Every piece of evidence points at a box on one of the two sheets.
    assert {e["sheetNumber"] for e in f.evidence} == {"A3.35", "A3.27"}
    assert all(e["bbox"]["width"] > 0 for e in f.evidence)


def test_a_column_only_the_overall_plan_shows_is_reported_too():
    _, _, _, (findings, _) = compare(build(COLUMNS[:-1]))
    assert "A3.27 shows a 2'-0\" x 2'-0\" column" in findings[0].question
    assert findings[0].confidence == "high"  # one difference, five columns agree


def test_the_same_differences_give_the_same_fingerprint_on_a_re_read():
    changed = COLUMNS[:-1]
    first = compare(build(changed))[3][0][0].fingerprint
    again = compare(build(changed))[3][0][0].fingerprint
    assert first == again


@pytest.mark.parametrize("rotation", [90, 270])
def test_a_rotated_pair_lines_up_and_its_evidence_boxes_hold_the_column(rotation):
    """RFI 015's sheets are /Rotate 90. Evidence boxes are in the page's
    UNROTATED space, like chunk bboxes, and must still frame the column."""
    changed = COLUMNS + [(36, 20, 2, 2)]
    doc = build(changed, rotate=rotation)
    _, _, alignments, (findings, _) = compare(doc)
    assert len(alignments) == 1 and len(findings) == 1
    box = findings[0].evidence[0]["bbox"]
    clip = fitz.Rect(box["x"], box["y"], box["x"] + box["width"], box["y"] + box["height"])
    # The unrotated box, drawn in unrotated space, covers the column's fill.
    fills = [
        d for d in doc[1].get_drawings()
        if d.get("fill") == (1.0, 1.0, 1.0) and fitz.Rect(d["rect"]).intersects(clip)
    ]
    assert fills


# --- look-alikes, each once reported as a column ----------------------------------------


def _text_mask(page, ox, oy, ptft):
    r = fitz.Rect(ox + 36 * ptft, oy + 10 * ptft, ox + 39.5 * ptft, oy + 11.2 * ptft)
    page.draw_rect(r, fill=(1, 1, 1), color=None)
    for i in range(4):
        page.draw_line((r.x0 + 5 + 8 * i, r.y0 + 4), (r.x0 + 6 + 8 * i, r.y0 + 5), color=(0.3, 0.3, 0.3), width=0.3)
    page.insert_text((r.x0 + 2, r.y1 - 3), "26'-9 5/8\"", fontsize=8)


def _wall_corner(page, ox, oy, ptft):
    leg_a = fitz.Rect(ox + 36 * ptft, oy + 26 * ptft, ox + 39.3 * ptft, oy + 26.8 * ptft)
    leg_b = fitz.Rect(leg_a.x0, leg_a.y1, leg_a.x0 + 0.8 * ptft, leg_a.y1 + 3 * ptft)
    for r in (leg_a, leg_b):
        page.draw_rect(r, fill=(1, 1, 1), color=(0, 0, 0), width=0.5)
        for i in range(5):
            page.draw_line((r.x0 + 2 + 2 * i, r.y0 + 2), (r.x0 + 3 + 2 * i, r.y0 + 2.6), color=(0.3, 0.3, 0.3), width=0.3)


def _grey_pad(page, ox, oy, ptft):
    r = fitz.Rect(ox + 60 * ptft, oy + 12 * ptft, ox + 62.7 * ptft, oy + 12.8 * ptft)
    page.draw_rect(r, fill=(0.8, 0.8, 0.8), color=(0, 0, 0), width=0.5)
    for i in range(5):
        page.draw_line((r.x0 + 3 + 4 * i, r.y0 + 4), (r.x0 + 4 + 4 * i, r.y0 + 4.6), color=(0.3, 0.3, 0.3), width=0.3)


@pytest.mark.parametrize("extra", [_text_mask, _wall_corner, _grey_pad], ids=["text mask", "wall corner", "grey pad"])
def test_a_look_alike_is_not_a_missing_column(extra):
    _, _, alignments, (findings, _) = compare(build(COLUMNS, extra=extra))
    assert len(alignments) == 1
    assert findings == []


def test_a_small_scale_column_with_two_dots_counts_when_it_has_its_own_outline():
    """At 1/8" a 2'x1' column holds two stipple dots — A3.27's did, and was
    reported missing until the outline counted."""
    doc = fitz.open()
    page = doc.new_page(width=600, height=400)
    page.insert_text((50, 380), '1/8" = 1\'-0"', fontsize=9)
    column(page, 100, 100, 18, 9, dots=2, outline=True)
    column(page, 200, 100, 18, 9, dots=2)  # no outline of its own: too little to tell
    found = pm.page_elements(page, pm.page_scales(page))
    assert [(round(e.x0), round(e.y0)) for e in found] == [(100, 100)]


# --- a detail that aligns to the wrong area says so -------------------------------------


def test_too_many_differences_is_reported_as_a_bad_alignment_not_as_findings(monkeypatch):
    monkeypatch.setattr(rc, "MAX_DIFF_SHARE", 0.1)
    _, _, _, (findings, notes) = compare(build(COLUMNS[:-1]))
    assert findings == []
    assert any("set aside" in n for n in notes)


# --- pictures for the model (B) ----------------------------------------------------------


def test_pair_windows_cut_the_same_area_out_of_both_sheets():
    changed = COLUMNS + [(36, 20, 2, 2)]
    a, b, alignments, _ = compare(build(changed))
    al = alignments[0]
    diffs = pm.differences(al, a.geometry, b.geometry)
    windows = rc.pair_windows(al, a, b, diffs, 3)
    assert len(windows) == 3
    first_a, first_b = windows[0]
    # The first window is centred on the difference...
    extra = next(d for d in diffs if d.kind == "only_a").a
    assert first_a.contains(fitz.Point(extra.cx, extra.cy))
    # ...and each B window is its A window mapped at the scale ratio.
    for ra, rb in windows:
        assert rb.width == pytest.approx(ra.width * 0.5, abs=0.5)
        x, y = al.to_b(ra.x0, ra.y0)
        assert (rb.x0, rb.y0) == (pytest.approx(x, abs=0.5), pytest.approx(y, abs=0.5))


def test_a_size_difference_at_the_edge_of_an_enlarged_detail_is_not_reported():
    """A3.35's detail 1 ends on a column it draws 1'-10" wide, where A3.27
    draws it 2'-6": the enlarged detail's window may cut the column off, which
    is not a size anybody chose."""
    overall = COLUMNS + [(77.5, 10, 2.5, 1)]
    at_edge = COLUMNS + [(78.0, 10, 2.0, 1)]  # its right side IS the drawing's edge
    _, _, _, (findings, _) = compare(build(at_edge, overall))
    assert findings == []
    inside = COLUMNS + [(60, 10, 2.0, 1)]
    _, _, _, (findings, _) = compare(build(inside, COLUMNS + [(59.75, 10, 2.5, 1)]))
    assert "is 2'-0\" x 1'-0\" on A3.35 and 2'-6\" x 1'-0\" on A3.27" in findings[0].question


def test_columns_outside_the_enlarged_window_are_not_missing_from_it():
    """An enlarged plan shows a WINDOW of the overall plan; the overall plan's
    columns beyond that window are not columns the enlarged plan left out."""
    window = [c for c in COLUMNS if c[0] < 55]
    _, _, alignments, (findings, notes) = compare(build(window, enlarged_width_ft=55))
    assert len(alignments) == 1 and findings == []
    assert any("line up, 0 differ" in n for n in notes)


def test_too_few_columns_do_not_line_anything_up():
    _, _, alignments, _ = compare(build(COLUMNS[:2], enlarged_width_ft=30))
    assert alignments == []


def test_a_regular_bay_that_lines_up_one_bay_over_is_refused():
    """Identical columns at an even spacing match themselves shifted by one
    bay almost as well as unshifted — that shift would move every column."""
    row = [(4 + 10 * i, 2, 2, 1) for i in range(8)]
    _, _, alignments, _ = compare(build(row[2:6], row, enlarged_width_ft=65))
    assert alignments == []
