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
    Finding,
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


def index(pid="p1", sheets=("G-001", "S-101", "S-102", "S-103", "S-104", "A-101")):
    """The cover sheet's drawing list — the ISSUED set. A reference is only an
    RFI when the sheet it names is missing from this, not merely from the
    upload."""
    return chunk("index", pid, "SHEET INDEX\n" + "\n".join(f"{s} SHEET TITLE" for s in sheets))


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
    chunks = [chunk("c1", "p1", "PILE CAP PER 5/S-501"), index()]
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
    findings, _ = dangling_references(pages, [chunk("c1", "p1", "SEE 5/S-501"), index(sheets=("S-101P", "S-102P", "S-103P", "S-104P", "S-105P"))])
    assert [(f.facts["missingSheet"], f.confidence) for f in findings] == [("S-501", "low")]


def test_a_matching_suffix_is_not_downgraded():
    pages = [page("p1", "S-101P"), page("p2", "S-102P")]
    findings, _ = dangling_references(pages, [chunk("c1", "p1", "SEE 5/S-501P"), index(sheets=("S-101P", "S-102P", "S-103P", "S-104P", "S-105P"))])
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
    findings, _ = dangling_references(SET, [chunk("c1", "p1", "SEE A-301 FOR FINISHES"), index(sheets=("G-001", "S-101", "S-102", "S-103", "S-104"))])
    assert [f.confidence for f in findings] == ["low"]


def test_unread_pages_downgrade_high_to_medium_and_say_why():
    pages = SET + [page("p4", None)]
    findings, notes = dangling_references(pages, [chunk("c1", "p1", "SEE 5/S-501"), index()])
    assert [f.confidence for f in findings] == ["medium"]
    assert any("no sheet number read" in n for n in notes)


def test_with_no_sheet_index_a_missing_sheet_is_a_note_not_an_rfi():
    """The client's set: A3.32 says "REFER TO RAMP SECTIONS ON A5.14", and
    A5.14 is part of the issued set — it was simply not uploaded. Without the
    drawing list nothing can tell those apart, so nothing is proposed."""
    findings, notes = dangling_references(SET, [chunk("c1", "p1", "REFER TO RAMP SECTIONS ON S-514")])
    assert findings == []
    assert any("No sheet index" in n and "S-514" in n for n in notes)


def test_a_sheet_the_index_lists_was_issued_and_merely_not_uploaded():
    chunks = [chunk("c1", "p1", "SEE 5/S-104"), index()]  # S-104 is listed, not uploaded
    findings, notes = dangling_references(SET, chunks)
    assert findings == []
    assert any("listed in the sheet index but were not uploaded" in n and "S-104" in n for n in notes)


def test_a_page_mentioning_a_drawing_list_is_not_the_index():
    # Fewer than MIN_INDEX_ENTRIES sheet numbers: a note, not the list.
    note = chunk("n", "p2", "SEE SHEET INDEX ON G-001 AND S-101")
    assert rfi_checks.sheet_index(SET, [note]) is None
    assert rfi_checks.sheet_index(SET, [index()]) == {"G001", "S101", "S102", "S103", "S104", "A101"}
    # A reference made ON the cover sheet is not an index entry...
    noted = chunk("note", "p1", "SEE A-301 FOR FINISHES")
    assert "A301" not in rfi_checks.sheet_index(SET, [index(), noted])
    # ...but a sheet both listed and referenced there is still listed.
    both = chunk("both", "p1", "SHEET INDEX\n" + "\n".join(f"{x} TITLE" for x in ("A-301", "S-101", "S-102", "S-103", "G-001")) + "\nSEE A-301")
    assert "A301" in rfi_checks.sheet_index(SET, [both])


def test_a_project_with_no_sheet_numbers_skips_the_check_out_loud():
    findings, notes = dangling_references([page("p1", None)], [chunk("c1", "p1", "SEE 5/S-501")])
    assert findings == []
    assert any("skipped" in n for n in notes)


