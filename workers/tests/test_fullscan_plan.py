"""Phase 2 of the full AI scan: which sheets the model is shown together, and
which windows of them. Every rule here keeps out a pair the client has already
rejected, so most tests are "this must NOT be paired"."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402

import fullscan_plan as fp  # noqa: E402
from sheet_facts import PageFacts  # noqa: E402

# A structural grid and the architectural one drawn on the same lines, named
# differently and shifted on the sheet — the client's RFI 002 shape.
S_X = {"A": 420.0, "B": 600.0, "C": 870.0, "D": 1140.0, "E": 1410.0}
S_Y = {"6": 215.0, "5": 393.0, "4": 664.0, "3": 934.0, "2": 1203.0, "1": 1473.0}
A_X = {k: v - 146.0 for k, v in S_X.items()}
A_Y = {n: S_Y[o] - 30.0 for o, n in zip(S_Y, ["9", "8", "6", "4", "3", "2"])}


def facts(pid, sheet, discipline, level="LEVEL 2", kind="plan", scales=(9.0,), x=None, y=None, repeats=False):
    return PageFacts(
        page_id=pid, document_id="d", page_number=int(pid[1:]), combined_page_number=int(pid[1:]),
        sheet_number=sheet, discipline=discipline, kind=kind, level=level, scales=list(scales),
        grid={"x": x if x is not None else S_X, "y": y if y is not None else S_Y, "size": [2592, 1728], "repeats": repeats},
    )


def test_two_disciplines_on_one_level_at_one_scale_are_paired():
    pairs, _ = fp.candidate_pairs([facts("p1", "S0.302", "structural"), facts("p2", "A3.01", "architectural", x=A_X, y=A_Y)])
    assert [(p.kind, {p.a.sheet_number, p.b.sheet_number}) for p in pairs] == [("same_level", {"S0.302", "A3.01"})]


def test_two_levels_are_never_paired():
    """The client's first rejected draft: A3.03 Level 4 against A3.05 Level 6."""
    pairs, skipped = fp.candidate_pairs([facts("p1", "A3.03", "architectural", "LEVEL 4"), facts("p2", "S2.105", "structural", "LEVEL 6")])
    assert pairs == []
    assert sorted(skipped["plan with no plan of another discipline on the same level"]) == ["A3.03", "S2.105"]


def test_an_unpaired_plan_is_told_what_to_fix():
    """One reason used to cover three causes with three different fixes."""
    _, skipped = fp.candidate_pairs([
        facts("p1", "A1.01", None),                                   # no discipline read
        facts("p2", "A2.01", "architectural", "LEVEL 3"),             # alone on its level
        facts("p3", "A2.02", "architectural", "LEVEL 3"),             # same discipline only
        facts("p4", "A4.01", "architectural", "ROOF", scales=(9.0,)),
        facts("p5", "S4.01", "structural", "ROOF", scales=(36.0,)),   # no scale in common
    ])
    assert any("gave no discipline" in why and labels == ["A1.01"] for why, labels in skipped.items())
    assert sorted(skipped["plan with no plan of another discipline on the same level"]) == ["A2.01", "A2.02"]
    assert sorted(next(v for k, v in skipped.items() if "no printed scale in common" in k)) == ["A4.01", "S4.01"]


def test_the_no_pairs_note_names_the_biggest_reasons():
    note = fp.no_pairs_note({"plan whose drawing title names no single level": ["a"] * 22,
                             "plan with no printed drawing scale": ["b"] * 9,
                             "sheet with several views (a grid label in two places)": ["c"]})
    assert "Most were left out because: plan whose drawing title names no single level (22); " \
           "plan with no printed drawing scale (9);" in note
    assert fp.no_pairs_note({}).startswith("No two sheets could be paired")


def test_a_plan_with_no_level_or_two_levels_is_left_out_and_listed():
    pairs, skipped = fp.candidate_pairs([facts("p1", "A3.32", "architectural", None), facts("p2", "S2.105", "structural")])
    assert pairs == []
    assert skipped["plan whose drawing title names no single level"] == ["A3.32"]


def test_a_sheet_of_several_views_is_left_out():
    """The second rejected draft: S1.102 (1/8") against A3.36's two 1/4" details."""
    pairs, skipped = fp.candidate_pairs([facts("p1", "A3.36", "architectural", repeats=True), facts("p2", "S1.102", "structural")])
    assert pairs == [] and skipped["sheet with several views (a grid label in two places)"] == ["A3.36"]


