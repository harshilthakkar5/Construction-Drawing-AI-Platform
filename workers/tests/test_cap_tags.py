"""A pile cap drawn with no tag and no size (UT Law RFI 008). Synthetic PDFs:
no model, no database."""

import sys
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import fitz  # noqa: E402
import pytest  # noqa: E402

import cap_tags as ct  # noqa: E402

PPF = 9.0  # 1/8" = 1'-0", UT Law S1.101


def test_cap_tags_are_tags_and_words_are_not():
    for tag in ("PC-A", "PC-1", "PC2", "PC12A", "CAP-3"):
        assert ct.CAP_TAG.fullmatch(tag), tag
    for word in ("CAPS", "CAP", "PC", "PCS", "PILE", "P-36"):
        assert not ct.CAP_TAG.fullmatch(word), word


def test_a_cap_symbol_with_a_tag_beside_it_is_tagged():
    near, far = {"x": 100, "y": 100}, {"x": 600, "y": 100}
    tags = [(140, 110)]  # 4.6 ft from `near`, 51 ft from `far`
    assert ct.untagged([near, far], tags, PPF) == [far]
    assert ct.untagged([near, far], [], PPF) == [near, far]


def _row(sheet, page_number):
    return NS(document_id="d", page_number=page_number, combined_page_number=page_number,
              sheet_number=sheet, sheet_kind="plan", region_text="")


def _set(tag_near_both=False):
    """S1.101: a foundation plan tagging one cap PC-A, with two symbols to 3/S3.100 —
    one beside that tag, one under P-36 at C-13 with no tag. S3.100: detail 3 titled
    "Section at Pile Cap" over a bare Revit scale line."""
    doc = fitz.open()
    plan = doc.new_page(width=1200, height=800)
    plan.insert_text((80, 700), "Foundation Plan - Level 1", fontsize=24)
    plan.insert_text((80, 730), "1/8\" = 1'-0\"", fontsize=8)
    plan.insert_text((200, 300), "PC-A", fontsize=8)
    for x in (210, 700):  # the stacked symbol: "3" over "S3.100"
        plan.insert_text((x + 9, 254), "3", fontsize=8)
        plan.insert_text((x, 266), "S3.100", fontsize=8)
    plan.insert_text((720, 300), "P-36", fontsize=8)
    plan.insert_text((715, 330), "C-13", fontsize=8)
    if tag_near_both:
        plan.insert_text((740, 280), "PC-B", fontsize=8)
    detail = doc.new_page(width=1200, height=800)
    detail.insert_text((300, 400), "1", fontsize=10)
    detail.insert_text((318, 400), "Section at Exterior", fontsize=10)
    detail.insert_text((320, 412), "3/4\" = 1'-0\"", fontsize=7)
    detail.insert_text((600, 400), "3", fontsize=10)
    detail.insert_text((618, 400), "Section at Pile Cap", fontsize=10)
    detail.insert_text((620, 412), "3/4\" = 1'-0\"", fontsize=7)
    return doc


def test_a_revit_title_over_a_bare_scale_is_read():
    doc = _set()
    assert ct.cap_details(doc[1]) == {"3"}


def test_an_untagged_cap_symbol_on_a_plan_that_tags_caps_is_a_finding():
    doc = _set()
    plan, every = [_row("S1.101", 1)], [_row("S1.101", 1), _row("S3.100", 2)]
    [f], notes = ct.findings(plan, [], lambda d, p: doc[p - 1], every=every)
    assert f.check_type == "untagged_pile_cap" and len(f.evidence) == 1 and notes == []
    assert "under P-36 (C-13), cut by section 3/S3.100" in f.question and "PC-A" in f.question
    # the detail sheet must be FOUND to know the symbol is a cap: unseen, nothing is claimed
    assert ct.findings(plan, [], lambda d, p: doc[p - 1], every=plan)[0] == []


@pytest.mark.parametrize("why", ["tagged", "no tags on the plan", "not a foundation plan"])
def test_left_alone(why):
    doc = _set(tag_near_both=why == "tagged")
    if why == "no tags on the plan":
        for w in doc[0].get_text("words"):
            if w[4].startswith("PC-"):
                doc[0].add_redact_annot(fitz.Rect(w[:4]))
        doc[0].apply_redactions()
    rows = [_row("S1.101", 1), _row("S3.100", 2)]
    if why == "not a foundation plan":
        rows[0].region_text = ""
        doc[0].add_redact_annot(fitz.Rect(70, 670, 600, 710))
        doc[0].apply_redactions()
        doc[0].insert_text((80, 700), "Framing Plan - Level 2", fontsize=24)
    assert ct.findings(rows[:1], [], lambda d, p: doc[p - 1], every=rows)[0] == []


def test_a_bare_scale_marks_a_title_only_beside_a_detail_number():
    import review_aids

    doc = fitz.open()
    page = doc.new_page(width=600, height=400)
    page.insert_text((100, 100), "4", fontsize=10)
    page.insert_text((118, 100), "Section at Pier", fontsize=10)
    page.insert_text((120, 112), "3/4\" = 1'-0\"", fontsize=7)
    page.insert_text((300, 200), "INSULATION - 072100", fontsize=7)  # a note inside a detail
    page.insert_text((300, 212), "1/2\" = 1'-0\"", fontsize=7)
    assert [(n, t) for n, t, _ in review_aids.detail_titles(review_aids._display_lines(page))] == [("4", "Section at Pier")]