def test_one_missing_sheet_referenced_everywhere_is_one_finding():
    pages = [page(f"p{i}", f"S-10{i}") for i in range(1, 9)]
    chunks = [chunk(f"c{i}", f"p{i}", "SEE 5/S-501") for i in range(1, 9)] + [index()]
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


def test_a_note_pointing_at_a_schedule_does_not_make_the_page_a_schedule():
    """The client's SR-25: a forming plan carrying stud rails SR-1, SR-2 and
    the note "SR-X DENOTES STUD RAILS. FOR STUD RAIL SCHEDULE AND DETAILS SEE
    SHEET S5.131" (plus a small LEVEL SCHEDULE table) was read as THE stud rail
    schedule, so SR-25 on the next plan was "missing from the Level Schedule".
    The real schedule is on S5.131, which is not in this set — so there is
    nothing to check SR-25 against, and nothing to report."""
    pages = [page("p1", "S2.103"), page("p2", "S2.107")]
    chunks = [
        chunk(
            "plan1", "p1",
            "LEVEL SCHEDULE\nLEVEL 3 28'-0\"\nLEVEL 4 38'-0\"\n"
            "8. SR-X DENOTES STUD RAILS. FOR STUD RAIL SCHEDULE AND DETAILS SEE SHEET S5.131.\nSR-1 SR-2 AT C/4",
            ["SR1", "SR2"],
        ),
        chunk("plan2", "p2", "SR-25 AT D/5", ["SR25"]),
    ]
    findings, notes = unscheduled_marks(pages, chunks)
    assert findings == []
    assert any("no schedule" in n for n in notes)


def test_a_mark_is_checked_only_against_a_schedule_named_for_its_family():
    # Two LEVEL entries do not make a LEVEL SCHEDULE a stud rail schedule.
    pages = [page("p1", "S2.103"), page("p2", "S2.107")]
    chunks = [
        chunk("sched", "p1", "LEVEL SCHEDULE SR-10 SR-11", ["SR10", "SR11"]),
        chunk("plan", "p2", "SR-25 AT D/5", ["SR25"]),
    ]
    assert unscheduled_marks(pages, chunks)[0] == []
    # ...while the stud rail schedule itself still checks it.
    chunks[0] = chunk("sched", "p1", "DECON STUDRAIL SCHEDULE SR-10 SR-11", ["SR10", "SR11"])
    assert [f.facts["mark"] for f in unscheduled_marks(pages, chunks)[0]] == ["SR-25"]  # as printed


def test_a_fastener_type_is_not_a_door_missing_from_the_door_schedule():
    """The client's S8: "TYPE S-8 PAN HEAD STEEL SCREWS" on three UL assembly
    sheets, reported as a mark missing from the door schedule."""
    pages = [page("p1", "A1.06"), page("p2", "A1.08"), page("p9", "A1.24")]
    screws = "ATTACH LEAD BATTEN STRIPS WITH TYPE S-8 PAN HEAD STEEL SCREWS 12 IN. OC"
    chunks = [
        chunk("sched", "p9", "DOOR SCHEDULE S1 S2 S3", ["S1", "S2", "S3"]),
        chunk("a", "p1", screws, ["S8"]),
        chunk("b", "p2", screws, ["S8"]),
    ]
    assert unscheduled_marks(pages, chunks)[0] == []
    # Neither rule alone is relied on: under a schedule that DOES name the S
    # family, the screw is still not a mark.
    chunks[0] = chunk("sched", "p9", "STOREFRONT SCHEDULE S1 S2 S3", ["S1", "S2", "S3"])
    assert unscheduled_marks(pages, chunks)[0] == []


