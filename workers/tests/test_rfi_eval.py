"""benchmarks/rfi_eval.py: a matcher that finds "6 = 9" inside "16 = 9.5"
reports a miss as a hit, and the benchmark exists to be believed."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "benchmarks"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import rfi_eval  # noqa: E402

EXP = {"what": "grid", "checkTypes": ["grid_mismatch", "G01"], "sheets": ["S2.105", "A3.01"], "pairs": [["6", "9"]]}


def cand(question: str, sheets=("S2.105", "A3.01"), check="grid_mismatch") -> dict:
    return {"id": question, "checkType": check, "subject": "Grid", "question": question,
            "evidence": [{"sheetNumber": s} for s in sheets]}


def test_a_pair_matches_in_either_order():
    assert rfi_eval.matches(EXP, cand("(S2.105 = A3.01): 6 = 9; 4 = 6")) == []
    assert rfi_eval.matches(EXP, cand("(A3.01 = S2.105): 9 = 6")) == []


def test_a_pair_inside_another_number_is_not_a_match():
    assert rfi_eval.matches(EXP, cand("16 = 9.5; 6 = 99")) == ["does not say 6 = 9"]


def test_a_missing_sheet_or_wrong_check_is_named():
    assert rfi_eval.matches(EXP, cand("6 = 9", sheets=("S2-105",))) == ["no evidence on A3.01"]
    assert "check C01" in rfi_eval.matches(EXP, cand("6 = 9", check="C01"))[0]


def test_unmatched_candidates_are_listed_not_scored():
    case = {"id": "x", "expected": [EXP]}
    result = rfi_eval.score(case, [cand("6 = 9"), cand("nothing", check="C01")])
    assert result["hits"] == 1 and result["unmatched"] == ["Grid"]


def test_the_checked_in_cases_are_well_formed():
    data = json.loads(rfi_eval.CASES.read_text())
    for case in data["cases"]:
        # A case expects something, or names something it must NOT raise.
        assert case["expected"] or case.get("rejected")
        assert case["target"]["type"] in ("sheet", "compare", "scan", "full_scan")
        for exp in case["expected"]:
            assert exp["checkTypes"] and all(len(p) == 2 for p in exp.get("pairs", []))
        for rej in case.get("rejected", []):
            assert rej["checkTypes"] and rej["why"] and (rej.get("sheets") or rej.get("mentions"))


REJ = {"what": "S8", "why": "a screw", "checkTypes": ["unscheduled_mark"], "sheets": [], "mentions": ["S8"]}


def test_a_finding_a_person_rejected_is_a_false_positive_when_raised_again():
    case = {"id": "fp", "expected": [], "rejected": [REJ]}
    raised = cand("Mark S-8 is shown on A1.06 but the DOOR SCHEDULE lists S1, S2", sheets=("A1.06",), check="unscheduled_mark")
    result = rfi_eval.score(case, [raised])
    assert [f["what"] for f in result["falsePositives"]] == ["S8"]
    assert result["unmatched"] == []


def test_a_rejection_does_not_swallow_a_different_finding():
    case = {"id": "fp", "expected": [], "rejected": [REJ]}
    other = cand("Mark S18 is shown on A1.06", sheets=("A1.06",), check="unscheduled_mark")
    wrong_check = cand("S8 open item", check="open_item_note")
    result = rfi_eval.score(case, [other, wrong_check])
    assert result["falsePositives"] == [] and len(result["unmatched"]) == 2


def test_the_four_client_rejections_are_in_the_benchmark():
    ids = {c["id"] for c in json.loads(rfi_eval.CASES.read_text())["cases"]}
    assert {"fp-a303-a305-levels", "fp-s1102-a336-scales", "fp-s8-door-schedule", "fp-sr25-level-schedule",
            "fp-not-uploaded-references", "fp-full-scan-project-checks", "fp-full-scan-ai"} <= ids


def test_the_full_scans_wrong_findings_fail_a_run_that_raises_them_again():
    """As the packages worded them, every proven false positive of the first
    real full scan is caught — so the benchmark cannot pass a repeat."""
    cases = {c["id"]: c for c in json.loads(rfi_eval.CASES.read_text())["cases"]}
    raised = [
        cand("Sheet A3.34 shows the dimension between grid line 3.7 and grid line 3.5 as 6'-1\", while sheet A3.13 "
             "shows this dimension as 5'-10\".", sheets=("A3.34", "A3.13"), check="C01"),
        cand("Sheet A3.34 indicates a dimension of 10'-1\" between grid lines 2 and 2.3, while Sheet A3.13 shows "
             "this dimension as 10'-0\".", sheets=("A3.34", "A3.13"), check="G02"),
        cand("Sheet A3.05 shows partition wall tag W1-14 at the demising wall, whereas Sheet S2.106 shows column "
             "C-5 (14 x 30) at this location.", sheets=("A3.05", "S2.106"), check="C01"),
        cand("Sheet A3.22 shows a round column, whereas Sheet S2.402 shows a square column outline.",
             sheets=("A3.22", "S2.402"), check="C01"),
    ]
    assert len(rfi_eval.score(cases["fp-full-scan-ai"], raised)["falsePositives"]) == 4
    scan = [
        cand("Drawings A3.25, A3.33 show mark F7, but the SMOOTH FINISH CONCRETE FINISH SCHEDULE does not list it.",
             sheets=("A3.25", "A3.33"), check="unscheduled_mark"),
        cand("3 of them are named differently: numbered lines 2.3 = 2.4; 1.4 = 1.5. lettered lines G.9 = H.",
             sheets=("A3.25", "S2.106"), check="grid_mismatch"),
    ]
    assert len(rfi_eval.score(cases["fp-full-scan-project-checks"], scan)["falsePositives"]) == 2


def test_a_full_scan_is_scored_by_its_own_column():
    assert rfi_eval.source_column(full_scan_id="f") == ('"fullScanId"', "f")
    assert rfi_eval.source_column(run_id="r") == ('"reviewRunId"', "r")
    assert rfi_eval.source_column(scan_id="s") == ('"scanId"', "s")
    with pytest.raises(ValueError):
        rfi_eval.source_column(run_id="r", full_scan_id="f")
    with pytest.raises(ValueError):
        rfi_eval.source_column()


def test_a_full_scan_finding_uses_the_catalogue_ids_the_cases_accept():
    """The full scan files a finding under the closest original question
    (C01, G01 …), never a check name. Every expected finding a full scan could
    produce must therefore accept a catalogue id, or a correct finding would
    score as a miss."""
    from generated import RFI_REVIEW_CHECKS

    ids = {c["id"] for c in RFI_REVIEW_CHECKS}
    cases = json.loads(rfi_eval.CASES.read_text())["cases"]
    for case in cases:
        for exp in case["expected"]:
            if set(exp["checkTypes"]) & {"grid_mismatch", "column_mismatch"}:
                assert set(exp["checkTypes"]) & ids, case["id"]


def test_a_full_scan_candidate_scores_against_rfi_002():
    candidate = {
        "id": "c1",
        "checkType": "G01",
        "subject": "Grid naming differs between S2.105 and A3.01",
        "question": "S2.105 shows grid 6 = 9 on A3.01. Which naming governs?",
        "evidence": [{"sheetNumber": "S2.105"}, {"sheetNumber": "A3.01"}],
    }
    case = next(c for c in json.loads(rfi_eval.CASES.read_text())["cases"] if c["id"] == "rfi-002")
    assert rfi_eval.score(case, [candidate])["hits"] == 1


def test_a_by_element_finding_counts_under_the_cases_elements():
    exp = {"what": "pit slab", "checkTypes": ["F03"], "sheets": ["S4.00"], "mentions": ["pit"]}
    c = {"id": "1", "checkType": "CE-05-01-Q03", "subject": "Elevator pit slab", "question": "?",
         "evidence": [{"sheetNumber": "S4.00"}]}
    assert rfi_eval.matches(exp, c) == ["check CE-05-01-Q03 is not F03"]
    assert rfi_eval.matches(exp, c, ("CE-05-01",)) == []
    assert rfi_eval.matches(exp, c, ("CE-05-0",)) != []  # a prefix of another element is not it
    # A project's own question counts when asked in a review of the case's element.
    own = dict(c, checkType="custom", element="CE-05-01")
    assert rfi_eval.matches(exp, own, ("CE-05-01",)) == []
    assert rfi_eval.matches(exp, dict(own, element="CE-04-12"), ("CE-05-01",)) != []
    assert rfi_eval.matches(exp, dict(own, element=None), ("CE-05-01",)) != []


def test_project_reviews_take_each_elements_latest_finished_run_and_tally_the_guards():
    rows = [
        ("old", "CE-05-01", "ready", ["Verification kept 1 of 2 proposed issue(s)."], {}, 1),
        ("new", "CE-05-01", "partial", ["Verification kept 0 of 3 proposed issue(s).",
                                        "Kept CE-05-01-Q03 pit: verification said another drawing gives it, and could not show where."],
         {"CE-05-01-Q03": {"outcome": "complete_no_issue"}, "CE-05-01-Q01": {"outcome": "insufficient_evidence"}}, 2),
        ("failed", "CE-05-01", "failed", [], {}, 3),
        ("other", "CE-03-01", "ready", [], {}, 1),
    ]
    runs = rfi_eval.element_runs(rows)
    assert runs["CE-05-01"]["id"] == "new" and set(runs) == {"CE-05-01", "CE-03-01"}
    assert rfi_eval.guard_tally(runs) == {"proposed": 3, "kept": 0, "rejected": 3, "overruled": 1}
    case = {"elements": ["CE-05-01", "CE-04-12"]}
    assert rfi_eval.missed_with_no_issue(case, runs) == ["CE-05-01: 1 question(s) said no issue (CE-05-01-Q03)"]


def test_every_leland_case_names_library_elements_that_exist():
    lib = json.loads((Path(__file__).resolve().parents[2] / "packages/shared/data/rfi-library.json").read_text())
    active = {e["id"] for e in lib["elements"] if e["active"]}
    cases = [c for c in json.loads(rfi_eval.CASES.read_text())["cases"] if c["id"].startswith("leland-rfi-")]
    assert len(cases) == 9
    for c in cases:
        assert c.get("elements") and set(c["elements"]) <= active, c["id"]


def test_rfi_006_is_scored_as_a_finding_the_drawings_answer():
    """S8.02 schedules every column RFI 006 clouds, so raising it is wrong on this set."""
    case = next(c for c in json.loads(rfi_eval.CASES.read_text())["cases"] if c["id"] == "leland-rfi-006")
    raised = {"id": "1", "checkType": "CE-07-01-Q02", "subject": "Column size at A5",
              "question": "Columns on grid A5 have no size in the column schedule. Provide sizes.",
              "evidence": [{"sheetNumber": "S2.30-B"}]}
    result = rfi_eval.score(case, [raised], tuple(case["elements"]))
    assert result["expected"] == 0 and len(result["falsePositives"]) == 1
    assert rfi_eval.score(case, [dict(raised, checkType="CE-04-12-Q01")], tuple(case["elements"]))["falsePositives"] == []


def test_code_only_keeps_what_a_code_check_made():
    types = rfi_eval.code_check_types()
    assert {"unsized_column", "grid_mismatch", "open_item_note"} <= types
    found = [{"checkType": "unsized_column"}, {"checkType": "CE-07-01-Q01"}, {"checkType": "C01"},
             {"checkType": "custom"}, {"checkType": "chat"}, {"checkType": "grid_mismatch"}]
    assert [c["checkType"] for c in rfi_eval.code_only(found, types)] == ["unsized_column", "grid_mismatch"]
