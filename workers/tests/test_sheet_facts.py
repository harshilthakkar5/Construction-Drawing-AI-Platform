"""Phase 1 of the full AI scan: what each page is. A wrong level or a wrong
kind builds a pair the client has already rejected (Level 4 against Level 6),
so the rules that decide are tested here on the titles the client's own sheets
carry, and a sheet that cannot be read must come out as "unknown", never as a
guess."""

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import fitz  # noqa: E402
import pytest  # noqa: E402

import sheet_facts as sf  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


def test_sheet_kinds_match_the_shared_contract():
    ts = (ROOT / "packages" / "shared" / "src" / "index.ts").read_text()
    block = re.search(r"export const SHEET_KINDS = \[(.*?)\] as const;", ts, re.S).group(1)
    assert tuple(re.findall(r'"([a-z_]+)"', block)) == sf.SHEET_KINDS


@pytest.mark.parametrize(
    "titles, has_grid, kind",
    [
        (["UT LAW STUDENT HOUSING", "S2.105", "Forming Plan - Level 5"], True, "plan"),
        (["BUILDING PLAN - LEVEL 2", "GREYSTAR"], True, "plan"),
        (["CONCRETE EXHIBIT - LEVEL 14"], True, "plan"),
        (["ENLARGED CONCRETE EXHIBIT - LEVEL 14", "ROOF DECK CONCRETE DIAGRAM"], False, "enlarged_plan"),
        (["Elevations", "CIP Concrete Shearwall"], True, "elevation"),
        (["3D Views"], False, "other"),
        (["PILE CAP SCHEDULE"], False, "schedule"),
        (["GENERAL NOTES"], False, "notes"),
        (["SHEET INDEX", "COVER"], False, "cover"),
        (["WALL SECTIONS", "KEY PLAN"], False, "section"),  # a key plan is a locator, not the sheet
        (["TYPICAL DETAILS", "SEE PLAN"], False, "detail"),
        (["UT LAW STUDENT HOUSING"], True, "plan"),  # no title word, but a grid
        (["UT LAW STUDENT HOUSING"], False, "other"),
    ],
)
def test_classify_kind(titles, has_grid, kind):
    assert sf.classify_kind(titles, has_grid) == kind


@pytest.mark.parametrize(
    "titles, level",
    [
        (["Forming Plan - Level 5"], "LEVEL 5"),
        (["Floor Plan -Level 7 to 13"], "LEVEL 7-13"),  # one drawing for floors 7 to 13
        (["ENLARGED CONCRETE EXHIBIT - LEVEL 7-13"], "LEVEL 7-13"),
        (["GARAGE LEVEL 01 FOUNDATION PLAN"], "LEVEL 1"),
        # The client's A3.32: two floors on one sheet — no one level, no pair.
        (["ENLARGED CONCRETE EXHIBIT - LEVEL 7-13", "ENLARGED CONCRETE EXHIBIT - LEVEL 6"], None),
        # A level schedule beside the title does not compete with it.
        (["FORMING PLAN - LEVEL 5", "LEVEL 1", "LEVEL 2", "LEVEL 14"], "LEVEL 5"),
        # A pointer is not a title.
        (["REFER TO LEVEL 5 PLAN FOR SLAB EDGE", "LEVEL 4 BUILDING PLAN"], "LEVEL 4"),
        (["FOUNDATION PLAN"], None),
        # Floors named in words — a client set with 33 plans read only 11 levels
        # and paired nothing, because only "LEVEL n" was understood.
        (["FIRST FLOOR PLAN"], "LEVEL 1"),
        (["SECOND FLOOR FRAMING PLAN"], "LEVEL 2"),
        (["2ND FLOOR PLAN"], "LEVEL 2"),
        (["FLOOR 3 PLAN"], "LEVEL 3"),
        (["SECOND FLOOR PLAN", "LEVEL 2 FRAMING PLAN"], "LEVEL 2"),  # two spellings, one floor
        (["THIRD FLOOR PLAN", "FOURTH FLOOR PLAN"], None),            # two floors on one sheet
        # A named floor pairs only with the same name: GROUND is level 1 in the
        # US and level 0 in the UK, so it is never turned into a number.
        (["GROUND FLOOR PLAN"], "GROUND FLOOR"),
        (["ROOF FRAMING PLAN"], "ROOF"),
        (["BASEMENT 2 PLAN"], "BASEMENT 2"),
        (["MEZZANINE PLAN"], "MEZZANINE"),
        (["LOWER LEVEL PLAN"], "LOWER LEVEL"),
        (["ENLARGED FLOOR PLAN"], None),
        (["ROOF DRAIN DETAILS"], None),  # not a plan title
        (["LEVEL 14"], "LEVEL 14"),  # bare, and nothing titled
    ],
)
def test_title_level(titles, level):
    assert sf.title_level(titles) == level