def test_one_discipline_at_one_scale_is_not_a_same_level_pair():
    pairs, _ = fp.candidate_pairs([facts("p1", "A3.01", "architectural"), facts("p2", "A3.02", "architectural")])
    assert pairs == []


def test_different_scales_are_not_a_same_level_pair():
    pairs, _ = fp.candidate_pairs([facts("p1", "A3.01", "architectural", scales=(9.0,)), facts("p2", "S0.302", "structural", scales=(13.5,))])
    assert pairs == []


def test_an_enlarged_plan_pairs_with_its_overall_plan_of_the_same_level():
    pairs, _ = fp.candidate_pairs([
        facts("p1", "A3.35", "architectural", "LEVEL 14", kind="enlarged_plan", scales=(18.0, 54.0), x={}, y={}),
        facts("p2", "A3.27", "architectural", "LEVEL 14", scales=(9.0,)),
        facts("p3", "A3.13", "architectural", "LEVEL 13", scales=(9.0,)),  # the level below: never
    ])
    assert [(p.kind, p.a.sheet_number, p.b.sheet_number) for p in pairs] == [("enlarged", "A3.35", "A3.27")]


def test_non_plans_are_not_listed_as_left_out():
    # Sections and schedules are covered by the text checks; they were never
    # candidates, so the plan does not report them as skipped.
    _, skipped = fp.candidate_pairs([facts("p1", "S5.210", "structural", kind="elevation")])
    assert skipped == {}


def test_grids_line_up_by_position_not_by_name():
    t = fp.grid_transform(facts("p1", "A3.01", "architectural", x=A_X, y=A_Y), facts("p2", "S0.302", "structural"))
    assert t is not None and t.scale == 1.0
    assert t.tx == pytest.approx(146, abs=1) and t.ty == pytest.approx(30, abs=1)


def test_dense_secondary_lines_fall_back_to_the_primary_lines():
    """A3.01 draws B.1..B.6 between B and C; with them, several offsets match
    nearly equally and the alignment is refused. The primaries decide."""
    secondaries = {f"B.{i}": A_X["B"] + 20 * i for i in range(1, 7)} | {f"C.{i}": A_X["C"] + 25 * i for i in range(1, 5)}
    a = facts("p1", "A3.01", "architectural", x=A_X | secondaries, y=A_Y)
    t = fp.grid_transform(a, facts("p2", "S0.302", "structural"))
    assert t is not None and t.tx == pytest.approx(146, abs=1)


def test_a_regular_grid_shifted_one_bay_is_not_lined_up():
    regular = {chr(65 + i): 300.0 + 270 * i for i in range(6)}
    shifted = {chr(75 + i): 300.0 + 270 * (i + 1) for i in range(6)}
    assert fp.grid_transform(facts("p1", "A", "architectural", x=regular), facts("p2", "S", "structural", x=shifted)) is None


def test_split_covers_the_area_with_overlapping_windows():
    area = [100.0, 50.0, 2100.0, 950.0]
    windows = fp.split(area, 750.0)
    assert min(w[0] for w in windows) == 100.0 and max(w[2] for w in windows) == 2100.0
    assert min(w[1] for w in windows) == 50.0 and max(w[3] for w in windows) == 950.0
    assert all(w[2] - w[0] <= 750.0 and w[3] - w[1] <= 750.0 for w in windows)
    xs = sorted({w[0] for w in windows})
    assert all(b - a < 750.0 for a, b in zip(xs, xs[1:]))  # neighbours overlap


def test_windows_show_the_same_area_of_both_sheets():
    t = fp.Transform(0.5, 127.0, -280.0)
    for wa, wb in fp.windows_for([200.0, 740.0, 2350.0, 1620.0], t, [2592.0, 1728.0], 750.0):
        assert wb == pytest.approx(t.rect(wa), abs=0.01)
        assert 0 <= wb[0] and wb[2] <= 2592 and 0 <= wb[1] and wb[3] <= 1728


def test_a_window_falling_off_sheet_b_is_clipped_on_both_sides():
    t = fp.Transform(1.0, 1500.0, 0.0)  # A's right half lands off B
    out = fp.windows_for([0.0, 0.0, 1500.0, 700.0], t, [2592.0, 1728.0], 750.0)
    assert out and all(wb[2] <= 2592 for _, wb in out)
    assert all(wa[2] <= 1092.0 + 0.01 for wa, _ in out)


