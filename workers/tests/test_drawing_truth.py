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
import fitz
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "benchmarks"))

import drawing_truth  # noqa: E402

BENCHMARKS = Path(__file__).resolve().parents[2] / "benchmarks"
SET = BENCHMARKS / "drawing_eval_set.json"
# EVERY checked-in set, not just the first one. A second sheet means a second
# file, and a validation that names one file leaves the other unchecked while
# still reading as "the set is validated".
SETS = sorted(BENCHMARKS.glob("drawing_eval_set*.json"))


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


@pytest.mark.parametrize("path", SETS, ids=lambda p: p.name)
class TestTheCheckedInSet:
    def test_every_case_can_score_an_invented_label(self, path):
        """Both recent reports printed "40/40 cases carry no labelPattern".
        This is the line that stops that coming back."""
        cases = json.loads(path.read_text())
        blind = [c for c in cases if not c.get("labelPattern")]
        assert not blind, f"{len(blind)} of {len(cases)} cases cannot score an invented label"

    def test_each_pattern_matches_that_case_s_own_answers(self, path):
        """A pattern that does not match the truth it ships with would score a
        correct answer as invented."""
        for case in json.loads(path.read_text()):
            for label in (case["expected"], case.get("distractor")):
                if label:
                    assert _matches(case["labelPattern"], label), (case["tag"], label)

    def test_one_project_per_set(self, path):
        """`drawing_eval.mjs` refuses a set naming two projects, because recall
        or accuracy across two corpora is one number describing neither. A set
        that mixes them fails at the run rather than here, after the ingest."""
        ids = {c.get("projectId", "") for c in json.loads(path.read_text())}
        assert len(ids) == 1, f"{path.name} names {len(ids)} projects: {sorted(ids)}"

    def test_no_case_answers_with_its_own_intersection(self, path):
        """The shorthand collision, asserted on the shipped file. A case whose
        expected mark is also its intersection's name scores CORRECT for a
        model that read the question and never looked at the sheet."""
        for case in json.loads(path.read_text()):
            d = case.get("derivation", {})
            col, row = d.get("gridColumn"), d.get("gridRow")
            if col is None or row is None:
                continue
            assert not drawing_truth.names_its_own_intersection(
                case["expected"], col, row
            ), (path.name, case["expected"], f"{col}/{row}")


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


