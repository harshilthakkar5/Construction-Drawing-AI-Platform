"""Settings → Project scope in the review: an RFI about work the contractor does not build is never raised."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import rfi_review  # noqa: E402
from generated import RFI_SCOPE_TOPICS  # noqa: E402


def test_a_problem_about_rebar_is_out_of_scope_and_one_that_only_mentions_it_is_not():
    out = ["rebar"]
    about = lambda element, issue: rfi_review.out_of_scope_topic({"element": element, "issue": issue}, out)  # noqa: E731
    # The client's case: a beam schedule with no bars.
    assert about("Beam B1", "The beam schedule gives no bottom bars or stirrups for B1.") == "Rebar detailing"
    assert about("Grade beam GB-2", "Top and bottom reinforcement is not specified.") == "Rebar detailing"
    # In scope: the size and place of reinforced concrete, and a dowel named after the first sentence.
    assert about("Reinforced concrete wall", "Wall thickness is not given.") is None
    assert about("Retaining wall T.O.C.", "T.O.C. is SEE PLAN and the plan gives none. Dowels per schedule.") is None
    assert about("Column C-30", "Column C-30 is not located to the grid.") is None
    # Nothing held out of scope: nothing is.
    assert rfi_review.out_of_scope_topic({"element": "Beam B1", "issue": "No bottom bars."}, []) is None


def test_the_prompts_name_the_scope_only_when_a_project_sets_one():
    assert "<out_of_scope>" in rfi_review.reasoning_system(["C01"], None, ["rebar"])
    assert "<out_of_scope>" in rfi_review.verification_system(["rebar"])
    assert "<out_of_scope>" not in rfi_review.reasoning_system(["C01"]) + rfi_review.verification_system()
    assert "Rebar detailing" in rfi_review.out_of_scope_block(["rebar"])


def test_the_topics_are_the_shared_ones():
    assert [t["id"] for t in RFI_SCOPE_TOPICS] == ["rebar", "interiors"]
    assert RFI_SCOPE_TOPICS[0]["categories"] == ["Reinforcement"]


def test_a_casework_sheet_is_out_of_scope_by_its_title_and_a_concrete_sheet_never_is():
    rows = [("d", 1, "INTERIOR ELEVATIONS & DETAILS"),  # Victaulic A5.1
            ("d", 2, "MISC. INTERIOR DETAILS - MILLWORK"),
            ("d", 3, "FOUNDATION PLAN AREA 'A'"),
            ("d", 4, "RATED ASSEMBLY MATRIX & FINISH SCHEDULE"),  # slab fire ratings live here
            ("d", 5, "MISC. INTERIOR DETAILS - ROLL-IN SHOWER"),  # a slab recess may live here
            ("d", 6, "INTERIOR WALL SECTIONS")]
    assert set(rfi_review.sheets_out_of_scope(rows, ["interiors"])) == {("d", 1), ("d", 2)}
    assert rfi_review.sheets_out_of_scope(rows, ["rebar"]) == {}  # rebar names no sheets


def test_a_code_finding_touching_a_casework_sheet_is_left_out():
    from rfi_checks import Finding
    ev = lambda page: {"documentId": "d", "pageNumber": page}  # noqa: E731
    on_casework = Finding(check_type="missing_detail", fingerprint="a", confidence="medium", subject="1A/A5.1",
                          question="?", evidence=[ev(7), ev(1)], facts={})
    structural = Finding(check_type="missing_detail", fingerprint="b", confidence="medium", subject="24/S4.02",
                         question="?", evidence=[ev(3)], facts={})
    kept, left = rfi_review.without_out_of_scope_sheets([on_casework, structural], {("d", 1): "Interior"})
    assert kept == [structural] and left == 1
    assert rfi_review.without_out_of_scope_sheets([on_casework], {}) == ([on_casework], 0)


def test_the_ai_is_told_which_sheets_were_read_from_pictures():
    scope = {"pages": [{"sheetNumber": "S1.1A", "drawnText": True}, {"sheetNumber": "A1.5"}]}
    block = rfi_review.drawn_text_block(scope)
    assert "S1.1A" in block and "A1.5" not in block and "Never claim a symbol" in block
    assert rfi_review.drawn_text_block({"pages": [{"sheetNumber": "A1.5"}]}) == ""


def test_a_kept_placement_question_never_says_a_symbol_is_missing():
    """The Victaulic RFI 003 package said "No step symbol … could be found"; S1.1A draws it (note 13)."""
    steps = {"checkId": "CE-04-05-Q01", "kind": "missing", "element": "Wall footing steps",
             "location": "Foundation plans S1.1A, S1.1B, S1.1C",
             "issue": "No step symbol or grid dimension for any step location could be found on the foundation plans.",
             "why": ""}
    reject = {"decision": "reject", "basis": "answered_elsewhere", "reason": "Note 13 sends the step to detail 8/S3.1.",
              "question": "What step height does 8/S3.1 require?"}
    q = rfi_review.kept_for_placement(steps, reject)["question"]
    assert "symbol" not in q and "could be found" not in q
    assert q.startswith("Wall footing steps are not dimensioned from the grid lines. Please locate each one from the grids.")
    assert "S1.1A" in q
