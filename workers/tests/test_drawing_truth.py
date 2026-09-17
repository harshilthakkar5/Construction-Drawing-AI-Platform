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


class TestWhoseLabelsACropContains:
    """The half `--against` never asked.

    "Is my label inside my crop?" and "is anyone ELSE's label inside my crop?"
    are different questions with different consequences, and a sheet can pass
    the first completely while failing the second everywhere. The second is the
    whole point of cropping: a crop exists to stop a model answering 2/C with
    row F's member size, which is exactly what the run before this did at 2/C,
    4.6/C and 7/C.
    """

    class _Rect:
        def __init__(self, x0, y0, x1, y1):
            self.x0, self.y0, self.x1, self.y1 = x0, y0, x1, y1

    @staticmethod
    def _grid(gap, half, reach):
        """Two intersections `gap` apart, crops of half-width `half`, labels
        sitting `reach` from their own intersection."""
        rects = {
            "1/A": TestWhoseLabelsACropContains._Rect(-half, -half, half, half),
            "2/A": TestWhoseLabelsACropContains._Rect(gap - half, -half, gap + half, half),
        }
        by_label = {
            label: [{"derivation": {"labelDistancePt": reach}}] for label in rects
        }
        return rects, by_label

    def test_a_label_short_of_the_neighbours_edge_cannot_reach_it(self):
        rects, _ = self._grid(gap=400, half=100, reach=50)
        # The neighbour's crop starts 300pt away; a 50pt label cannot get there.
        assert drawing_truth._reach_to((0, 0), rects["2/A"]) == 300
        assert 50 < 300

    def test_a_label_further_out_than_the_gap_to_the_neighbour_can(self):
        """The real sheet: rows C and F are 137.2pt apart with a 222.8pt crop,
        so F's crop starts 25.8pt from C's intersection and every label is 47pt
        or more out. Nothing about the size can fix that."""
        rects, _ = self._grid(gap=137.2, half=111.4, reach=83.7)
        need = drawing_truth._reach_to((0, 0), rects["2/A"])
        assert round(need, 1) == 25.8
        assert 83.7 >= need

    def test_it_reports_rather_than_fails(self, capsys):
        """On this sheet the condition is unsatisfiable — a crop must hold its
        OWN furthest label — so failing would block a run that is as good as the
        geometry allows. The number is what says how much weight the prompt's
        neighbour rule is carrying."""
        rects, by_label = self._grid(gap=137.2, half=111.4, reach=83.7)
        drawing_truth._report_crop_overlap([list(rects.items())], by_label)
        said = capsys.readouterr().err
        assert "can reach a NEIGHBOURING crop" in said
        assert "VLM_CROP_BAYS cannot fix it" in said
        assert "25.8pt away" in said

    def test_a_sheet_whose_crops_are_clean_says_so(self, capsys):
        rects, by_label = self._grid(gap=400, half=100, reach=50)
        drawing_truth._report_crop_overlap([list(rects.items())], by_label)
        assert "No crop can contain another intersection's label." in capsys.readouterr().err

    def test_the_checked_in_set_is_measured_not_assumed(self, capsys):
        """Against the real sheet's own coordinates, read out of the set. If
        this ever reports clean, the crop geometry changed and the prediction
        written into CLAUDE.md for the crop run no longer applies."""
        cases = json.loads(
            (Path(__file__).resolve().parents[2] / "benchmarks" / "drawing_eval_set.json").read_text()
        )
        columns, rows = {}, {}
        for case in cases:
            d = case["derivation"]
            columns[d["gridColumn"]] = d["intersectionPt"][0]
            rows[d["gridRow"]] = d["intersectionPt"][1]

        def median(values):
            gaps = sorted(b - a for a, b in zip(sorted(values), sorted(values)[1:]))
            mid = len(gaps) // 2
            return gaps[mid] if len(gaps) % 2 else (gaps[mid - 1] + gaps[mid]) / 2

        half_x, half_y = median(columns.values()) * 0.6, median(rows.values()) * 0.6
        rects, by_label = {}, {}
        for col, x in columns.items():
            for row, y in rows.items():
                label = f"{col}/{row}"
                rects[label] = self._Rect(x - half_x, y - half_y, x + half_x, y + half_y)
        for case in cases:
            d = case["derivation"]
            by_label.setdefault(f"{d['gridColumn']}/{d['gridRow']}", []).append(case)
        drawing_truth._report_crop_overlap([list(rects.items())], by_label)
        said = capsys.readouterr().err
        assert f"{len(by_label)} of {len(by_label)} intersections" in said, (
            "every intersection on this sheet can reach a neighbour: labels reach 83.7pt "
            "and the tightest gaps are 129.9 and 137.2pt"
        )
        assert "25.8pt away" in said, "rows C and F are the tightest pair"

    def test_the_neighbour_reported_is_the_nearest_one_not_the_first_found(self, capsys):
        """Which neighbour a crop reaches first is what decides the tightest
        pair, and the tightest pair is the confusion to look for in the run. The
        far one is inserted first here, so taking whichever turns up first gets
        it wrong while still reporting something that looks right."""
        half = 111.4
        rects = {
            "3/A": self._Rect(400 - half, -half, 400 + half, half),  # far, seen first
            "1/A": self._Rect(-half, -half, half, half),
            "2/A": self._Rect(137.2 - half, -half, 137.2 + half, half),  # near
        }
        by_label = {"1/A": [{"derivation": {"labelDistancePt": 300.0}}]}
        drawing_truth._report_crop_overlap([list(rects.items())], by_label)
        said = capsys.readouterr().err
        assert "2/A's crop starts 25.8pt away" in said
        assert "3/A" not in said