class TestTheSpacingTag:
    """A bay dimension is the one question a crop description cannot hold.

    The crop pass writes a grid header and one line per intersection. What lies
    BETWEEN two grid lines is not in it at all, and it is not in the text layer
    either — not as a fact. The number is there; which gap owns it is not.
    """

    # (text, x, y) in display coordinates, as dimensions_of returns them.
    CHAIN = [
        ("26' - 2 1/2\"", 209.0, 40.0),
        ("5' - 3 1/2\"", 383.0, 40.0),
        ("18' - 0\"", 520.0, 40.0),
    ]
    COLUMNS = {"7": 100.0, "8": 318.0, "9": 448.0, "10": 592.0}

    def test_the_dimension_inside_a_gap_is_the_answer(self):
        value, reason = drawing_truth.dimension_between(self.CHAIN, 100.0, 318.0, 0)
        assert value == "26' - 2 1/2\""
        assert reason == ""

    def test_containment_is_the_rule_and_not_nearest(self):
        # 5'-3 1/2" at x=383 is 65pt from the 318 line and 26'-2 1/2" at 209 is
        # 109pt from it. Nearest would give the 8-9 gap the wrong one; the gap
        # it is printed INSIDE gives the right one.
        value, _ = drawing_truth.dimension_between(self.CHAIN, 318.0, 448.0, 0)
        assert value == "5' - 3 1/2\""

    def test_two_different_dimensions_in_one_gap_are_refused(self):
        # An overall dimension crossing a bay run: the question has two true
        # answers and the set must not pick one.
        crowded = [*self.CHAIN, ("44' - 6\"", 250.0, 90.0)]
        value, reason = drawing_truth.dimension_between(crowded, 100.0, 318.0, 0)
        assert value is None
        assert "2 different dimensions" in reason

    def test_the_same_value_written_twice_is_one_answer(self):
        twice = [*self.CHAIN, ("26' - 2 1/2\"", 209.0, 300.0)]
        value, _ = drawing_truth.dimension_between(twice, 100.0, 318.0, 0)
        assert value == "26' - 2 1/2\""

    def test_a_dimension_just_outside_the_boundary_is_refused(self):
        # 7pt past the 318 line: containment says it belongs to the next gap
        # and 20pt of jitter says that verdict is rounding. The reading moves,
        # so there is no answer here — the same refusal a label gets when it
        # moves under jitter, applied to the BOUNDARY rather than to a point.
        edgy = [("26' - 2 1/2\"", 209.0, 40.0), ("9' - 0\"", 325.0, 40.0)]
        value, reason = drawing_truth.dimension_between(edgy, 100.0, 318.0, 0)
        assert value is None
        assert "boundary jitter" in reason

    def test_an_empty_gap_is_refused_rather_than_guessed(self):
        value, reason = drawing_truth.dimension_between(self.CHAIN, 700.0, 900.0, 0)
        assert value is None
        assert "no dimension" in reason

    def test_the_row_axis_reads_the_other_coordinate(self):
        vertical = [("12' - 0\"", 40.0, 209.0)]
        assert drawing_truth.dimension_between(vertical, 100.0, 318.0, 1)[0] == "12' - 0\""
        assert drawing_truth.dimension_between(vertical, 100.0, 318.0, 0)[0] is None

    def test_only_adjacent_grid_lines_are_asked_about(self):
        # "between 7 and 9" spans a line and has no single dimension. Asking it
        # would score a model for declining what the drawing declines too.
        pairs = drawing_truth.adjacent_pairs(self.COLUMNS)
        assert [(a, b) for a, b, _, _ in pairs] == [("7", "8"), ("8", "9"), ("9", "10")]

    def test_pairs_come_from_position_and_not_from_name(self):
        # Grid names are not sortable: 4.6 sits between 4 and 6, and "10" sorts
        # before "7" as a string.
        pairs = drawing_truth.adjacent_pairs({"6": 300.0, "4": 100.0, "4.6": 230.0})
        assert [(a, b) for a, b, _, _ in pairs] == [("4", "4.6"), ("4.6", "6")]

    def test_the_distractor_is_the_neighbouring_bay(self):
        got = drawing_truth._neighbour_bay(self.CHAIN, self.COLUMNS, "8", "9", 0, "5' - 3 1/2\"")
        assert got in {"26' - 2 1/2\"", "18' - 0\""}

    def test_a_bay_with_no_usable_neighbour_has_no_distractor(self):
        lone = [("26' - 2 1/2\"", 209.0, 40.0)]
        assert drawing_truth._neighbour_bay(lone, {"7": 100.0, "8": 318.0}, "7", "8", 0, "26' - 2 1/2\"") is None

    def test_the_pattern_matches_a_dimension_as_a_drafter_writes_it(self):
        for good in ("26' - 2 1/2\"", "18' - 0\"", "5'-3 1/2\""):
            assert drawing_truth.DIMENSION.fullmatch(good), good
        for bad in ("S-301.0", "HSS8X8X3/8", "F9", "26'"):
            assert not drawing_truth.DIMENSION.fullmatch(bad), bad

    def test_the_scorers_shape_pattern_accepts_what_a_model_writes(self):
        # The extraction pattern reads the PDF, where the spacing is the
        # drafter's. The scorer sees prose, where it is the model's.
        shape = re.compile(drawing_truth.LABEL_PATTERN["grid-spacing"])
        for written in ("26' - 2 1/2\"", "26'-2 1/2\"", "26' - 2 1/2 \""):
            assert shape.fullmatch(written), written
        assert not shape.fullmatch("HSS8X8X3/8")