def test_a_secondary_grid_line_is_not_a_mark():
    """The client's full scan: grid line F.7 (indexed as F7, since the
    identifier index strips separators) was proposed as "Mark F7 missing from
    the concrete finish schedule" on four sheets — every quote a grid bubble."""
    pages = [page("sched", "A1.05"), page("p1", "A3.25"), page("p2", "A3.22")]
    chunks = [
        chunk("s", "sched", "FLOOR FINISH SCHEDULE F1 F2 F3", ["F1", "F2", "F3"]),
        chunk("a", "p1", "E F F.7 G.9 H", ["F7"]),
        chunk("b", "p2", "GRID F.7", ["F7"]),
    ]
    assert unscheduled_marks(pages, chunks)[0] == []
    # The same mark written as a mark is still checked.
    chunks[2] = chunk("b", "p2", "FINISH F7 AT CORRIDOR", ["F7"])
    assert [f.facts["mark"] for f in unscheduled_marks(pages, chunks)[0]] == ["F7"]


def jetright(plan_text, plan_ids, *, plan_discipline="structural", plan_page="p67", plan_sheet="S2.01"):
    """JETRIGHT's S1.02: the heading "FOOTING SCHEDULE" is a text block of its
    own, and the rows (F5.0 ... F11.0B) are separate blocks under it."""
    pages = [
        Page("p66", "doc", 66, 66, "S1.02", discipline="structural"),
        Page(plan_page, "doc", 67, 67, plan_sheet, discipline=plan_discipline),
    ]
    heading = Chunk("h", "p66", "FOOTING SCHEDULE\n", {"x": 949, "y": 220, "width": 120, "height": 12})
    rows = Chunk(
        "r", "p66", "F5.0\n2' - 0\"\n6#5 T&B\nF5.0B\nF7.0\n7#6 T&B\nF8.0\nF11.0\nF11.0B\n",
        {"x": 873, "y": 299, "width": 300, "height": 140}, ("F50", "F50B", "F70", "F80", "F110", "F110B"),
    )
    plan = Chunk("plan", plan_page, plan_text, {"x": 10, "y": 10, "width": 50, "height": 50}, tuple(plan_ids))
    return pages, [heading, rows, plan]


def test_a_dotted_footing_mark_missing_from_a_schedule_printed_apart_from_its_heading():
    """Astra's JR-001, which this check could not see: the heading and the rows
    were different blocks, and F6.0 has a dot."""
    pages, chunks = jetright("F5.0 F7.0 F6.0 F8.0 F6.0", ["F50", "F70", "F60", "F80"])
    findings, _ = unscheduled_marks(pages, chunks)
    assert [f.facts["mark"] for f in findings] == ["F6.0"]
    assert "F5.0, F7.0" in findings[0].question, "the schedule is quoted as printed"


def test_a_plain_mark_beside_a_dotted_schedule_is_not_its_dotted_twin():
    """F10 on a plan is not F1.0 in a schedule that writes F5.0, F7.0."""
    pages, chunks = jetright("F5.0 AT 3/B. FINISH F10.", ["F50", "F10"])
    assert unscheduled_marks(pages, chunks)[0] == []


def test_a_schedule_speaks_only_for_its_own_discipline():
    pages, chunks = jetright("F6.0 TYPE", ["F60"], plan_discipline="architectural", plan_sheet="A6.01")
    assert unscheduled_marks(pages, chunks)[0] == []


def test_a_sheet_using_the_letters_for_something_else_is_not_checked():
    """A3.01, the roof plan, labels R4, R7 ... R18 beside a railing schedule of
    R1..R6: eleven 'missing railings' that are not railings."""
    pages = [Page("s", "doc", 55, 55, "A5.32", discipline="architectural"),
             Page("roof", "doc", 43, 43, "A3.01", discipline="architectural")]
    chunks = [
        Chunk("h", "s", "RAILING SCHEDULE", {"x": 752, "y": 78, "width": 100, "height": 12}),
        Chunk("r", "s", "R1 R2 R3 R4 R5 R6", {"x": 186, "y": 124, "width": 300, "height": 200},
              ("R1", "R2", "R3", "R4", "R5", "R6")),
        Chunk("p", "roof", "R4 R7 R8 R9 R11 R13 R18", None, ("R4", "R7", "R8", "R9", "R11", "R13", "R18")),
    ]
    assert unscheduled_marks(pages, chunks)[0] == []
    # One stray mark on a sheet that otherwise uses the schedule is still found.
    chunks[2] = Chunk("p", "roof", "R1 R2 R4 R7", None, ("R1", "R2", "R4", "R7"))
    assert [f.facts["mark"] for f in unscheduled_marks(pages, chunks)[0]] == ["R7"]