def test_title_lines_are_the_big_text_relative_to_the_sheet():
    lines = [(31.5, "UT LAW STUDENT HOUSING"), (28.2, "S2.105"), (24.9, "Forming Plan - Level 5"),
             (12.6, "Sheet Title"), (9.6, "TOP OF CONCRETE = SEE PLAN")]
    titles = sf.title_lines(lines, ["S2.105 FORMING PLAN"])
    assert "Forming Plan - Level 5" in titles and "Sheet Title" not in titles
    assert "S2.105 FORMING PLAN" in titles  # the user's title-block region counts too


def test_a_detail_number_or_logo_never_sets_the_title_size():
    """A client's A1.01, sizes as measured: the sheet number at 64.5, the
    detail number "01" in its bubble at 51.6, and the drawing title at 25.5.
    With "01" as the reference, 55% of it (28.4) left the title out, and every
    architectural plan of the set read as "other"."""
    lines = [(64.5, "A1.01"), (51.6, "01"), (25.5, "FLOOR PLAN - LEVEL 1 OVERALL"),
             (19.5, "HANGAR 15"), (9.0, "GENERAL NOTE TEXT")]
    titles = sf.title_lines(lines)
    assert "FLOOR PLAN - LEVEL 1 OVERALL" in titles
    assert sf.classify_kind(titles, has_grid=False) == "plan"
    assert sf.title_level(titles) == "LEVEL 1"


def test_a_label_bubbled_twice_far_apart_is_several_views():
    one = {"A": [(100.0, 50.0), (101.0, 1600.0)]}  # both ends of one line
    two = {"A": [(100.0, 50.0), (900.0, 50.0)]}  # two details side by side
    assert not sf.repeats_a_label({"A": 100.0}, {}, one)
    assert sf.repeats_a_label({"A": 100.0}, {}, two)


def _drawn_plan(title: str, small_title: str | None = None) -> fitz.Page:
    doc = fitz.open()
    page = doc.new_page(width=2592, height=1728)
    page.insert_text((2300, 1650), "S2.105", fontsize=28)
    page.insert_text((2000, 1600), title, fontsize=25)
    if small_title:
        page.insert_text((300, 1500), small_title, fontsize=12)
    page.insert_text((300, 1530), '1/8" = 1\'-0"', fontsize=10)
    page.insert_text((300, 1560), "PROJECT NAME", fontsize=31)
    return page


def test_read_page_on_a_drawn_sheet():
    facts = sf.read_page(_drawn_plan("Foundation Plan - Level", "Foundation Plan - Level 1"))
    assert facts["level"] == "LEVEL 1"  # the view title, since the big one breaks
    assert facts["scales"] == [9.0]
    assert facts["kind"] == "plan"


SET = os.environ.get("RFI_REVIEW_TEST_PDF")


@pytest.mark.skipif(not SET or not os.path.exists(SET), reason="set RFI_REVIEW_TEST_PDF to the client's S2.105/A3.01 set")
def test_the_client_sheets_read_as_their_titles_say():
    doc = fitz.open(SET)
    s2105, a301 = sf.read_page(doc[0]), sf.read_page(doc[1])
    assert (s2105["kind"], s2105["level"], s2105["scales"]) == ("plan", "LEVEL 5", [9.0])
    assert (a301["kind"], a301["level"], a301["scales"]) == ("plan", "LEVEL 2", [9.0])
    assert len(s2105["grid"]["x"]) >= 6 and not s2105["grid"]["repeats"]