class TestTheSpacingLoopActuallyRuns:
    """`build` end to end over a real PDF, because the unit tests above cover
    the decisions and NOT the loop that calls them.

    That distinction is not academic here. A first regeneration against the
    real sheet emitted 40 cases and 14 refusals — every one of them a footing
    or a column, not one spacing case and, more tellingly, not one spacing
    REFUSAL. Ten candidate gaps producing neither an answer nor a reason is
    the signature of a loop that never ran, and no test of `dimension_between`
    could have shown it.
    """

    @staticmethod
    def _sheet(tmp_path, dimensions):
        """A page carrying the given dimensions; a 4th field rotates one."""
        doc = fitz.open()
        page = doc.new_page(width=800, height=600)
        for text, x, y, *rest in dimensions:
            page.insert_text((x, y), text, fontsize=8, rotate=(rest[0] if rest else 0))
        path = tmp_path / "sheet.pdf"
        doc.save(str(path))
        doc.close()
        return str(path)

    # Two column lines and two row lines, so there is exactly one gap on each
    # axis and the arithmetic is checkable by eye.
    AXES = ({"7": 100.0, "8": 400.0}, {"B": 100.0, "C": 500.0})

    def test_a_gap_with_one_dimension_becomes_a_case(self, tmp_path, monkeypatch):
        pdf = self._sheet(tmp_path, [("26'-2 1/2\"", 200.0, 300.0)])
        monkeypatch.setattr(drawing_truth.grid, "axes", lambda _: self.AXES)
        cases = drawing_truth.build(pdf, "p1", "S-100.0", explain=False)
        spacing = [c for c in cases if c["tag"] == "grid-spacing"]
        assert len(spacing) == 1, [c["question"] for c in cases]
        assert spacing[0]["expected"] == "26'-2 1/2\""
        assert "column line 7 and column line 8" in spacing[0]["question"]

    def test_the_case_carries_what_the_scorer_needs(self, tmp_path, monkeypatch):
        pdf = self._sheet(tmp_path, [("26'-2 1/2\"", 200.0, 300.0)])
        monkeypatch.setattr(drawing_truth.grid, "axes", lambda _: self.AXES)
        case = [c for c in drawing_truth.build(pdf, "p1", "S-100.0", False) if c["tag"] == "grid-spacing"][0]
        assert case["labelPattern"] == drawing_truth.LABEL_PATTERN["grid-spacing"]
        assert case["sheetLabels"] == ["26'-2 1/2\""]
        assert case["derivation"]["between"] == ["7", "8"]
        assert case["derivation"]["gapPt"] == 300.0
        assert case["projectId"] == "p1"

    def test_a_gap_that_refuses_says_so_instead_of_vanishing(self, tmp_path, monkeypatch):
        # No dimension anywhere: both gaps must REFUSE, and a refusal is what
        # tells someone the loop ran at all.
        pdf = self._sheet(tmp_path, [("S-301.0", 200.0, 300.0)])
        monkeypatch.setattr(drawing_truth.grid, "axes", lambda _: self.AXES)
        cases = drawing_truth.build(pdf, "p1", "S-100.0", explain=False)
        assert not [c for c in cases if c["tag"] == "grid-spacing"]

    def test_both_axes_are_walked(self, tmp_path, monkeypatch):
        # One dimension written across for the column gap, one rotated for the
        # row gap. A loop that ran only the first axis passes every test above.
        pdf = self._sheet(
            tmp_path,
            [("26'-2 1/2\"", 200.0, 300.0), ("12'-0\"", 600.0, 300.0, 90)],
        )
        monkeypatch.setattr(
            drawing_truth.grid,
            "axes",
            lambda _: ({"7": 100.0, "8": 400.0}, {"B": 100.0, "C": 500.0}),
        )
        cases = drawing_truth.build(pdf, "p1", "S-100.0", explain=False)
        axes_asked = {c["derivation"]["axis"] for c in cases if c["tag"] == "grid-spacing"}
        assert axes_asked == {"column line", "row line"}

    def test_a_page_with_no_grid_emits_no_spacing_case(self, tmp_path, monkeypatch):
        pdf = self._sheet(tmp_path, [("26'-2 1/2\"", 200.0, 300.0)])
        monkeypatch.setattr(drawing_truth.grid, "axes", lambda _: ({}, {}))
        assert drawing_truth.build(pdf, "p1", "S-100.0", explain=False) == []

    def test_a_horizontal_dimension_never_answers_a_row_gap(self, tmp_path, monkeypatch):
        # It sits inside the row gap by containment, and it measures a
        # horizontal distance. Offering it as the answer to "what is between
        # row lines B and C" would be a wrong answer derived from the PDF,
        # which is the one thing this generator must never produce.
        pdf = self._sheet(tmp_path, [("26'-2 1/2\"", 200.0, 300.0)])
        monkeypatch.setattr(drawing_truth.grid, "axes", lambda _: self.AXES)
        spacing = [
            c for c in drawing_truth.build(pdf, "p1", "S-100.0", False) if c["tag"] == "grid-spacing"
        ]
        assert [c["derivation"]["axis"] for c in spacing] == ["column line"]

    def test_a_rotated_dimension_answers_the_row_gap_alone(self, tmp_path, monkeypatch):
        pdf = self._sheet(tmp_path, [("12'-0\"", 200.0, 300.0, 90)])
        monkeypatch.setattr(drawing_truth.grid, "axes", lambda _: self.AXES)
        spacing = [
            c for c in drawing_truth.build(pdf, "p1", "S-100.0", False) if c["tag"] == "grid-spacing"
        ]
        assert [c["derivation"]["axis"] for c in spacing] == ["row line"]
        assert spacing[0]["expected"] == "12'-0\""

    def test_dimensions_of_reports_which_way_the_text_runs(self, tmp_path):
        pdf = self._sheet(tmp_path, [("26'-2 1/2\"", 200.0, 300.0), ("12'-0\"", 600.0, 300.0, 90)])
        doc = fitz.open(pdf)
        by_text = {d[0]: d[3] for d in drawing_truth.dimensions_of(doc[0])}
        assert by_text == {
            "26'-2 1/2\"": drawing_truth.ACROSS,
            "12'-0\"": drawing_truth.DOWN,
        }
        doc.close()

    def test_dimensions_are_read_off_the_page_itself(self, tmp_path):
        pdf = self._sheet(tmp_path, [("26'-2 1/2\"", 200.0, 300.0), ("HSS8X8X3/8", 400.0, 300.0)])
        doc = fitz.open(pdf)
        found = drawing_truth.dimensions_of(doc[0])
        assert [d[0] for d in found] == ["26'-2 1/2\""]
        text, x, y, _ = found[0]
        assert 200.0 < x < 300.0, "the span's centre, not its origin"
        doc.close()