def test_same_level_windows_cover_only_what_both_sheets_draw():
    pair = fp.Pair("same_level", facts("p1", "A3.01", "architectural", x=A_X, y=A_Y), facts("p2", "S0.302", "structural"), "r")
    windows, why = fp.same_level_windows(pair)
    assert why is None and windows
    for wa, wb in windows:
        assert wb[0] == pytest.approx(wa[0] + 146, abs=1) and wb[1] == pytest.approx(wa[1] + 30, abs=1)


def test_an_enlarged_window_stays_inside_its_detail():
    class E:  # a column of the enlarged sheet
        def __init__(self, x, y):
            self.cx, self.cy, self.w, self.h = x, y, 20.0, 20.0

    class Al:
        scale, tx, ty, detail, inliers = 0.5, 127.0, -280.0, 3, []
        members = [E(400, 900), E(1800, 900), E(1800, 1500)]

    pair = fp.Pair("enlarged", facts("p1", "A3.35", "architectural", kind="enlarged_plan"), facts("p2", "A3.27", "architectural"), "r")
    box = [200.0, 740.0, 2350.0, 1620.0]  # the detail's ink; the title block starts at 2400
    windows, why = fp.enlarged_windows(pair, [Al()], {3: box})
    assert why is None and windows
    assert all(wa[2] <= 2350.0 + 0.01 for wa, _ in windows)


def test_the_estimate_counts_two_images_per_tile_and_a_range_of_close_looks():
    est = fp.estimate(20)
    assert est["calls"] == 20 and est["images"] == 40
    assert est["verifyCalls"]["low"] <= est["verifyCalls"]["high"]
    assert est["inputTokens"] == 20 * (2 * fp.IMAGE_TOKENS + fp.SYSTEM_TOKENS + fp.WORDS_TOKENS)


def test_pages_kept_out_say_why_and_what_to_do():
    """A new project planned its scan while its document was still processing:
    "Read 0 pages … 103 kept out (old revisions, RFIs, unprocessed)"."""
    note = fp.kept_out_note({"superseded": 0, "excludedFromRfi": 0, "notProcessed": 103}, read=0)
    assert note.startswith("No page could be read: 103 page(s) belong to a document still processing")
    assert "plan again" in note
    rfi = fp.kept_out_note({"superseded": 4, "excludedFromRfi": 12, "notProcessed": 0}, read=80)
    assert rfi.startswith("Kept out of this scan:") and "Docs tab" in rfi and "old revisions" in rfi
    assert fp.kept_out_note({"superseded": 0, "excludedFromRfi": 0, "notProcessed": 0}, read=50) is None


def test_an_excluded_document_is_named_with_who_switched_it_off():
    """A 103-page drawing set was off for RFI analysis though neither its name
    nor its first pages look like an RFI; the count alone could not say why."""
    note = fp.kept_out_note(
        {"superseded": 0, "excludedFromRfi": 103, "notProcessed": 0}, read=0,
        excluded_documents=[("SET.pdf", "Excluded from RFI review by a person")],
    )
    assert '"SET.pdf" (Excluded from RFI review by a person)' in note
    assert "RFI review input" in note and "plan again" in note
    many = fp.kept_out_note({"superseded": 0, "excludedFromRfi": 9, "notProcessed": 0}, read=0,
                            excluded_documents=[(f"{i}.pdf", None) for i in range(5)])
    assert "and 2 more" in many and "no reason recorded" in many


def test_a_pair_with_no_grid_is_tiled_where_its_walls_line_up():
    """JETRIGHT's electrical plans draw no grid; the walls they copy from the
    architect's plan line them up instead."""
    import wall_match

    a = facts("p1", "A1.01", "architectural", x={}, y={})
    b = facts("p2", "E2.01", "electrical", x={}, y={})
    pair = fp.Pair("same_level", a, b, "Level 2")
    shift = wall_match.WallShift(tx=-32.0, ty=-147.0, matched=726, share=0.52, runner_up=31, extent=[400.0, 300.0, 1600.0, 1200.0])
    windows, why = fp.wall_windows(pair, shift)
    assert why is None and windows
    assert pair.transform.tx == -32.0 and pair.transform.ty == -147.0
    assert "lined up by the walls both draw" in pair.reason
    for wa, wb in windows:
        assert wb[0] == pytest.approx(wa[0] - 32.0) and wb[1] == pytest.approx(wa[1] - 147.0)
        assert wa[0] >= 400.0 - fp.WALL_PAD_PT and wa[2] <= 1600.0 + fp.WALL_PAD_PT


