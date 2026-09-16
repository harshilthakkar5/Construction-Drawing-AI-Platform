"""The eval generator's pure parts.

Deriving a case needs a PDF, so `build` is not tested here. The label-shape
table and the backfill are pure, and they carry the contract that matters
across languages: the SHAPE of a mark is defined once, in Python, and travels
with the cases. A set without it still runs — and the scorer then cannot tell a
label the model INVENTED from a refusal to answer, so both land in "abstained",
filing the most dangerous outcome as the safest.
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "benchmarks"))

import drawing_truth  # noqa: E402

SET = Path(__file__).resolve().parents[2] / "benchmarks" / "drawing_eval_set.json"


def _matches(pattern: str, text: str) -> bool:
    """The scorer's own wrapper, so a pattern is tested the way it is used."""
    return bool(re.search(rf"(?<![a-z0-9])(?:{pattern})(?![a-z0-9])", text, re.I))


class TestLabelPattern:
    def test_footing_marks_this_sheet_actually_uses(self):
        pattern = drawing_truth.LABEL_PATTERN["grid-footing"]
        for mark in ("F6", "F7", "F8", "F9", "F10", "F11", "F12", "F13"):
            assert _matches(pattern, f"the footing there is {mark}."), mark

    def test_member_sizes_including_the_spacing_a_model_invents(self):
        pattern = drawing_truth.LABEL_PATTERN["grid-column"]
        for size in (
            "HSS8X8X3/8",
            "HSS 8x8x3/8",  # a model writes it spaced and lowercase
            "HSS10X10X1/2",
            "HSS6.875X0.375",
            "HSS5X5X3/8",
            "HSS7X5X1/2",
        ):
            assert _matches(pattern, f"column {size} sits on it"), size

    def test_it_catches_a_size_that_is_on_no_drawing(self):
        """The whole reason the field exists. HSS9X9X3/8 is a real AISC shape
        and appears nowhere on this sheet; a description of it answered nearly
        every column question with that size and all 21 scored "abstained"."""
        pattern = drawing_truth.LABEL_PATTERN["grid-column"]
        for invented in ("HSS9X9X3/8", "HSS8X9X1/2", "HSS8X8X1/8"):
            assert _matches(pattern, f"the column is {invented}"), invented

    def test_prose_is_not_a_label(self):
        footing = drawing_truth.LABEL_PATTERN["grid-footing"]
        for text in ("the drawing shows nothing there", "see sheet S-301.0"):
            assert not _matches(footing, text), text


class TestBackfill:
    def test_it_fills_every_case_from_its_tag(self):
        cases = [{"tag": "grid-footing"}, {"tag": "grid-column"}]
        assert drawing_truth.backfill_label_patterns(cases) == 2
        assert cases[0]["labelPattern"] == drawing_truth.LABEL_PATTERN["grid-footing"]
        assert cases[1]["labelPattern"] == drawing_truth.LABEL_PATTERN["grid-column"]

    def test_it_leaves_an_existing_pattern_alone(self):
        cases = [{"tag": "grid-footing", "labelPattern": "custom"}]
        assert drawing_truth.backfill_label_patterns(cases) == 0
        assert cases[0]["labelPattern"] == "custom"

    def test_an_unknown_tag_is_skipped_rather_than_guessed(self):
        """A tag with no shape defined for it must stay blind rather than be
        given some other tag's pattern — the report says in as many words that
        it cannot score invented labels, and that has to keep being true."""
        cases = [{"tag": "grid-elevation"}]
        assert drawing_truth.backfill_label_patterns(cases) == 0
        assert "labelPattern" not in cases[0]

    def test_it_does_not_touch_anything_else(self):
        case = {"tag": "grid-footing", "expected": "F12", "distractor": "F13"}
        drawing_truth.backfill_label_patterns([case])
        assert case["expected"] == "F12" and case["distractor"] == "F13"


class TestTheCheckedInSet:
    def test_every_case_can_score_an_invented_label(self):
        """Both recent reports printed "40/40 cases carry no labelPattern".
        This is the line that stops that coming back."""
        cases = json.loads(SET.read_text())
        blind = [c for c in cases if not c.get("labelPattern")]
        assert not blind, f"{len(blind)} of {len(cases)} cases cannot score an invented label"

    def test_each_pattern_matches_that_case_s_own_answers(self):
        """A pattern that does not match the truth it ships with would score a
        correct answer as invented."""
        for case in json.loads(SET.read_text()):
            for label in (case["expected"], case.get("distractor")):
                if label:
                    assert _matches(case["labelPattern"], label), (case["tag"], label)