class TestOrientationOnARotatedSheet:
    """The bug the real sheet found, and the reason it looked like absence.

    `get_text` reports in the page's UNROTATED system — the same fact that
    makes a clip need `derotation_matrix` — so reading `dir` raw while mapping
    the bbox to display space is an exact 90-degree inversion on a /Rotate 90
    sheet. Seven of eight column gaps then reported "no dimension printed
    inside this gap" on a drawing that visibly carries a dimension chain,
    because every column gap was hunting text that runs down the sheet.

    What is asserted is AGREEMENT rather than a fixed answer, and the first
    version of this class got that wrong: it demanded that a given string stay
    horizontal at every rotation, which is false and is not the claim. Rotating
    a page genuinely changes which way its text reads on the display. The
    invariant is that the two readings describe ONE drawing — a run classified
    horizontal has a display bbox wider than it is tall — because the gap
    boundaries it will be compared against are display coordinates.
    """

    @staticmethod
    def _sheet(tmp_path, rotation):
        doc = fitz.open()
        page = doc.new_page(width=800, height=600)
        page.insert_text((200.0, 300.0), "26'-2 1/2\"", fontsize=8)
        page.insert_text((600.0, 300.0), "12'-0\"", fontsize=8, rotate=90)
        page.set_rotation(rotation)
        path = tmp_path / f"rot{rotation}.pdf"
        doc.save(str(path))
        doc.close()
        return str(path)

    @staticmethod
    def _display_boxes(page):
        """Each dimension's bbox in DISPLAY space, read independently of `dir`."""
        to_display = ~page.derotation_matrix
        out = {}
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = span["text"].strip()
                    if drawing_truth.DIMENSION.fullmatch(text):
                        out[text] = fitz.Rect(span["bbox"]) * to_display
        return out

    @pytest.mark.parametrize("rotation", [0, 90, 180, 270])
    def test_the_direction_agrees_with_the_shape_of_the_box(self, tmp_path, rotation):
        # The bug in one assertion: before the fix, at /Rotate 90 every run was
        # called horizontal while its display box was taller than it was wide.
        doc = fitz.open(self._sheet(tmp_path, rotation))
        page = doc[0]
        boxes = self._display_boxes(page)
        for text, x, y, runs in drawing_truth.dimensions_of(page):
            box = boxes[text]
            expected = drawing_truth.ACROSS if box.width > box.height else drawing_truth.DOWN
            assert runs == expected, (
                f"/Rotate {rotation}: {text!r} called {runs} "
                f"but its display box is {box.width:.0f}x{box.height:.0f}"
            )
        doc.close()

    @pytest.mark.parametrize("rotation", [0, 90, 180, 270])
    def test_the_two_runs_never_share_an_orientation(self, tmp_path, rotation):
        # One reads across the sheet and one down it, whatever the rotation, so
        # a classifier that has collapsed to a constant is caught here.
        doc = fitz.open(self._sheet(tmp_path, rotation))
        flags = sorted(str(d[3]) for d in drawing_truth.dimensions_of(doc[0]))
        assert flags == [drawing_truth.ACROSS, drawing_truth.DOWN], f"/Rotate {rotation}"
        doc.close()

    def test_an_unrotated_sheet_reads_the_way_it_is_drawn(self, tmp_path):
        # The anchor: with no rotation, display space IS the page's own space.
        doc = fitz.open(self._sheet(tmp_path, 0))
        by_text = {d[0]: d[3] for d in drawing_truth.dimensions_of(doc[0])}
        assert by_text == {
            "26'-2 1/2\"": drawing_truth.ACROSS,
            "12'-0\"": drawing_truth.DOWN,
        }
        doc.close()

    def test_a_diagonal_run_belongs_to_neither_axis(self, tmp_path):
        # Not clearly along either one, so it answers neither gap. Forcing it
        # onto the nearer axis would let a skewed callout answer a bay
        # question, which is the one thing a refusal is cheaper than.
        doc = fitz.open()
        page = doc.new_page(width=800, height=600)
        page.insert_text((200.0, 300.0), "26'-2 1/2\"", fontsize=8, morph=(
            fitz.Point(200.0, 300.0), fitz.Matrix(45),
        ))
        path = tmp_path / "skew.pdf"
        doc.save(str(path))
        doc.close()
        opened = fitz.open(str(path))
        assert [d[3] for d in drawing_truth.dimensions_of(opened[0])] == [None]
        opened.close()

    def test_a_diagonal_dimension_is_still_part_of_the_sheets_vocabulary(self, tmp_path, monkeypatch):
        # Dropping it from sheetLabels would make the scorer call it INVENTED —
        # the outcome that accuses the model of fabricating something that is
        # printed on the drawing.
        doc = fitz.open()
        page = doc.new_page(width=800, height=600)
        page.insert_text((200.0, 300.0), "26'-2 1/2\"", fontsize=8, morph=(
            fitz.Point(200.0, 300.0), fitz.Matrix(45),
        ))
        page.insert_text((250.0, 300.0), "9'-0\"", fontsize=8)
        path = tmp_path / "mixed.pdf"
        doc.save(str(path))
        doc.close()
        monkeypatch.setattr(
            drawing_truth.grid, "axes", lambda _: ({"7": 100.0, "8": 400.0}, {"B": 100.0, "C": 500.0})
        )
        spacing = [
            c for c in drawing_truth.build(str(path), "p1", "S-100.0", False)
            if c["tag"] == "grid-spacing"
        ]
        assert spacing, "the horizontal dimension still answers its gap"
        assert "26'-2 1/2\"" in spacing[0]["sheetLabels"]
        assert spacing[0]["expected"] == "9'-0\""

    def test_the_axis_threshold_is_pinned_at_its_boundary(self):
        # Reachable only as numbers: a page whose text sits at exactly
        # atan(1/2) is a floating-point coincidence rather than a fixture.
        assert drawing_truth.runs_along(1.0, 0.0) == drawing_truth.ACROSS
        assert drawing_truth.runs_along(0.0, 1.0) == drawing_truth.DOWN
        assert drawing_truth.runs_along(2.0, 1.0) == drawing_truth.ACROSS, "twice is twice"
        assert drawing_truth.runs_along(1.0, 2.0) == drawing_truth.DOWN
        # 30 degrees: 1.73x, dominant but not dominant enough.
        assert drawing_truth.runs_along(0.866, 0.5) is None
        assert drawing_truth.runs_along(1.0, 1.0) is None, "45 degrees is neither"
        assert drawing_truth.runs_along(0.0, 0.0) is None, "no direction at all"

    def test_the_threshold_ignores_the_sign_of_the_direction(self):
        # Text running right-to-left or bottom-to-top measures the same axis.
        for across, down in ((-1.0, 0.0), (1.0, -0.0)):
            assert drawing_truth.runs_along(across, down) == drawing_truth.ACROSS
        assert drawing_truth.runs_along(0.0, -1.0) == drawing_truth.DOWN


