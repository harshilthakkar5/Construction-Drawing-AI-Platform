"""benchmarks/rfi_eval.py: a matcher that finds "6 = 9" inside "16 = 9.5"
reports a miss as a hit, and the benchmark exists to be believed."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "benchmarks"))

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
        assert case["target"]["type"] in ("sheet", "compare", "scan")
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
    assert {"fp-a303-a305-levels", "fp-s1102-a336-scales", "fp-s8-door-schedule", "fp-sr25-level-schedule"} <= ids