def test_no_wall_shift_keeps_the_pair_out_with_a_reason():
    pair = fp.Pair("same_level", facts("p1", "A3.01", "architectural"), facts("p2", "P1.04", "plumbing"), "Roof")
    windows, why = fp.wall_windows(pair, None)
    assert windows == [] and "walls" in why and pair.transform is None


def test_stray_bubbles_on_a_gridless_sheet_are_not_several_views():
    """E4.01 read one bubble on one axis and two on the other ("repeats") and
    was left out as a multi-view sheet; only a real grid can say that."""
    a = facts("p1", "A1.01", "architectural", level="LEVEL 1")
    stray = facts("p2", "E4.01", "electrical", level="LEVEL 1", x={"1": 100.0}, y={"A": 50.0, "B": 900.0}, repeats=True)
    pairs, skipped = fp.candidate_pairs([a, stray])
    assert [(p.a.sheet_number, p.b.sheet_number) for p in pairs] == [("A1.01", "E4.01")]
    real = facts("p3", "E4.01", "electrical", level="LEVEL 1", repeats=True)
    pairs, skipped = fp.candidate_pairs([a, real])
    assert pairs == [] and "sheet with several views (a grid label in two places)" in skipped


def test_two_consultants_plans_are_not_compared_with_each_other():
    """JETRIGHT's mezzanine: E2.02 against P1.03 produced a "floor edge" RFI out
    of two differently exported copies of the architect's background. Each
    consultant plan is compared with the plan that governs the geometry."""
    e = facts("p1", "E2.02", "electrical", level="MEZZANINE")
    p = facts("p2", "P1.03", "plumbing", level="MEZZANINE")
    g = facts("p3", "G2.01", "general", level="MEZZANINE")
    pairs, skipped = fp.candidate_pairs([e, p, g])
    assert pairs == []
    reason = next(k for k in skipped if "no architectural or structural plan" in k)
    assert set(skipped[reason]) == {"E2.02", "P1.03", "G2.01"}
    # With the architectural plan of that level present, each is compared with IT.
    a = facts("p4", "A1.02", "architectural", level="MEZZANINE")
    pairs, _ = fp.candidate_pairs([e, p, g, a])
    assert sorted((x.a.sheet_number, x.b.sheet_number) for x in pairs) == [
        ("A1.02", "E2.02"), ("A1.02", "G2.01"), ("A1.02", "P1.03")]


def test_an_enlarged_consultant_plan_pairs_with_its_own_discipline_only():
    big = facts("p1", "P1.05", "plumbing", level="LEVEL 1", kind="enlarged_plan", scales=(18.0,))
    own = facts("p2", "P1.01", "plumbing", level="LEVEL 1")
    other = facts("p3", "E2.01", "electrical", level="LEVEL 1")
    pairs, _ = fp.candidate_pairs([big, own, other])
    assert [(x.kind, x.a.sheet_number, x.b.sheet_number) for x in pairs] == [("enlarged", "P1.05", "P1.01")]


def test_a_drawing_the_worker_could_not_open_is_not_called_a_disagreement(monkeypatch):
    """A user's plan reported 'the walls do not line up' for all six pairs of a
    set whose walls line up five times out of six here: every PDF download had
    failed, and an unread drawing became a drawing that disagreed."""
    from contextlib import contextmanager

    import fullscan
    import rfi_review

    @contextmanager
    def unreachable(project_id, docs):
        def open_page(document_id, page_number):
            open_page.errors[document_id] = "EndpointConnectionError: could not connect"
            return None

        open_page.errors = {}
        yield open_page

    monkeypatch.setattr(rfi_review, "_documents", unreachable)
    monkeypatch.setattr(fullscan, "_set", lambda *a, **k: None)
    monkeypatch.setattr(fullscan, "_check_cancel", lambda *a: None)
    pair = fp.Pair("same_level", facts("p1", "A1.01", "architectural", x={}, y={}),
                   facts("p2", "E2.01", "electrical", x={}, y={}), "Level 2")
    skipped, errors = {}, {}
    assert fullscan._line_up("scan", "project", [pair], skipped, errors) == []
    assert skipped == {fullscan.UNOPENED: ["A1.01 / E2.01"]}
    assert not any("walls do not line up" in reason for reason in skipped)
    note = fullscan.unopened_note(errors, {"d": "jet.pdf"})
    assert "jet.pdf (EndpointConnectionError" in note and "not a finding about the drawings" in note
    assert fullscan.unopened_note({}, {}) is None