class TestTheEmittedTally:
    """A total is a liveness signal only if you know what it should be.

    "40 cases emitted, 24 refused" read as healthy on a run where the spacing
    tag produced zero, because 40 is exactly the footing-plus-column count.
    The figure that should have raised the alarm was the one that looked
    normal, and nothing downstream reports a tag that is simply absent.
    """

    def test_the_breakdown_names_every_tag(self):
        said = drawing_truth.tally(
            [{"tag": "grid-footing"}] * 19 + [{"tag": "grid-column"}] * 21,
            ["a"] * 24,
            drawing_truth.TAGS,
        )
        assert "40 cases emitted" in said
        assert "grid-footing 19" in said and "grid-column 21" in said
        for quiet in set(drawing_truth.TAGS) - {"grid-footing", "grid-column"}:
            assert f"{quiet} 0" in said
        assert "24 refused" in said

    def test_a_tag_that_emitted_nothing_is_called_out(self):
        # Derived from TAGS rather than naming one, so adding a tag to the
        # generator does not silently retarget this test at a different tag —
        # or break it, which is what a hardcoded vocabulary did here once.
        *alive, dead = drawing_truth.TAGS
        said = drawing_truth.tally(
            [{"tag": t} for t in alive], [], drawing_truth.TAGS
        )
        assert f"NO CASES for {dead}" in said

    def test_several_dead_tags_are_named_together(self):
        alive, *dead = drawing_truth.TAGS
        said = drawing_truth.tally([{"tag": alive}], [], drawing_truth.TAGS)
        assert f"NO CASES for {', '.join(dead)}" in said

    def test_a_full_set_says_nothing_extra(self):
        said = drawing_truth.tally(
            [{"tag": t} for t in drawing_truth.TAGS], [], drawing_truth.TAGS
        )
        assert "NO CASES" not in said
        assert said.count("\n") == 0, "one line when every tag answered"

    def test_a_tag_outside_the_vocabulary_does_not_crash_the_count(self):
        said = drawing_truth.tally(
            [{"tag": t} for t in drawing_truth.TAGS] + [{"tag": "grid-beam"}],
            [],
            drawing_truth.TAGS,
        )
        assert f"{len(drawing_truth.TAGS) + 1} cases emitted" in said
        assert "NO CASES" not in said