def test_an_insulation_rating_is_not_a_mark():
    pages = [Page("s", "doc", 55, 55, "A5.32", discipline="architectural"),
             Page("w", "doc", 70, 70, "A6.02", discipline="architectural")]
    chunks = [
        Chunk("h", "s", "RAILING SCHEDULE R1 R2 R3", None, ("R1", "R2", "R3")),
        Chunk("p", "w", "INSULATION BY PEMB SUPPLIER - R-13 MIN.", None, ("R13",)),
    ]
    assert unscheduled_marks(pages, chunks)[0] == []


def test_a_schedule_title_does_not_run_across_a_line_break():
    assert rfi_checks.schedule_titles("EXPOSED SLAB EDGE: SMOOTH FINISH\nCONCRETE FINISH SCHEDULE") == [
        "CONCRETE FINISH SCHEDULE"
    ]


def test_schedule_titles_are_headings_not_pointers():
    assert rfi_checks.schedule_titles("PILE CAP SCHEDULE") == ["PILE CAP SCHEDULE"]
    assert rfi_checks.schedule_titles("FOR STUD RAIL SCHEDULE AND DETAILS SEE SHEET S5.131") == []
    assert rfi_checks.schedule_titles("REFER TO PILE CAP SCHEDULE ON S5.131") == []
    assert rfi_checks.schedule_titles("COLUMN SCHEDULE SEE S5.1") == []


@pytest.mark.parametrize(
    "title, family, names",
    [
        ("PILE CAP SCHEDULE", "PC", True),
        ("DECON STUDRAIL SCHEDULE", "SR", True),
        ("SHEAR WALL SCHEDULE", "SW", True),
        ("COLUMN SCHEDULE", "C", True),
        ("LEVEL SCHEDULE", "SR", False),
        ("DOOR SCHEDULE", "S", False),
        ("PILE CAP SCHEDULE", "C", True),  # CAP: a cap schedule of C marks is plausible
        ("FOOTING SCHEDULE", "PC", False),
    ],
)
def test_a_schedule_title_names_its_mark_family(title, family, names):
    assert rfi_checks.title_names_family(title, family) is names


