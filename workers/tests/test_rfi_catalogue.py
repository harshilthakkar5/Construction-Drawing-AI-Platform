"""The worker's generated copy of the review catalogue against the golden
transcription of the 16 original questions, and the numeric-thinking-budget
capability against the fixture the API dialog reads."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402

import llm  # noqa: E402
from generated import RFI_REVIEW_CATALOGUE_VERSION, RFI_REVIEW_CHECKS, RFI_REVIEW_DEPTHS  # noqa: E402

FIXTURES = Path(__file__).resolve().parents[2] / "packages" / "shared" / "fixtures"


def test_the_generated_catalogue_is_the_16_original_questions_verbatim():
    golden = json.loads((FIXTURES / "rfi-original-questions.json").read_text())["questions"]
    assert len(golden) == 16
    assert [c["id"] for c in RFI_REVIEW_CHECKS] == [q["id"] for q in golden]
    for check, q in zip(RFI_REVIEW_CHECKS, golden):
        assert check["originalQuestion"] == q["question"]
        assert (check["sourceSection"], check["sourceQuestionNumber"]) == (q["section"], q["number"])
    assert RFI_REVIEW_CATALOGUE_VERSION


def test_every_depth_carries_the_caps_the_worker_reads():
    for depth in RFI_REVIEW_DEPTHS.values():
        for key in ("chunks", "visualPages", "cropsPerPage", "pairWindows", "maxBatches", "maxTotalTokens"):
            assert depth[key] > 0


CASES = json.loads((FIXTURES / "thinking-capability.json").read_text())["cases"]


@pytest.mark.parametrize("case", CASES, ids=[c["model"] for c in CASES])
def test_the_transport_agrees_which_models_take_a_numeric_budget(case):
    if case["provider"] == "claude":
        takes_budget = not llm._claude_takes_effort(case["model"])
    else:
        takes_budget = not llm._takes_thinking_level(case["model"])
    assert takes_budget is case["budget"]