class TestTheLabelClassTable:
    """A tag is one row, so it cannot ship with half of itself missing.

    The three things a tag needs used to live in three places — an extraction
    pattern, a scorer-facing shape and a question written inline in the loop.
    Nothing checked that a tag had all three, and the failure that shape
    produces is a set whose cases carry another tag's vocabulary: well-formed,
    scored, and measuring the wrong thing.
    """

    def test_every_tag_the_generator_names_has_a_shape(self):
        assert set(drawing_truth.LABEL_PATTERN) == set(drawing_truth.TAGS)

    def test_every_intersection_tag_is_complete(self):
        for tag, cls in drawing_truth.INTERSECTION_TAGS.items():
            assert cls.extract.pattern, tag
            assert cls.shape, tag
            for field in ("{sheet}", "{col}", "{row}"):
                assert field in cls.question, f"{tag} never fills {field}"

    def test_a_question_never_names_a_candidate_answer(self):
        """The prohibition the vision prompt already carries, on this side.

        A question that shows what a mark looks like invites the model to
        answer in that shape without reading the drawing, and the run then
        scores the question rather than the sheet.
        """
        for tag, cls in drawing_truth.INTERSECTION_TAGS.items():
            filled = cls.question.format(sheet="S-100.0", col="4", row="B")
            assert not re.search(cls.shape, filled), f"{tag} seeds its own answer"

    def test_the_two_mark_vocabularies_do_not_capture_each_other(self):
        """`PC1` is a pile cap and `C1` is a column mark, and the difference is
        one character at the front. Extraction is `fullmatch`, which is what
        keeps them apart; `search` would file every pile cap as a column mark
        as well, and both tags would then be scored against a polluted
        vocabulary."""
        pilecap = drawing_truth.INTERSECTION_TAGS["grid-pilecap"].extract
        colmark = drawing_truth.INTERSECTION_TAGS["grid-colmark"].extract
        assert colmark.fullmatch("C1") and not colmark.fullmatch("PC1")
        assert pilecap.fullmatch("PC1") and not pilecap.fullmatch("C1")

    def test_a_column_mark_shape_does_not_match_inside_a_pile_cap(self):
        """The scorer's own bracketing, asserted here because it is what makes
        the short `C\\d` shape safe to emit at all."""
        shape = drawing_truth.LABEL_PATTERN["grid-colmark"]
        scorer = re.compile(f"(?<![a-z0-9])(?:{shape})(?![a-z0-9])", re.I)
        assert scorer.search("the mark is C3")
        assert not scorer.search("the pile cap is PC1")


