"""The RFI checks decide what is missing, so every guard that keeps a false
finding out is tested here as carefully as the finding itself.

The asymmetry these tests encode: a missed gap is found the normal way, by a
person reading the drawings. A false one becomes a question someone has to
answer, and ten of those and nobody opens the feature again. So most tests
below are "this must NOT be a finding".
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402

import rfi_checks  # noqa: E402
from rfi_checks import (  # noqa: E402
    Chunk,
    Page,
    dangling_references,
    fingerprint,
    normalize,
    open_item_notes,
    open_items,
    run_all,
    sheet_references,
    signature,
    unscheduled_marks,
)

SHARED = Path(__file__).resolve().parents[2] / "packages/shared/src/index.ts"


def page(pid, sheet, n=None, *, doc="doc-1", region=None):
    n = n if n is not None else int(re.sub(r"\D", "", pid) or 1)
    return Page(
        id=pid,
        document_id=doc,
        page_number=n,
        combined_page_number=n,
        sheet_number=sheet,
        region_text=region,
    )


def chunk(cid, pid, text, identifiers=()):
    return Chunk(id=cid, page_id=pid, text=text, bbox={"x": 1, "y": 2, "width": 3, "height": 4},
                 identifiers=tuple(identifiers))


# A structural set numbered S-101 .. S-103, the shape most of these tests use.
SET = [page("p1", "S-101"), page("p2", "S-102"), page("p3", "S-103")]


# --- normalization -------------------------------------------------------------


def test_normalize_matches_the_identifier_index():
    # Same rule as cdip_identifiers(): upper, every separator gone.
    assert normalize("S-501") == "S501"
    assert normalize("s.100.0") == "S1000"
    assert normalize("PC-4") == "PC4"


def test_signature_is_the_shape_of_a_sheet_number():
    assert signature("S101") == ("S", 3, False)
    assert signature("S102A") == ("S", 3, True)
    assert signature("FP201") == ("FP", 3, False)
    assert signature("HELLO") is None


def test_fingerprint_is_the_finding_not_its_spelling():
    # "S501" and "S-501" are one missing sheet; a re-scan must not propose it twice.
    assert fingerprint("dangling_reference", normalize("S-501")) == fingerprint(
        "dangling_reference", normalize("S501")
    )
    assert fingerprint("dangling_reference", "S501") != fingerprint("dangling_reference", "S502")
    assert fingerprint("dangling_reference", "S501") != fingerprint("unscheduled_mark", "S501")


# --- sheet references ----------------------------------------------------------


@pytest.mark.parametrize(
    "text, token",
    [
        ("SEE 5/S-501 FOR PILE DETAIL", "S-501"),
        ("REFER TO SHEET S-501", "S-501"),
        ("SEE DETAIL 4 ON S-501", "S-501"),
        ("A/S501 TYP.", "S501"),
        ("see s-501", "S-501"),
    ],
)
def test_references_with_a_pointer_are_found(text, token):
    assert token in [t for t, _, _ in sheet_references(text)]


@pytest.mark.parametrize(
    "text",
    [
        "HSS8X8X3/8 COLUMN",  # a fraction inside a member size is not a callout
        "ASTM A36/A572 GR 50",  # a material grade, not detail 36 on sheet A572
        "ASTM A500/A501",
        "S-501",  # a sheet naming itself in its title block points at nothing
        "1/2\" GAP",
        "3/8 PLATE",
    ],
)
def test_text_without_a_pointer_is_not_a_reference(text):
    assert sheet_references(text) == []


def test_a_reference_to_a_missing_sheet_in_an_issued_discipline_is_high():
    chunks = [chunk("c1", "p1", "PILE CAP PER 5/S-501")]
    findings, _ = dangling_references(SET, chunks)
    assert len(findings) == 1
    f = findings[0]
    assert f.check_type == "dangling_reference"
    assert f.confidence == "high"
    assert "S-501" in f.subject and "S-501" in f.question
    assert f.evidence[0]["sheetNumber"] == "S-101"
    assert f.evidence[0]["bbox"] == {"x": 1, "y": 2, "width": 3, "height": 4}


def test_a_reference_to_a_sheet_that_exists_is_not_a_finding():
    findings, _ = dangling_references(SET, [chunk("c1", "p1", "SEE 5/S-102")])
    assert findings == []


def test_a_column_mark_behind_a_slash_is_not_a_missing_sheet():
    # "5/C3" in a set numbered S-101: C3 has the wrong SHAPE to be a sheet here.
    findings, _ = dangling_references(SET, [chunk("c1", "p1", "SEE 5/C3 AT GRID 4")])
    assert findings == []


def test_a_suffix_that_differs_from_the_set_is_kept_as_low():
    # A set numbered S-101P referencing S-501: maybe another package's sheet,
    # maybe a missing one. Weaker evidence, not none — shown, not dropped.
    pages = [page("p1", "S-101P"), page("p2", "S-102P")]
    findings, _ = dangling_references(pages, [chunk("c1", "p1", "SEE 5/S-501")])
    assert [(f.facts["missingSheet"], f.confidence) for f in findings] == [("S-501", "low")]


def test_a_matching_suffix_is_not_downgraded():
    pages = [page("p1", "S-101P"), page("p2", "S-102P")]
    findings, _ = dangling_references(pages, [chunk("c1", "p1", "SEE 5/S-501P")])
    assert [f.confidence for f in findings] == ["high"]


def test_a_sheet_whose_number_was_misread_is_not_reported_missing():
    # S-104 exists; the classifier just failed to read its number. Its own
    # title-block text still says S-104.
    pages = SET + [page("p4", None, region="STRUCTURAL SECTIONS  S-104  REV 2")]
    findings, _ = dangling_references(pages, [chunk("c1", "p1", "SEE 2/S-104")])
    assert findings == []


def test_a_whole_missing_discipline_is_low_not_high():
    # No architectural sheet is in the project at all — most likely just not
    # uploaded, which is a note to the uploader rather than an RFI.
    findings, _ = dangling_references(SET, [chunk("c1", "p1", "SEE A-301 FOR FINISHES")])
    assert [f.confidence for f in findings] == ["low"]


def test_unread_pages_downgrade_high_to_medium_and_say_why():
    pages = SET + [page("p4", None)]
    findings, notes = dangling_references(pages, [chunk("c1", "p1", "SEE 5/S-501")])
    assert [f.confidence for f in findings] == ["medium"]
    assert any("no sheet number read" in n for n in notes)


def test_a_project_with_no_sheet_numbers_skips_the_check_out_loud():
    findings, notes = dangling_references([page("p1", None)], [chunk("c1", "p1", "SEE 5/S-501")])
    assert findings == []
    assert any("skipped" in n for n in notes)


def test_one_missing_sheet_referenced_everywhere_is_one_finding():
    pages = [page(f"p{i}", f"S-10{i}") for i in range(1, 9)]
    chunks = [chunk(f"c{i}", f"p{i}", "SEE 5/S-501") for i in range(1, 9)]
    findings, _ = dangling_references(pages, chunks)
    assert len(findings) == 1
    assert len(findings[0].evidence) == rfi_checks.MAX_EVIDENCE


# --- marks missing from their schedule -----------------------------------------


SCHEDULE_TEXT = "PILE CAP SCHEDULE\nMARK SIZE REINF\nPC1 6'X6' #8@12\nPC2 8'X8' #9@12\nPC3 10'X10' #9@10"


def mark_project(plan_texts, *, schedule_ids=("PC1", "PC2", "PC3")):
    pages = [page("p1", "S-101"), page("p2", "S-102"), page("p5", "S-501")]
    chunks = [chunk("sched", "p5", SCHEDULE_TEXT, schedule_ids)]
    for i, (pid, text, ids) in enumerate(plan_texts):
        chunks.append(chunk(f"plan{i}", pid, text, ids))
    return pages, chunks


def test_a_mark_missing_from_its_schedule_is_found():
    pages, chunks = mark_project([("p1", "PC4 AT GRID 7/D", ["PC4"])])
    findings, _ = unscheduled_marks(pages, chunks)
    assert [f.facts["mark"] for f in findings] == ["PC4"]
    f = findings[0]
    assert f.confidence == "medium"  # one call-out could still be a stray note
    assert "PILE CAP SCHEDULE" in f.question
    roles = [e["role"] for e in f.evidence]
    assert roles == ["finding", "context"], "the schedule it is missing from is shown too"


def test_a_mark_called_out_twice_is_high():
    pages, chunks = mark_project([("p1", "PC4 AT 7/D", ["PC4"]), ("p2", "PC-4 AT 9/D", ["PC4"])])
    findings, _ = unscheduled_marks(pages, chunks)
    assert [f.confidence for f in findings] == ["high"]


def test_a_mark_that_is_in_the_schedule_is_not_a_finding():
    pages, chunks = mark_project([("p1", "PC2 AT GRID 7/D", ["PC2"])])
    assert unscheduled_marks(pages, chunks)[0] == []


def test_a_schedule_split_across_chunks_still_counts_its_second_half():
    # The continuation chunk has no word SCHEDULE; it is on the schedule's
    # PAGE, and that is what counts.
    pages, chunks = mark_project([("p1", "PC4 AT GRID 7/D", ["PC4"])])
    chunks.append(chunk("sched-2", "p5", "PC4 12'X12' #10@10", ["PC4"]))
    assert unscheduled_marks(pages, chunks)[0] == []


def test_no_schedule_means_no_findings_and_a_note():
    pages = [page("p1", "S-101")]
    findings, notes = unscheduled_marks(pages, [chunk("c1", "p1", "PC4 AT 7/D", ["PC4"])])
    assert findings == []
    assert any("no schedule" in n for n in notes)


def test_one_scheduled_mark_is_not_a_schedule():
    pages, chunks = mark_project([("p1", "PC4 AT 7/D", ["PC4"])], schedule_ids=("PC1",))
    assert unscheduled_marks(pages, chunks)[0] == []


def test_a_mark_the_wrong_shape_for_its_schedule_is_ignored():
    # A slab schedule of S1, S2 says nothing about S501, which is a sheet.
    pages = [page("p1", "S-101"), page("p5", "S-102")]
    chunks = [
        chunk("sched", "p5", "SLAB SCHEDULE S1 6\" S2 8\"", ["S1", "S2"]),
        chunk("plan", "p1", "SEE S501", ["S501"]),
    ]
    assert unscheduled_marks(pages, chunks)[0] == []


def test_sheet_numbers_are_never_marks():
    # A small set numbered P1..P3 beside a fixture schedule of P4..P6: the sheet
    # numbers share the family AND the shape, so only the exclusion stops
    # "SEE P2" reading as a fixture missing from its schedule.
    pages = [page("p1", "P1"), page("p2", "P2"), page("p3", "P3")]
    chunks = [
        chunk("sched", "p3", "PLUMBING FIXTURE SCHEDULE P4 WC P5 LAV P6 SINK", ["P4", "P5", "P6"]),
        chunk("plan", "p1", "RISER, SEE P2", ["P2"]),
    ]
    assert unscheduled_marks(pages, chunks)[0] == []


def test_an_indexed_mark_the_text_does_not_show_is_skipped():
    # Nothing to quote means nothing for a reviewer to check.
    pages, chunks = mark_project([("p1", "UNRELATED TEXT", ["PC4"])])
    assert unscheduled_marks(pages, chunks)[0] == []


# --- open items --------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, confidence",
    [
        ("BEAM SIZE TBD", "high"),
        ("BEAM SIZE T.B.D.", "high"),
        ("ELEVATION TO BE DETERMINED", "high"),
        ("FINISH TBC", "medium"),
        ("ANCHOR EMBED ???", "medium"),
        ("VERIFY IN FIELD", "low"),
        ("DIM V.I.F.", "low"),
    ],
)
def test_open_item_markers(text, confidence):
    hits = open_items(text)
    assert len(hits) == 1, hits
    assert hits[0][2] == confidence


def test_overlapping_patterns_report_once_at_the_stronger_confidence(monkeypatch):
    # No two patterns overlap today; this guards the list as it grows.
    weak = (re.compile(r"\bELEV TBD\b"), "low", "weak")
    strong = (re.compile(r"\bELEV\b"), "high", "strong")
    monkeypatch.setattr(rfi_checks, "_OPEN_ITEM_PATTERNS", [weak, strong])
    assert [h[2] for h in open_items("ELEV TBD")] == ["high"]
    monkeypatch.setattr(rfi_checks, "_OPEN_ITEM_PATTERNS", [strong, weak])
    assert [h[2] for h in open_items("ELEV TBD")] == ["high"]


@pytest.mark.parametrize("text", ["STBD SIDE", "TBDX", "WHAT? YES", "VIFTER"])
def test_look_alikes_are_not_open_items(text):
    assert open_items(text) == []


def test_the_same_note_on_many_sheets_is_one_open_item():
    pages = [page(f"p{i}", f"S-10{i}") for i in range(1, 9)]
    chunks = [chunk(f"c{i}", f"p{i}", "GENERAL NOTES\nTOP OF PILE ELEV TBD") for i in range(1, 9)]
    findings, _ = open_item_notes(pages, chunks)
    assert len(findings) == 1
    assert len(findings[0].evidence) == rfi_checks.MAX_EVIDENCE
    assert findings[0].facts["note"] == "TOP OF PILE ELEV TBD"


def test_open_items_are_ordered_strongest_first():
    pages = [page("p1", "S-101")]
    chunks = [chunk("c1", "p1", "DIM VERIFY IN FIELD\nBEAM TBD")]
    findings, _ = open_item_notes(pages, chunks)
    assert [f.confidence for f in findings] == ["high", "low"]


# --- running them ------------------------------------------------------------------


def test_the_cap_keeps_the_strongest_and_says_so(monkeypatch):
    monkeypatch.setattr(rfi_checks, "MAX_FINDINGS_PER_CHECK", 3)
    pages = [page("p1", "S-101")]
    text = "\n".join([f"ITEM {i} TBD" for i in range(5)] + ["DIM VERIFY IN FIELD"])
    findings, notes = open_item_notes(pages, [chunk("c1", "p1", text)])
    assert len(findings) == 3
    assert all(f.confidence == "high" for f in findings)
    assert any("only the first 3" in n for n in notes)


def test_run_all_runs_every_check():
    pages, chunks = mark_project([("p1", "PC4 AT 7/D. SEE 5/S-509. PILE TIP TBD", ["PC4"])])
    findings, _ = run_all(pages, chunks)
    assert {f.check_type for f in findings} == set(rfi_checks.CHECK_TYPES)


def test_check_types_match_the_labels_the_ui_renders():
    """A check the UI has no label for renders as its raw key."""
    source = SHARED.read_text()
    block = re.search(r"export const RFI_CHECK_LABELS = \{(.*?)\} as const;", source, re.S)
    assert block, "RFI_CHECK_LABELS not found in @cdip/shared"
    keys = set(re.findall(r"^\s*(\w+):", block.group(1), re.M))
    assert keys == set(rfi_checks.CHECK_TYPES)