# --- open items --------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, confidence",
    [
        ("BEAM SIZE TBD", "high"),
        ("BEAM SIZE T.B.D.", "high"),
        ("ELEVATION TO BE DETERMINED", "high"),
        ("FINISH TBC", "medium"),
        ("ANCHOR EMBED ???", "medium"),
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


@pytest.mark.parametrize(
    "text",
    ["STBD SIDE", "TBDX", "WHAT? YES", "VIFTER", "VERIFY IN FIELD", "DIM V.I.F.",
     "TRAFFIC BEARING (T.B.C.O.)"],  # C-301: a cover rating, not "to be confirmed"
)
def test_look_alikes_are_not_open_items(text):
    assert open_items(text) == []


def test_the_same_note_on_many_sheets_is_one_open_item():
    pages = [page(f"p{i}", f"S-10{i}") for i in range(1, 9)]
    chunks = [chunk(f"c{i}", f"p{i}", "GENERAL NOTES\nTOP OF PILE ELEV TBD") for i in range(1, 9)]
    findings, _ = open_item_notes(pages, chunks)
    assert len(findings) == 1
    assert len(findings[0].evidence) == rfi_checks.MAX_EVIDENCE
    assert findings[0].facts["note"] == "TOP OF PILE ELEV TBD"


@pytest.mark.parametrize(
    "text",
    [
        "ALL DIMENSIONS TO BE CONFIRMED BY CONTRACTOR",
        "CONNECTION TO BE DETERMINED BY FABRICATOR",
        "ANCHOR LAYOUT TBD PER SHOP DRAWINGS",
        "FINAL ELEVATION TO BE CONFIRMED IN FIELD",
    ],
)
def test_an_open_item_handed_to_another_party_is_not_an_rfi(text):
    """An RFI asks the designer what the documents cannot answer. A note that
    hands the item to the contractor, a fabricator or a submittal is their
    work, settled through that channel."""
    findings, notes = open_item_notes([page("p1", "S-101")], [chunk("c1", "p1", text)])
    assert findings == []
    assert any("not a question for the designer" in n for n in notes)


def test_a_designer_open_item_on_the_next_line_is_still_found():
    # The hand-off applies to its own note, not to its neighbour.
    text = "1. DIMENSIONS TO BE CONFIRMED BY CONTRACTOR\n2. BEAM SIZE TBD"
    findings, _ = open_item_notes([page("p1", "S-101")], [chunk("c1", "p1", text)])
    assert [f.facts["note"] for f in findings] == ["2. BEAM SIZE TBD"]


def test_open_items_are_ordered_strongest_first():
    pages = [page("p1", "S-101")]
    chunks = [chunk("c1", "p1", "SLAB EDGE TBC\nBEAM TBD")]
    findings, _ = open_item_notes(pages, chunks)
    assert [f.confidence for f in findings] == ["high", "medium"]


# --- running them ------------------------------------------------------------------


def test_the_cap_keeps_the_strongest_and_says_so(monkeypatch):
    monkeypatch.setattr(rfi_checks, "MAX_FINDINGS_PER_CHECK", 3)
    pages = [page("p1", "S-101")]
    text = "\n".join([f"ITEM {i} TBD" for i in range(5)] + ["SLAB EDGE TBC"])
    findings, notes = open_item_notes(pages, [chunk("c1", "p1", text)])
    assert len(findings) == 3
    assert all(f.confidence == "high" for f in findings)
    assert any("only the first 3" in n for n in notes)


def test_run_all_runs_every_check():
    from rfi_grid import GridSystem

    pages, chunks = mark_project([("p1", "PC4 AT 7/D. SEE 5/S-509. PILE TIP TBD. EF-1 400 CFM", ["PC4"])])
    chunks.append(index(sheets=("S-101", "S-102", "S-501", "S-502", "S-503")))
    chunks.append(chunk("fan", "p2", "EF-1 450 CFM"))
    rows = {"1": 100.0, "2": 330.0, "3": 470.0, "4": 800.0}
    grids = [
        GridSystem(pages[0].id, "blue 27pt", {}, rows),
        GridSystem(pages[0].id, "grey 18pt", {}, {"2": 100.0, "3": 330.0, "4": 470.0, "5": 800.0}),
    ]
    import dataclasses

    pages[0] = dataclasses.replace(pages[0], illegible_pictures=(COARSE,))
    findings, _ = run_all(pages, chunks, grids)
    # The pair checks need sheets the scan lined up (test_geometry_checks).
    assert {f.check_type for f in findings} == set(rfi_checks.CHECK_TYPES) - set(rfi_checks.PAIR_CHECK_TYPES)


def test_run_all_says_when_the_grid_check_could_not_look():
    """No grids read and no grids found are different answers."""
    pages, chunks = mark_project([("p1", "PILE TIP TBD", [])])
    _, notes = run_all(pages, chunks, None)
    assert any("Grid check did not run" in n for n in notes)
    _, notes = run_all(pages, chunks, [])
    assert any("found no grid bubbles" in n for n in notes)


def test_check_types_match_the_labels_the_ui_renders():
    """A check the UI has no label for renders as its raw key."""
    source = SHARED.read_text()
    block = re.search(r"export const RFI_CHECK_LABELS = \{(.*?)\} as const;", source, re.S)
    assert block, "RFI_CHECK_LABELS not found in @cdip/shared"
    keys = set(re.findall(r"^\s*(\w+):", block.group(1), re.M))
    assert keys == set(rfi_checks.CHECK_TYPES) | set(rfi_checks.REVIEW_CHECK_TYPES)


def test_the_column_check_writes_a_labelled_type():
    import rfi_columns

    assert rfi_columns.CHECK_TYPE in rfi_checks.CHECK_TYPES


# --- one tag, two ratings ------------------------------------------------------


def three_sheets(*texts):
    pages = [Page(f"p{i}", "doc", i, i, sheet) for i, sheet in enumerate(("P0.02", "P1.03", "P1.04"), start=1)]
    return pages, [Chunk(f"c{i}", f"p{i}", text, None) for i, text in enumerate(texts, start=1)]


def test_one_tag_with_two_ratings_on_two_sheets_is_found():
    """Astra's JR-002 on JETRIGHT: RTU-3 at 80 MBH on the gas riser and the
    mezzanine piping plan, 100 MBH on the roof piping plan."""
    pages, chunks = three_sheets("UP TO\nRTU-3 \n80MBH", "UP TO RTU-3 80MBH", "RTU-3\n100 MBH\nRTU-2 80MBH")
    findings, _ = rfi_checks.tag_value_conflicts(pages, chunks)
    assert len(findings) == 1
    f = findings[0]
    assert f.facts["tag"] == "RTU-3" and f.confidence == "high"  # two sheets agree, one does not
    assert f.question.startswith("RTU-3 is labelled 80 MBH on P0.02, P1.03; 100 MBH on P1.04.")
    assert {e["sheetNumber"] for e in f.evidence} == {"P0.02", "P1.03", "P1.04"}


def test_agreeing_ratings_and_other_tags_are_not_findings():
    pages, chunks = three_sheets("RTU-3 80MBH", "RTU-3 80 MBH", "RTU-2 100 MBH")
    assert rfi_checks.tag_value_conflicts(pages, chunks)[0] == []


def test_two_values_on_one_sheet_are_two_quantities_not_a_conflict():
    pages = [Page("p1", "doc", 1, 1, "M0.02"), Page("p2", "doc", 2, 2, "P1.04")]
    chunks = [Chunk("a", "p1", "RTU-3 INPUT 80 MBH OUTPUT 64.8 MBH", None), Chunk("b", "p2", "RTU-3 100 MBH", None)]
    # The schedule row carries two MBH values after the tag: neither is taken.
    assert rfi_checks.tag_value_conflicts(pages, chunks)[0] == []


def test_a_fraction_rating_is_compared_by_value():
    pages = [Page("p1", "doc", 1, 1, "M0.02"), Page("p2", "doc", 2, 2, "E0.05")]
    chunks = [Chunk("a", "p1", "CF-2 2 HP", None), Chunk("b", "p2", "CF-2 2-1/2 HP", None)]
    (f,) = rfi_checks.tag_value_conflicts(pages, chunks)[0]
    assert f.confidence == "medium" and "2 HP on M0.02" in f.question and "2-1/2 HP on E0.05" in f.question
    chunks[1] = Chunk("b", "p2", "CF-2 2.0 HP", None)
    assert rfi_checks.tag_value_conflicts(pages, chunks)[0] == []


@pytest.mark.parametrize(
    "text",
    [
        'RTU-3 3/4"ø G',  # a pipe size is not a rating
        "RTU 3 100 MBH",  # no hyphen: not a tag
        "RTU-3.1 100 MBH",  # a sub-tag
        "SEE 1/RTU-3 100 MBH",  # part of a reference
    ],
)
def test_look_alikes_are_not_tag_ratings(text):
    assert [r for r in rfi_checks.tag_ratings(text) if r[0] == "RTU-3" and r[1] == "MBH"] == []


def test_a_reference_must_be_numbered_like_the_sets_sheets_of_its_prefix():
    """JETRIGHT: "MECHANICAL DRAWING E2" on A4.01, in a set whose electrical
    sheets are E2.01, E2.02 — accepted only because the cover sheet is T1."""
    pages = [page("p1", "T1"), page("p2", "E2.01"), page("p3", "E2.02"), page("p4", "A4.01")]
    chunks = [index(sheets=("T1", "E2.01", "E2.02", "A4.01", "A4.02")),
              chunk("c", "p4", "SEE MECHANICAL DRAWING E2 FOR DUCT")]
    assert dangling_references(pages, chunks)[0] == []


# --- a bare open-item cell is named by its table --------------------------------


FIRE_TABLE = (
    "IBC CH 7\n"
    "F I R E    R E S I S T A N C E  -  W A L L S    &    P A R T I T I O N S\n"
    "RATING\nACHIEVED BY\nSHAFT ENCLOSURES\n1 HR\nTBD\nN/A\nFIRE WALLS\n2 HR\nTBD\nN/A\n"
)


def test_a_bare_tbd_cell_is_named_by_its_table_and_counted():
    # G2.01: the "ACHIEVED BY" column of a fire-resistance table reads TBD on
    # every row. "Open item on G2.01: "TBD"" told a reader nothing.
    findings, _ = rfi_checks.open_item_notes([page("p1", "G2.01")], [chunk("c1", "p1", FIRE_TABLE)])
    assert len(findings) == 1
    f = findings[0]
    assert "FIRE RESISTANCE - WALLS & PARTITIONS" in f.subject and "(2 entries)" in f.subject
    assert f.facts["entries"] == 2 and f.facts["table"] == "FIRE RESISTANCE - WALLS & PARTITIONS"
    assert "2 entries reading only \"TBD\"" in f.question


def test_bare_tbd_cells_in_two_tables_are_two_findings():
    other = "DOOR HARDWARE SCHEDULE\nSET\nTBD\n"
    findings, _ = rfi_checks.open_item_notes(
        [page("p1", "G2.01"), page("p2", "A6.01")],
        [chunk("c1", "p1", FIRE_TABLE), chunk("c2", "p2", other)],
    )
    assert sorted(f.facts.get("table") for f in findings) == ["DOOR HARDWARE SCHEDULE", "FIRE RESISTANCE - WALLS & PARTITIONS"]


def test_a_note_with_words_keeps_its_own_wording():
    findings, _ = rfi_checks.open_item_notes([page("p1", "A8.01")], [chunk("c1", "p1", "NOTES\nGROUT COLOR: TBD\n")])
    assert findings[0].subject == 'Open item on A8.01: "GROUT COLOR: TBD"'
    assert "table" not in findings[0].facts


def test_a_bare_cell_with_no_heading_stays_bare():
    findings, _ = rfi_checks.open_item_notes([page("p1", "A8.01")], [chunk("c1", "p1", "1 HR\nTBD\nN/A\n")])
    assert findings[0].subject == 'Open item on A8.01: "TBD"'


def test_evidence_names_the_printed_words_for_pinpointing():
    findings, _ = rfi_checks.open_item_notes([page("p1", "A8.01")], [chunk("c1", "p1", "GROUT COLOR: TBD\n")])
    assert findings[0].evidence[0]["_term"] == "TBD"



# --- OCR-read text and illegible pictures ----------------------------------------

COARSE = {"b": [246.0, 256.0, 1050.0, 750.0], "dpi": 66.6, "lines": 175, "readable": 42, "illegible": True}


def test_a_schedule_picture_too_coarse_to_read_asks_for_a_legible_copy():
    # E0.05: seven panel schedules pasted at ~67 DPI. Nothing is guessed off
    # them; the question is the one a reviewer asks.
    e005 = Page("p9", "doc-1", 94, 94, "E0.05", illegible_pictures=(COARSE, dict(COARSE, b=[275, 1351, 1040, 1861])))
    [f] = rfi_checks.illegible_schedules([e005, page("p1", "S-101")])
    assert f.check_type == "illegible_schedule" and f.confidence == "medium"
    assert f.subject == "E0.05: 2 schedules pasted as pictures too coarse to read"
    assert "about 67 DPI" in f.question and "legible" in f.question
    assert len(f.evidence) == 2 and f.evidence[0]["bbox"] == {"x": 246.0, "y": 256.0, "width": 804.0, "height": 494.0}
    assert "42 of 175" in f.evidence[0]["quote"]
    # One sheet, one finding, stable across scans.
    assert f.fingerprint == rfi_checks.illegible_schedules([e005])[0].fingerprint


def test_a_finding_read_by_ocr_is_capped_and_says_so():
    ocr_chunk = Chunk("c1", "p1", "NOTES\nGROUT COLOR: TBD\n", {"x": 0, "y": 0, "width": 9, "height": 9}, (), ocr=True)
    [f] = rfi_checks.open_item_notes([page("p1", "M0.02")], [ocr_chunk])[0]
    assert f.evidence[0]["source"] == "ocr"
    rfi_checks.mark_ocr(f)
    assert f.confidence == "medium"
    assert f.facts["readByOcr"] == ["M0.02"]
    assert "read by OCR" in f.question


def test_text_layer_findings_are_not_touched_by_the_ocr_rule():
    [f] = rfi_checks.open_item_notes([page("p1", "A8.01")], [chunk("c1", "p1", "GROUT COLOR: TBD\n")])[0]
    rfi_checks.mark_ocr(f)
    assert f.confidence == "high" and "readByOcr" not in f.facts and "source" not in f.evidence[0]


def test_ocr_context_caps_only_where_context_decides():
    # "No row for PC4" rests on the schedule: read by OCR, it may have missed
    # the row, so the finding is capped. A tag conflict's context does not
    # decide it.
    def finding(check):
        return Finding(check, "fp", "high", "s", "q",
                       [{"sheetNumber": "S-101", "role": "finding"},
                        {"sheetNumber": "S-501", "role": "context", "source": "ocr"}])
    mark, tag = finding("unscheduled_mark"), finding("tag_value_conflict")
    rfi_checks.mark_ocr(mark)
    rfi_checks.mark_ocr(tag)
    assert mark.confidence == "medium" and mark.facts["readByOcr"] == ["S-501"]
    assert tag.confidence == "high"


def _ocr(cid, pid, text, identifiers=()):
    return Chunk(cid, pid, text, {"x": 1, "y": 2, "width": 3, "height": 4}, tuple(identifiers), ocr=True)


def test_ocr_text_never_names_a_missing_sheet():
    # JETRIGHT: "FP0.O1" (a letter O for a zero) and "1/16 in" read "l/i6".
    pages = [page("p1", "S-101"), page("p2", "S-102")]
    chunks = [index("p1", sheets=("S-101", "S-102")), _ocr("o1", "p2", "SEE DETAILS ON SHEET S-109")]
    findings, notes = dangling_references(pages, chunks)
    assert findings == []
    assert any("read only by OCR" in n for n in notes)


def test_an_ocr_read_mark_on_a_plan_is_never_unscheduled_but_an_ocr_schedule_suppresses():
    pages = [page("p1", "S-101"), page("p2", "S-102")]
    schedule = chunk("sch", "p1", "PILE CAP SCHEDULE\nPC1\nPC2\nPC3", ["PC1", "PC2", "PC3"])
    misread = _ocr("o1", "p2", "PC8 AT 4/B", ["PC8"])
    assert unscheduled_marks(pages, [schedule, misread])[0] == []
    # A row only OCR could read still counts as scheduled.
    ocr_row = _ocr("o2", "p1", "PC4 24x24", ["PC4"])
    plan = chunk("pl", "p2", "PC4 AT 4/B", ["PC4"])
    assert unscheduled_marks(pages, [schedule, ocr_row, plan])[0] == []