class TestAMarkThatNamesItsOwnIntersection:
    """S101P marks columns C1..C4, names rows A..H and columns 1..19 — so at
    column line 1 and row line C the mark `C1` and the intersection's own
    shorthand are the same two characters, and no reading of the answer tells
    them apart."""

    def test_the_collision_is_refused(self):
        assert drawing_truth.names_its_own_intersection("C1", "1", "C")

    def test_the_other_order_is_refused_too(self):
        assert drawing_truth.names_its_own_intersection("C1", "C", "1")

    def test_the_same_mark_elsewhere_is_fine(self):
        # C1 at 11/H is unambiguous: nothing about that intersection is "C1".
        assert not drawing_truth.names_its_own_intersection("C1", "11", "H")

    def test_it_is_not_special_cased_to_column_marks(self):
        # A footing mark collides the same way on a sheet with an F row.
        assert drawing_truth.names_its_own_intersection("F2", "2", "F")

    def test_an_ordinary_mark_is_untouched(self):
        assert not drawing_truth.names_its_own_intersection("PC1", "4", "B")
        assert not drawing_truth.names_its_own_intersection("F12", "4", "B")


class TestTheMarkTagsActuallyRun:
    """`build` end to end, for the reason the spacing tag established: every
    decision above is unit-tested and the LOOP that calls them is not, and a
    tag wired up wrong emits nothing while every unit test stays green."""

    AXES = ({"7": 100.0, "8": 400.0}, {"B": 100.0, "C": 500.0})

    @staticmethod
    def _sheet(tmp_path, marks):
        doc = fitz.open()
        page = doc.new_page(width=800, height=600)
        for text, x, y in marks:
            page.insert_text((x, y), text, fontsize=8)
        path = tmp_path / "marks.pdf"
        doc.save(str(path))
        doc.close()
        return str(path)

    def _build(self, tmp_path, monkeypatch, marks):
        pdf = self._sheet(tmp_path, marks)
        monkeypatch.setattr(drawing_truth.grid, "axes", lambda _: self.AXES)
        return drawing_truth.build(pdf, "p1", "S101P", explain=False)

    def test_a_pile_cap_mark_becomes_a_case(self, tmp_path, monkeypatch):
        cases = self._build(tmp_path, monkeypatch, [("PC1", 105.0, 105.0)])
        caps = [c for c in cases if c["tag"] == "grid-pilecap"]
        assert caps, [c["tag"] for c in cases]
        assert caps[0]["expected"] == "PC1"
        assert "pile cap mark" in caps[0]["question"]
        assert caps[0]["labelPattern"] == drawing_truth.LABEL_PATTERN["grid-pilecap"]

    def test_a_column_mark_becomes_a_case(self, tmp_path, monkeypatch):
        cases = self._build(tmp_path, monkeypatch, [("C3", 105.0, 105.0)])
        marks = [c for c in cases if c["tag"] == "grid-colmark"]
        assert marks, [c["tag"] for c in cases]
        assert marks[0]["expected"] == "C3"
        assert marks[0]["derivation"]["gridColumn"] == "7"
        assert marks[0]["derivation"]["gridRow"] == "B"

    def test_a_pile_cap_is_not_also_emitted_as_a_column_mark(self, tmp_path, monkeypatch):
        cases = self._build(tmp_path, monkeypatch, [("PC1", 105.0, 105.0)])
        assert not [c for c in cases if c["tag"] == "grid-colmark"]

    def test_a_mark_naming_its_own_intersection_is_refused(self, tmp_path, monkeypatch):
        # "C7" sits at column line 7, row line C — the one intersection where
        # that mark is indistinguishable from the question.
        cases = self._build(tmp_path, monkeypatch, [("C7", 105.0, 505.0)])
        at_7c = [
            c
            for c in cases
            if c["tag"] == "grid-colmark"
            and (c["derivation"]["gridColumn"], c["derivation"]["gridRow"]) == ("7", "C")
        ]
        assert not at_7c

    def test_the_sheet_vocabulary_travels_with_the_case(self, tmp_path, monkeypatch):
        # Both marks are on the sheet, so naming either is off-target rather
        # than invented — the distinction `sheetLabels` exists to carry.
        cases = self._build(
            tmp_path, monkeypatch, [("C3", 105.0, 105.0), ("C2", 405.0, 105.0)]
        )
        marks = [c for c in cases if c["tag"] == "grid-colmark"]
        assert marks
        assert all(c["sheetLabels"] == ["C2", "C3"] for c in marks)


class TestWhoseLabelAnIntersectionMayClaim:
    """The converse question `stable_reading` was missing.

    "Which label is nearest this intersection?" always has an answer, and that
    is not the same as the label being THIS intersection's. On S101P — row
    lines D and C 40pt apart, nine column pairs 50-64pt apart — one radius of
    100pt reaches several intersections at once, so 164 of 205 generated cases
    asked about a mark belonging somewhere else. The set asserted a column mark
    at an unmarked intersection and scored the model WRONG for disagreeing,
    which is a benchmark inventing bad news rather than reporting it.
    """

    # Two column lines 52pt apart, as tight as S101P's tightest pair.
    TIGHT = [("9", "E", 100.0, 100.0), ("10", "E", 152.0, 100.0)]

    def test_a_label_on_its_own_intersection_is_owned_by_it(self):
        assert drawing_truth.owning_intersection(102.0, 104.0, self.TIGHT) == ("9", "E")

    def test_a_close_neighbour_does_not_get_to_claim_it(self):
        # 2pt from column 9 and 50pt from column 10: inside MAX_LABEL_PT of
        # both, and only one of them owns it.
        assert drawing_truth.owning_intersection(102.0, 100.0, self.TIGHT) != ("10", "E")

    def test_a_label_midway_between_two_belongs_to_neither(self):
        # Dead centre: 20pt of jitter hands it to either side, so it is not an
        # answer for either — the same refusal the reading's own jitter makes.
        assert drawing_truth.owning_intersection(126.0, 100.0, self.TIGHT) is None

    def test_ownership_is_decided_in_two_dimensions(self):
        grid_ = [("4", "B", 0.0, 0.0), ("4", "C", 0.0, 300.0)]
        assert drawing_truth.owning_intersection(5.0, 290.0, grid_) == ("4", "C")


class TestAMarkIsNotClaimedByItsNeighbour:
    """`build` end to end on the geometry that produced the bad set."""

    @staticmethod
    def _sheet(tmp_path, marks):
        doc = fitz.open()
        page = doc.new_page(width=800, height=600)
        for text, x, y in marks:
            page.insert_text((x, y), text, fontsize=8)
        path = tmp_path / "tight.pdf"
        doc.save(str(path))
        doc.close()
        return str(path)

    # Column lines 9 and 10 are 52pt apart, as tight as S101P's tightest pair.
    TIGHT = ({"9": 100.0, "10": 152.0}, {"E": 100.0, "H": 400.0})
    # A grid with S101P's widest spacing, where both marks are unambiguous.
    ROOMY = ({"9": 100.0, "10": 250.0}, {"E": 100.0, "H": 400.0})

    def _colmarks(self, tmp_path, monkeypatch, marks, axes):
        pdf = self._sheet(tmp_path, marks)
        monkeypatch.setattr(drawing_truth.grid, "axes", lambda _: axes)
        cases = drawing_truth.build(pdf, "p1", "S101P", explain=False)
        return {
            (c["derivation"]["gridColumn"], c["derivation"]["gridRow"]): c["expected"]
            for c in cases
            if c["tag"] == "grid-colmark"
        }

    def test_the_owning_intersection_still_gets_its_case(self, tmp_path, monkeypatch):
        got = self._colmarks(tmp_path, monkeypatch, [("C2", 101.0, 103.0)], self.TIGHT)
        assert got.get(("9", "E")) == "C2", got

    def test_the_neighbour_52pt_away_does_not(self, tmp_path, monkeypatch):
        """The exact shape of the bug: 10/E is well inside MAX_LABEL_PT of a
        mark that is 52pt closer to 9/E, and must not be handed it."""
        got = self._colmarks(tmp_path, monkeypatch, [("C2", 101.0, 103.0)], self.TIGHT)
        assert ("10", "E") not in got, f"10/E was handed 9/E's mark: {got}"

    def test_a_mark_answers_exactly_one_question(self, tmp_path, monkeypatch):
        """The set-wide invariant that was violated: 66 cases were drawn from
        24 marks, and one C2 was the expected answer at four intersections."""
        got = self._colmarks(tmp_path, monkeypatch, [("C2", 101.0, 103.0)], self.TIGHT)
        assert len(got) <= 1, f"one mark produced {len(got)} cases: {got}"

    def test_each_intersection_with_its_own_mark_keeps_both(self, tmp_path, monkeypatch):
        """The rule must not simply delete the tag: where the drawing really
        does mark both lines, both cases survive."""
        got = self._colmarks(
            tmp_path, monkeypatch, [("C2", 101.0, 103.0), ("C4", 251.0, 103.0)], self.ROOMY
        )
        assert got.get(("9", "E")) == "C2" and got.get(("10", "E")) == "C4", got
