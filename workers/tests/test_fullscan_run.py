"""Phases 3-4 of the full AI scan: the first look, the close look, the rules.

Every model here is a STUB. These tests prove what the code does with any
reply — what it parses, refuses, saves, resumes and stops on — and nothing
about whether a real model finds real problems. Only benchmarks/rfi_eval.py
against real RFIs measures that.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import uuid
from pathlib import Path

import fitz
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import fullscan  # noqa: E402
import fullscan_run as fr  # noqa: E402
from generated import RFI_REVIEW_CHECKS  # noqa: E402

CLIENT_PDF = Path(os.environ.get("RFI_REVIEW_TEST_PDF") or "/nonexistent")
TEST_DB = os.environ.get("RFI_TEST_DATABASE_URL")


def _issue(**over):
    base = {"checkId": "C01", "kind": "missing", "element": "column C-6", "whatA": "a column", "whatB": "nothing",
            "boxA": [0.1, 0.2, 0.3, 0.4], "boxB": [0.1, 0.2, 0.3, 0.4], "confidence": "high"}
    base.update(over)
    return base


# --- parsing ----------------------------------------------------------------------------


class TestParseIssues:
    def test_reads_a_valid_issue(self):
        out = fr.parse_issues(json.dumps({"issues": [_issue()]}))
        assert out == [_issue()]

    def test_no_issues_is_an_empty_list_not_a_failure(self):
        assert fr.parse_issues('{"issues": []}') == []

    def test_a_reply_that_is_not_the_json_asked_for_is_none(self):
        assert fr.parse_issues("The drawings look fine.") is None
        assert fr.parse_issues('{"problems": []}') is None
        assert fr.parse_issues(None) is None

    def test_boxes_in_thousandths_are_scaled_down(self):
        out = fr.parse_issues(json.dumps({"issues": [_issue(boxA=[100, 200, 300, 400])]}))
        assert out[0]["boxA"] == pytest.approx([0.1, 0.2, 0.3, 0.4])

    @pytest.mark.parametrize("box", [[0.3, 0.2, 0.1, 0.4], [0.1, 0.2], [-0.1, 0, 0.2, 0.2], [1, 2, 3000, 4000], "x"])
    def test_an_issue_with_an_unusable_box_is_dropped(self, box):
        assert fr.parse_issues(json.dumps({"issues": [_issue(boxB=box)]})) == []

    def test_an_issue_naming_no_known_question_is_dropped(self):
        assert fr.parse_issues(json.dumps({"issues": [_issue(checkId="Z99")]})) == []

    def test_an_issue_without_what_each_sheet_shows_is_dropped(self):
        assert fr.parse_issues(json.dumps({"issues": [_issue(whatB="")]})) == []

    def test_at_most_four_per_tile(self):
        out = fr.parse_issues(json.dumps({"issues": [_issue(element=f"column {i}") for i in range(9)]}))
        assert len(out) == fr.MAX_ISSUES_PER_TILE

    def test_unknown_kind_and_confidence_fall_back(self):
        out = fr.parse_issues(json.dumps({"issues": [_issue(kind="weird", confidence="sure")]}))
        assert out[0]["kind"] == "conflict" and out[0]["confidence"] == "medium"


class TestParseVerdict:
    def test_keep(self):
        v = fr.parse_verdict(json.dumps({"decision": "keep", "reason": "r", "subject": "s", "question": "q",
                                         "confidence": "low", "priority": "high"}))
        assert v["decision"] == "keep" and v["confidence"] == "low" and v["priority"] == "high"

    def test_a_decision_that_is_neither_keep_nor_reject_is_none(self):
        assert fr.parse_verdict('{"decision": "maybe"}') is None
        assert fr.parse_verdict("keep") is None

    def test_defaults(self):
        v = fr.parse_verdict('{"decision": "reject"}')
        assert v["confidence"] == "medium" and v["priority"] == "normal"


# --- geometry -----------------------------------------------------------------------------


WINDOW = {"documentId": "d", "pageNumber": 1, "rect": [100.0, 200.0, 853.0, 953.0]}


def test_box_rect_maps_fractions_into_the_window():
    assert fr.box_rect(WINDOW, [0, 0, 1, 1]) == [100, 200, 853, 953]
    assert fr.box_rect(WINDOW, [0.5, 0.5, 0.6, 0.6]) == pytest.approx([476.5, 576.5, 551.8, 651.8])


def test_a_small_box_gets_a_close_up_of_at_least_the_minimum():
    r = fr.close_rect(WINDOW, [0.5, 0.5, 0.51, 0.51])
    assert r[2] - r[0] == pytest.approx(fr.CLOSE_MIN_PT)
    assert r[3] - r[1] == pytest.approx(fr.CLOSE_MIN_PT)
    cx = (r[0] + r[2]) / 2
    assert cx == pytest.approx(100 + 0.505 * 753)  # centred on the box


def test_a_large_box_is_padded_by_its_own_size():
    r = fr.close_rect(WINDOW, [0.1, 0.1, 0.9, 0.9])
    side = 0.8 * 753
    assert r[2] - r[0] == pytest.approx(side * (1 + 2 * fr.CLOSE_PAD))


# --- the rules the model is not trusted to apply ---------------------------------------------


PAIR = {"a": {"sheetNumber": "A3.01", "level": "2"}, "b": {"sheetNumber": "S0.302", "level": "2"}, "reason": "r"}


def _keep(subject="Column C-6 at the ramp", question="A3.01 shows column C-6 and S0.302 does not. Which is correct?"):
    return {"decision": "keep", "reason": "r", "subject": subject, "question": question,
            "confidence": "high", "priority": "normal"}


def test_a_grounded_keep_passes():
    assert fr.rule_out(PAIR, _keep(), fr.material_for(PAIR, ("COLUMN C-6", ""))) is None


def test_a_reject_keeps_its_reason():
    assert fr.rule_out(PAIR, {"decision": "reject", "reason": "style only"}, "") == "style only"


def test_no_verdict_is_rejected():
    assert fr.rule_out(PAIR, None, "") == "no usable verdict"


def test_a_keep_with_no_question_is_rejected():
    assert "no question" in fr.rule_out(PAIR, _keep(question=""), "C-6")


def test_an_invented_number_is_rejected():
    why = fr.rule_out(PAIR, _keep(question="Is column C-6 987 mm wide?"), fr.material_for(PAIR, ("COLUMN C-6", "")))
    assert why and "987" in why


def test_an_invented_mark_is_rejected():
    why = fr.rule_out(PAIR, _keep(subject="Column C-99"), fr.material_for(PAIR, ("COLUMN C-6", "")))
    assert why and "C-99" in why


def test_two_levels_are_never_one_finding():
    pair = {**PAIR, "b": {"sheetNumber": "S0.302", "level": "3"}}
    assert fr.rule_out(pair, _keep(), fr.material_for(pair, ("COLUMN C-6", ""))) == "the two sheets are not one level"


def test_the_first_looks_own_description_is_not_grounding():
    """The model's whatA/whatB is exactly the claim being checked, so a number
    it states there must not make the same number in the question 'grounded'."""
    material = fr.material_for(PAIR, ("COLUMN", "COLUMN"))
    assert "450" not in material
    assert fr.rule_out(PAIR, _keep(question="Is the column 450 wide?"), material)


# --- prompts --------------------------------------------------------------------------------


def test_the_discovery_prompt_offers_every_original_question():
    system = fr.discovery_system()
    assert all(c["id"] in system for c in RFI_REVIEW_CHECKS)


def test_both_prompts_mark_the_drawings_as_untrusted():
    assert "UNTRUSTED" in fr.discovery_system() and "UNTRUSTED" in fr.verify_system()


def test_the_tile_prompt_names_the_sheets_and_the_scale():
    pair = {"reason": "enlarged plan", "transform": {"scale": 0.5},
            "a": {"sheetNumber": "A3.35", "level": "14"}, "b": {"sheetNumber": "A3.27", "level": "14"}}
    user, labels = fr.tile_prompt(pair, {"tile": 0}, "WORDS A", "WORDS B")
    assert "A3.35" in user and "A3.27" in user and "2 times larger" in user
    assert "<words_a>WORDS A</words_a>" in user
    assert labels[0].startswith("Image A") and labels[1].startswith("Image B")


# --- the whole run, against a real database -------------------------------------------------

needs_db = pytest.mark.skipif(
    not TEST_DB or not CLIENT_PDF.exists(),
    reason="set RFI_TEST_DATABASE_URL to a migrated database (and RFI_REVIEW_TEST_PDF to the S2.105/A3.01 set)",
)


@pytest.fixture
def database(monkeypatch):
    import config
    import db
    import llm
    import storage

    monkeypatch.setattr(config, "DATABASE_URL", TEST_DB)
    monkeypatch.setattr(db, "_pool", None)
    monkeypatch.setattr(db, "_pool_unavailable", True)
    monkeypatch.setattr(fullscan, "HEARTBEAT_SECONDS", 0.05)
    monkeypatch.setattr(fr, "RETRY_DELAY", 0.01)
    monkeypatch.setattr(storage, "download_to_file", lambda key, path: shutil.copy(CLIENT_PDF, path))
    monkeypatch.setattr(llm, "available", lambda provider: True)
    stored: dict[str, bytes] = {}
    monkeypatch.setattr(storage, "put_bytes", lambda key, data, content_type: stored.__setitem__(key, data))
    db.stored_images = stored
    return db


def _seed(db, tiles_per_pair=3, limits=None, use_batch=False) -> dict:
    """A planned scan written by hand: the two client sheets (S2.105, A3.01)
    are two levels, so the planner would rightly refuse to pair them. The run
    reads only the stored pairs and tiles, which is what this exercises."""
    project, document, user, scan = (str(uuid.uuid4()) for _ in range(4))
    pdf = fitz.open(CLIENT_PDF)
    with db.connect() as conn:
        conn.execute("INSERT INTO users (id, email, name, \"passwordHash\") VALUES (%s, %s, 'fs test', 'x')",
                     (user, f"{user}@test.invalid"))
        conn.execute('INSERT INTO projects (id, name, "ownerId") VALUES (%s, \'full scan test\', %s)', (project, user))
        conn.execute('INSERT INTO documents (id, "projectId", filename, "spacesKey", pages, status) '
                     "VALUES (%s, %s, 'set.pdf', 'k', 2, 'completed')", (document, project))
        refs = []
        for n, (sheet, discipline) in enumerate([("S2.105", "structural"), ("A3.01", "architectural")], start=1):
            page_id = str(uuid.uuid4())
            conn.execute('INSERT INTO pages (id, "documentId", "pageNumber", "combinedPageNumber", "sheetNumber", discipline) '
                         "VALUES (%s, %s, %s, %s, %s, %s)", (page_id, document, n, n, sheet, discipline))
            refs.append({"pageId": page_id, "documentId": document, "pageNumber": n, "combinedPageNumber": n,
                         "sheetNumber": sheet, "discipline": discipline, "level": "5", "kind": "plan"})
        pair = {"index": 0, "kind": "same_level", "a": refs[0], "b": refs[1], "reason": "test pair",
                "tiles": tiles_per_pair, "transform": {"scale": 1, "tx": 0, "ty": 0}}
        conn.execute(
            'INSERT INTO rfi_full_scans (id, "projectId", "createdById", status, provider, model, "useBatch", pairs, '
            '"sourceRevisions", limits, notes) VALUES (%s, %s, %s, \'queued\', \'claude\', \'stub-model\', %s, %s, %s, %s, \'[]\')',
            (scan, project, user, use_batch, json.dumps([pair]), json.dumps({document: 2}),
             json.dumps(limits) if limits else None),
        )
        rect_a, rect_b = pdf[0].rect, pdf[1].rect
        for t in range(tiles_per_pair):
            x = 300 + 300 * t
            windows = {"a": {"documentId": document, "pageNumber": 1, "rect": [x, 300, x + 600, 900]},
                       "b": {"documentId": document, "pageNumber": 2, "rect": [x, 300, x + 600, 900]}}
            assert fitz.Rect(windows["a"]["rect"]) in rect_a and fitz.Rect(windows["b"]["rect"]) in rect_b
            conn.execute('INSERT INTO rfi_full_scan_tiles (id, "scanId", "pairIndex", "tileIndex", windows, status, "updatedAt") '
                         "VALUES (gen_random_uuid()::text, %s, 0, %s, %s::jsonb, 'pending', now())", (scan, t, json.dumps(windows)))
    return {"project": project, "scan": scan, "user": user, "document": document}


class Stub:
    """The model. The first look reports one issue on every tile listed in
    `issue_on`; the close look keeps it with a question grounded in the
    close-up's own words — or, with `invent`, a question naming a number the
    drawings do not carry."""

    def __init__(self, issue_on=(0,), invent=False, on_call=None):
        self.issue_on, self.invent, self.on_call = set(issue_on), invent, on_call
        self.discovery = self.verify = 0
        self.batches: list[int] = []

    def _usage(self, kw):
        import usage

        usage.record(kw.get("project_id"), "rfi", "stub-model", 4000, 300)

    def complete(self, system, user, **kw):
        import llm

        self._usage(kw)
        assert len(kw["images"]) == 2 and all(i[:4] == b"\x89PNG" for i in kw["images"])
        if self.on_call:
            self.on_call(self)
        if "confirm or reject" in system:
            self.verify += 1
            sheet_a = user.split("<sheet_a>")[1].split(",")[0]
            question = (f"Sheet {sheet_a} shows a column here that the other sheet does not. Which is correct?"
                        if not self.invent else "Is the slab 987654 thick?")
            return llm.Reply(text=json.dumps({"decision": "keep", "reason": "drawn on one only",
                                              "subject": f"Column on {sheet_a}", "question": question,
                                              "confidence": "high", "priority": "normal"}), stop_reason="end_turn")
        self.discovery += 1
        return llm.Reply(text=json.dumps({"issues": self._issues(kw["image_labels"])}), stop_reason="end_turn")

    def _issues(self, labels):
        tile = int(labels[0].rsplit("window ", 1)[1]) - 1
        if tile not in self.issue_on:
            return []
        return [_issue(element=f"column at window {tile + 1}", boxA=[0.4, 0.4, 0.6, 0.6], boxB=[0.4, 0.4, 0.6, 0.6])]

    def batch(self, prompts, **kw):
        import usage

        self.batches.append(len(prompts))
        assert usage._context.get()["stage"] == "discovery_batch"
        out = {}
        for i, cid in enumerate(sorted(prompts)):
            self._usage(kw)
            if i == 0:
                continue  # an entry the batch did not return
            out[cid] = json.dumps({"issues": self._issues(kw["image_labels"][cid])})
        return out


def _install(monkeypatch, stub):
    import llm

    monkeypatch.setattr(llm, "complete", stub.complete)
    monkeypatch.setattr(llm, "complete_batch", stub.batch)


def _scan(db, scan_id):
    return fullscan.load_scan(scan_id)


def _candidates(db, scan_id):
    with db.connect() as conn:
        return conn.execute('SELECT subject, question, evidence, origin::text, "questionSource" FROM rfi_candidates '
                            'WHERE "fullScanId" = %s', (scan_id,)).fetchall()


def _tiles(db, scan_id):
    with db.connect() as conn:
        return dict(conn.execute('SELECT status, count(*) FROM rfi_full_scan_tiles WHERE "scanId" = %s GROUP BY status',
                                 (scan_id,)).fetchall())


@needs_db
def test_a_run_looks_at_every_tile_and_saves_a_confirmed_finding(database, monkeypatch):
    seed = _seed(database)
    stub = Stub(issue_on=(1,))
    _install(monkeypatch, stub)
    result = fullscan.handle(seed["scan"], "run")
    scan = _scan(database, seed["scan"])
    assert scan["status"] == "ready" and scan["findings"] == 1 and result["saved"] == 1
    assert stub.discovery == 3 and stub.verify == 1
    assert _tiles(database, seed["scan"]) == {"done": 3}
    with database.connect() as conn:
        summary, = conn.execute("SELECT summary FROM rfi_full_scans WHERE id = %s", (seed["scan"],)).fetchone()
        outcomes = sorted(r[0] for r in conn.execute(
            'SELECT outcome FROM rfi_full_scan_tiles WHERE "scanId" = %s', (seed["scan"],)).fetchall())
    # The stub states no area verdict: the two quiet tiles are "unstated", never "agree".
    assert outcomes == ["issues", "unstated", "unstated"]
    assert summary["newFindings"] == 1 and summary["foundAgain"] == [] and summary["possibleProblems"] == 1
    assert summary["areas"] == {"issues": 1, "unstated": 2} and summary["pagesCompared"] == 2
    (subject, question, evidence, origin, source), = _candidates(database, seed["scan"])
    assert origin == "full_scan" and source == "model" and "S2.105" in question
    assert [e["sheetNumber"] for e in evidence] == ["S2.105", "A3.01"]
    # The stored box is in the page's UNROTATED space and lands inside the
    # tile's display window once rotated back.
    pdf = fitz.open(CLIENT_PDF)
    for e in evidence:
        page = pdf[e["pageNumber"] - 1]
        b = e["bbox"]
        shown = fitz.Rect(b["x"], b["y"], b["x"] + b["width"], b["y"] + b["height"]) * page.rotation_matrix
        assert shown in fitz.Rect(600, 300, 1200, 900)
        assert e["imageKey"] in database.stored_images
    with database.connect() as conn:
        stages = dict(conn.execute('SELECT stage, count(*) FROM usage_events WHERE "reviewRunId" = %s GROUP BY stage',
                                   (seed["scan"],)).fetchall())
    assert stages == {"discovery": 3, "verification": 1}


@needs_db
def test_an_ungrounded_question_is_never_saved(database, monkeypatch):
    seed = _seed(database)
    _install(monkeypatch, Stub(issue_on=(0,), invent=True))
    fullscan.handle(seed["scan"], "run")
    assert _candidates(database, seed["scan"]) == []
    scan = _scan(database, seed["scan"])
    assert scan["status"] == "ready" and any("987654" in n for n in scan["notes"])


@needs_db
def test_the_ceiling_stops_the_run_and_a_resume_finishes_it_without_asking_twice(database, monkeypatch):
    seed = _seed(database, tiles_per_pair=6, limits={"maxTotalTokens": 16000})
    stub = Stub(issue_on=(0, 5))
    _install(monkeypatch, stub)
    monkeypatch.setattr(fr, "CALL_CONCURRENCY", 1)
    fullscan.handle(seed["scan"], "run")
    scan = _scan(database, seed["scan"])
    assert scan["status"] == "partial" and any("Stopped early" in n for n in scan["notes"])
    first = stub.discovery
    assert 0 < first < 6
    spent = sum(fullscan.spent_tokens(seed["scan"]))
    assert spent <= 16000  # never past the ceiling, counting calls in flight

    with database.connect() as conn:
        conn.execute("UPDATE rfi_full_scans SET status = 'queued', limits = %s WHERE id = %s",
                     (json.dumps({"maxTotalTokens": 1_000_000}), seed["scan"]))
    fullscan.handle(seed["scan"], "run")
    scan = _scan(database, seed["scan"])
    assert scan["status"] == "ready" and stub.discovery == 6  # each tile asked exactly once across both
    assert scan["findings"] == 2


@needs_db
def test_batch_mode_sends_one_wave_and_retries_a_missing_entry_directly(database, monkeypatch):
    seed = _seed(database, tiles_per_pair=4, use_batch=True)
    stub = Stub(issue_on=(0,))
    _install(monkeypatch, stub)
    fullscan.handle(seed["scan"], "run")
    assert stub.batches == [4]
    assert stub.discovery == 1  # the one entry the batch did not return
    assert _scan(database, seed["scan"])["status"] == "ready"
    assert _tiles(database, seed["scan"]) == {"done": 4}


@needs_db
def test_changed_drawings_make_the_scan_stale_before_any_call(database, monkeypatch):
    seed = _seed(database)
    stub = Stub()
    _install(monkeypatch, stub)
    with database.connect() as conn:
        conn.execute('UPDATE rfi_full_scans SET "sourceRevisions" = %s WHERE id = %s',
                     (json.dumps({seed["document"]: 3}), seed["scan"]))
    fullscan.handle(seed["scan"], "run")
    assert _scan(database, seed["scan"])["status"] == "stale"
    assert stub.discovery == 0


@needs_db
def test_a_cancel_stops_the_run(database, monkeypatch):
    seed = _seed(database, tiles_per_pair=5)

    def cancel(stub):
        if stub.discovery == 1:
            with database.connect() as conn:
                conn.execute("UPDATE rfi_full_scans SET status = 'cancelled' WHERE id = %s", (seed["scan"],))

    stub = Stub(on_call=cancel)
    _install(monkeypatch, stub)
    monkeypatch.setattr(fr, "CALL_CONCURRENCY", 1)
    fullscan.handle(seed["scan"], "run")
    assert _scan(database, seed["scan"])["status"] == "cancelled"
    assert stub.discovery < 5


@needs_db
def test_a_starter_removed_from_the_project_stops_the_run(database, monkeypatch):
    seed = _seed(database)
    stub = Stub()
    _install(monkeypatch, stub)
    with database.connect() as conn:
        conn.execute('UPDATE projects SET "ownerId" = NULL WHERE id = %s', (seed["project"],))
        conn.execute("DELETE FROM users WHERE id = %s", (seed["user"],))
    fullscan.handle(seed["scan"], "run")
    scan = _scan(database, seed["scan"])
    assert scan["status"] == "cancelled" and "no longer has access" in scan["error"]
    assert stub.discovery == 0


@needs_db
def test_a_finding_already_on_file_is_reported_not_overwritten(database, monkeypatch):
    seed = _seed(database)
    _install(monkeypatch, Stub(issue_on=(0,)))
    fullscan.handle(seed["scan"], "run")
    with database.connect() as conn:
        conn.execute("UPDATE rfi_candidates SET status = 'dismissed' WHERE \"fullScanId\" = %s", (seed["scan"],))
        conn.execute("UPDATE rfi_full_scans SET status = 'queued' WHERE id = %s", (seed["scan"],))
        conn.execute("UPDATE rfi_full_scan_tiles SET status = 'pending', issues = NULL WHERE \"scanId\" = %s", (seed["scan"],))
    fullscan.handle(seed["scan"], "run")
    scan = _scan(database, seed["scan"])
    assert any("Found again" in n and "dismissed" in n for n in scan["notes"])
    with database.connect() as conn:
        assert conn.execute("SELECT status::text FROM rfi_candidates WHERE \"fullScanId\" = %s", (seed["scan"],)).fetchone()[0] == "dismissed"


@needs_db
def test_calls_still_in_flight_count_against_the_ceiling(database, monkeypatch):
    """Four calls running at once, none yet billed: a check that looked only
    at the ledger would send all four and land past the ceiling."""
    import time

    seed = _seed(database, tiles_per_pair=6, limits={"maxTotalTokens": 16000})
    import llm
    import usage

    stub = Stub()
    monkeypatch.setattr(stub, "_usage", lambda kw: None)

    def billed(system, user, **kw):
        time.sleep(0.3)  # the call is running; its tokens are not in the ledger yet
        reply = stub.complete(system, user, **kw)
        usage.record(kw.get("project_id"), "rfi", "stub-model", 4000, 300)
        return reply

    monkeypatch.setattr(llm, "complete", billed)
    monkeypatch.setattr(fr, "CALL_CONCURRENCY", 4)
    fullscan.handle(seed["scan"], "run")
    assert sum(fullscan.spent_tokens(seed["scan"])) <= 16000
    assert _scan(database, seed["scan"])["status"] == "partial"


# --- stage thinking settings ----------------------------------------------------------------


@pytest.mark.parametrize("value,expected", [(None, "off"), ("", "off"), ("on", "off"), ("ON", "off"),
                                            ("medium", "medium"), ("none", "off")])
def test_the_first_look_setting_is_checked_and_falls_back_to_its_own_default(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("FULL_SCAN_THINKING", raising=False)
    else:
        monkeypatch.setenv("FULL_SCAN_THINKING", value)
    assert fr.discovery_thinking() == expected


def test_the_close_look_setting_falls_back_to_low(monkeypatch):
    monkeypatch.setenv("FULL_SCAN_VERIFY_THINKING", "on")
    assert fr.verify_thinking() == "low"
    monkeypatch.setenv("FULL_SCAN_VERIFY_THINKING", "high")
    assert fr.verify_thinking() == "high"


# --- the measured rule for "the dimension between grid X and Y differs" -----------------

# The client's A3.34 (1/4" = 1'-0" and 3/4" printed) and A3.13 (1/8"), as the
# catalogue stores them: display-space grid positions, scales in pt per foot.
A3_34 = {"grid": {"y": {"3.7": 260.4, "3.5": 365.2, "2.3": 1000.8, "2": 1180.7}}, "scales": [18.0, 54.0]}
A3_13 = {"grid": {"y": {"3.7": 714.2, "3.5": 766.7, "2.3": 1084.4, "2": 1174.4}}, "scales": [9.0]}


@pytest.mark.parametrize(
    "question",
    [
        "Sheet A3.34 shows the dimension between grid line 3.7 and grid line 3.5 as 6'-1\", while sheet A3.13 "
        "shows this dimension as 5'-10\". What is the correct dimension between grid lines 3.7 and 3.5?",
        "Sheet A3.34 indicates a dimension of 10'-1\" between grid lines 2 and 2.3, while Sheet A3.13 shows "
        "this dimension as 10'-0\". Which is the correct grid dimension?",
    ],
)
def test_a_dimension_dispute_the_drawn_grid_settles_is_rejected(question):
    why = fr.grid_spacing_agrees(question, A3_34, A3_13)
    assert why and "apart on both sheets" in why


def test_a_grid_that_really_differs_is_left_for_the_model():
    moved = {"grid": {"y": {"3.7": 714.2, "3.5": 769.9}}, "scales": [9.0]}  # 6'-1" on A3.13 too? no: 6'-2"
    q = "A3.34 shows 6'-1\" between grid lines 3.7 and 3.5, A3.13 shows 6'-2\"."
    assert fr.grid_spacing_agrees(q, A3_34, moved) is None


@pytest.mark.parametrize(
    "question, a, b",
    [
        ("Column C-5 at grid lines 3.7 and 3.5 is missing.", A3_34, A3_13),  # no dimension: not a dimension claim
        ("6'-1\" between grid lines 9 and 10.", A3_34, A3_13),  # lines this sheet does not have
        ("6'-1\" between grid lines 3.7 and 3.5.", A3_34, {"grid": A3_13["grid"], "scales": []}),  # no scale read
        ("6'-1\" between grid lines 3.7 and 3.5.", None, A3_13),
    ],
)
def test_what_cannot_be_measured_decides_nothing(question, a, b):
    assert fr.grid_spacing_agrees(question, a, b) is None


def test_rule_out_applies_the_measured_rule():
    pair = {"a": {"sheetNumber": "A3.34", "level": "14"}, "b": {"sheetNumber": "A3.13", "level": "14"}}
    verdict = _keep(subject="Dimension between grid 3.7 and 3.5",
                    question="A3.34 shows 6'-1\" between grid lines 3.7 and 3.5, A3.13 shows 5'-10\". Which governs?")
    material = "A3.34 A3.13 6'-1\" 5'-10\" 3.7 3.5 14"
    assert fr.rule_out(pair, verdict, material) is None  # without the facts, nothing to measure
    assert "apart on both sheets" in fr.rule_out(pair, verdict, material, (A3_34, A3_13))


def test_both_prompts_name_the_mistakes_the_first_real_scan_made():
    for prompt in (fr.discovery_system(), fr.verify_system()):
        assert fr._NOT_A_PROBLEM in prompt


# --- the second real scan: two boxes, two places; a column measured on both sheets ------
#
# Three findings from the client's second full scan (5 Oct 2026), each measured
# against the drawings. The numbers in the first two tests are the measured
# ones; the sheets themselves are SYNTHETIC — no client drawing is in this
# repository.

WIN_A = {"documentId": "a", "pageNumber": 1, "rect": [900.0, 300.0, 1500.0, 900.0], "sheetNumber": "A9.01"}
WIN_B = {"documentId": "b", "pageNumber": 1, "rect": [1046.0, 330.0, 1646.0, 930.0]}


def _frac(window: dict, cx: float, cy: float, half: float = 30.0) -> list[float]:
    x0, y0, x1, y1 = window["rect"]
    w, h = x1 - x0, y1 - y0
    return [(cx - half - x0) / w, (cy - half - y0) / h, (cx + half - x0) / w, (cy + half - y0) / h]


def test_two_boxes_ten_feet_apart_are_two_things():
    """The C-13 finding: the A box on the column at E/4, the B box on an empty
    patch 60pt across and 64pt up (about 10 ft at 1/8")."""
    issue = _issue(boxA=_frac(WIN_A, 1200, 600), boxB=_frac(WIN_B, 1346 + 60, 630 - 64))
    why = fr.boxes_apart(issue, {"a": WIN_A, "b": WIN_B}, 9.0)
    assert why and "10 ft apart" in why


def test_two_boxes_at_two_grid_crossings_are_two_things():
    """The round-vs-square finding: B/1.4 on one sheet, C/2.3 on the other —
    one bay (270pt, 30 ft) over and one bay up. On the client's sheets the two
    clouds measured 38 ft apart; here the boxes sit exactly on the crossings."""
    issue = _issue(boxA=_frac(WIN_A, 1000, 800), boxB=_frac(WIN_B, 1146 + 270, 830 - 270))
    assert "42 ft apart" in fr.boxes_apart(issue, {"a": WIN_A, "b": WIN_B}, 9.0)


def test_boxes_at_one_place_pass_and_a_large_box_gets_room():
    same = _issue(boxA=_frac(WIN_A, 1200, 600), boxB=_frac(WIN_B, 1346 + 12, 630 - 9))
    assert fr.boxes_apart(same, {"a": WIN_A, "b": WIN_B}, 9.0) is None
    big = _issue(boxA=_frac(WIN_A, 1200, 600, half=150), boxB=_frac(WIN_B, 1346 + 60, 630, half=150))
    assert fr.boxes_apart(big, {"a": WIN_A, "b": WIN_B}, 9.0) is None


def test_both_close_ups_show_one_area():
    issue = _issue(boxA=[0.1, 0.2, 0.3, 0.4], boxB=[0.15, 0.1, 0.35, 0.3])
    assert fr.shared_box(issue) == [0.1, 0.1, 0.35, 0.4]


@pytest.mark.parametrize("element,what_a,what_b,expected", [
    ("column C-13", "column with its west face on the grid line", "column centered on the grid line", True),
    ("column", "column shifted east", "column at the crossing", True),
    ("column near Room B5", "circular column", "square column", False),  # a shape claim, not a location
    ("wall", "wall offset from grid", "wall on the grid line", False),
])
def test_only_a_column_location_claim_is_measured(element, what_a, what_b, expected):
    assert fr.is_column_location_claim(_issue(element=element, whatA=what_a, whatB=what_b)) is expected


def _sheet_with_column(rotation: int, dx_in: float = 0.0, size_in: float = 22.0, ring: bool = False) -> fitz.Document:
    """One grey filled column at display (1200 + dx, 600) — a SYNTHETIC
    sheet — drawn through the derotation matrix so it lands there at any
    /Rotate, between two wall lines like an architectural plan draws it."""
    doc = fitz.open()
    page = doc.new_page(width=2592, height=1728)
    page.set_rotation(rotation)
    m = page.derotation_matrix
    half = size_in / 12 * 9.0 / 2
    cx, cy = 1200 + dx_in / 12 * 9.0, 600.0
    if ring:  # a pier outline round the column, as S1.101 draws one
        page.draw_rect(fitz.Rect(fitz.Rect(cx - 18, cy - 18, cx + 18, cy + 18) * m).normalize(), color=(0, 0, 0), width=1)
    page.draw_rect(fitz.Rect(fitz.Rect(cx - half, cy - half, cx + half, cy + half) * m).normalize(),
                   color=(0, 0, 0), fill=(0.7, 0.7, 0.7), width=1)
    for y in (cy - half - 2, cy + half + 2):
        page.draw_line(fitz.Point(1100, y) * m, fitz.Point(1300, y) * m, color=(0, 0, 0), width=2)
    return doc


class _Pages:
    def __init__(self, a, b):
        self.docs = {"a": a, "b": b}

    def page(self, document_id, page_number):
        return self.docs[document_id][page_number - 1]


FACTS_A = {"grid": {"x": {"D": 900.0, "E": 1200.0}, "y": {"4": 600.0, "3.7": 680.0}}, "scales": [9.0]}
FACTS_B = {"grid": {"x": {"D": 1046.0, "E": 1346.0}, "y": {"4": 630.0, "3.7": 710.0}}, "scales": [9.0]}


@pytest.mark.parametrize("rotation", [0, 90])
def test_a_column_both_sheets_centre_on_one_crossing_is_rejected(rotation):
    """The C-13 claim, measured: both sheets centre a 22x22 column on E/4."""
    a = _sheet_with_column(rotation)
    b = _sheet_with_column(0)
    # Move B's drawing to where its grid puts E/4.
    b2 = fitz.open()
    pb = b2.new_page(width=2592, height=1728)
    pb.show_pdf_page(fitz.Rect(146, 30, 2592 + 146, 1728 + 30), b, 0)
    issue = _issue(element="column C-13", whatA="column with its west face flush on the grid line",
                   whatB="column centered on the grid line", boxA=_frac(WIN_A, 1200, 600), boxB=_frac(WIN_B, 1346, 630))
    why = fr.column_position_agrees(_Pages(a, b2), issue, {"a": WIN_A, "b": WIN_B}, (FACTS_A, FACTS_B))
    assert why and "same place" in why and "on grid line E and on grid line 4" in why


def test_a_column_that_really_moved_is_left_for_the_model():
    a = _sheet_with_column(0)
    b = fitz.open()
    pb = b.new_page(width=2592, height=1728)
    pb.show_pdf_page(fitz.Rect(146, 30, 2592 + 146, 1728 + 30), _sheet_with_column(0, dx_in=11), 0)
    issue = _issue(element="column C-13", whatA="column centered on the grid line", whatB="column face on the grid line",
                   boxA=_frac(WIN_A, 1200, 600), boxB=_frac(WIN_B, 1354, 630))
    assert fr.column_position_agrees(_Pages(a, b), issue, {"a": WIN_A, "b": WIN_B}, (FACTS_A, FACTS_B)) is None


def test_the_column_inside_a_pier_outline_is_what_is_measured():
    import column_locate

    page = _sheet_with_column(0, ring=True)[0]
    body, why = column_locate.body_at(page, (1200, 600), 18, 9.0)
    assert why is None and round(body.width / 9 * 12) == 22


def test_nothing_measurable_decides_nothing():
    empty = fitz.open()
    empty.new_page(width=2592, height=1728)
    issue = _issue(element="column", whatA="column offset east", whatB="column on the grid line",
                   boxA=_frac(WIN_A, 1200, 600), boxB=_frac(WIN_B, 1346, 630))
    assert fr.column_position_agrees(_Pages(_sheet_with_column(0), empty), issue, {"a": WIN_A, "b": WIN_B}, (FACTS_A, FACTS_B)) is None
    assert fr.column_position_agrees(_Pages(_sheet_with_column(0), empty), issue, {"a": WIN_A, "b": WIN_B}, (FACTS_A, None)) is None


# --- the third real scan, read from its diagnostic export (6 Oct 2026) ------------------
#
# Nothing in it was an API failure; every defect below was the application's.


def test_a_box_in_mixed_units_is_refused_not_guessed():
    """The C-25 candidate's box, verbatim: x as fractions, y as thousandths.
    Dividing all four by 1000 moved the close-up to the image's left edge,
    where it found C-13 and rejected C-25 for a mark it was never shown."""
    box, why = fr.read_box([0.785, 575, 0.835, 606])
    assert box is None and "mixes fractions" in why
    assert fr._box([0.785, 575, 0.835, 606]) is None


@pytest.mark.parametrize("value, expected", [
    ([0.1, 0.2, 0.3, 0.4], [0.1, 0.2, 0.3, 0.4]),
    ([100, 200, 300, 400], [0.1, 0.2, 0.3, 0.4]),
    ([0, 575, 1, 606], [0.0, 0.575, 0.001, 0.606]),  # 0 and 1 are whole thousandths
])
def test_one_unit_boxes_still_read(value, expected):
    assert fr.read_box(value) == (pytest.approx(expected), None)


def test_an_unplaceable_problem_is_recorded_not_lost():
    look = fr.parse_first_look(json.dumps({"status": "issues", "issues": [_issue(boxB=[0.785, 575, 0.835, 606])]}))
    assert look["issues"] == []
    assert look["outcome"] == "invalid_location"
    assert look["dropped"][0]["reason"] == "invalid_location" and "boxB" in look["dropped"][0]["detail"]
    assert look["dropped"][0]["item"]["boxB"] == [0.785, 575, 0.835, 606]


@pytest.mark.parametrize("reply, outcome", [
    ({"status": "agree", "issues": []}, "agree"),
    ({"status": "unclear", "note": "the column is cut off", "issues": []}, "unclear"),
    ({"status": "misaligned", "issues": []}, "misaligned"),
    ({"issues": []}, "unstated"),  # never assumed to agree
    ({"status": "agree", "issues": [_issue()]}, "issues"),  # a listed issue wins over the word
])
def test_the_area_outcome_keeps_unsure_apart_from_agree(reply, outcome):
    look = fr.parse_first_look(json.dumps(reply))
    assert look["outcome"] == outcome
    assert fr.parse_issues(json.dumps(reply)) == look["issues"]


def test_the_note_on_an_unclear_area_is_kept():
    assert fr.parse_first_look('{"status": "unclear", "note": "too small", "issues": []}')["note"] == "too small"


class _StubRun:
    per_discovery = 1000

    def __init__(self, replies, room=10**9):
        self.replies, self.asked, self._room = list(replies), [], room

    def room(self, in_flight=0):
        return self._room

    def ask(self, stage, system, user, images, labels, max_tokens, thinking, trace=None):
        self.asked.append(user)
        text = self.replies.pop(0)
        return type("Reply", (), {"text": text})()


def test_an_unplaceable_box_gets_one_request_to_restate_it():
    bad = fr.parse_first_look(json.dumps({"status": "issues", "issues": [_issue(boxA=[0.785, 575, 0.835, 606])]}))
    run = _StubRun([json.dumps({"status": "issues", "issues": [_issue(boxA=[0.785, 0.575, 0.835, 0.606])]})])
    fixed = fr.repair_boxes(run, "sys", "user", [], [], bad, None)
    assert len(run.asked) == 1 and "fractions 0-1" in run.asked[0] and "0.785" in run.asked[0]
    assert fixed["outcome"] == "issues" and fixed["issues"][0]["boxA"] == [0.785, 0.575, 0.835, 0.606]
    assert fixed["dropped"][0]["repairAsked"] is True  # the first answer stays on the record


def test_the_box_request_is_asked_once_and_only_with_room():
    bad = fr.parse_first_look(json.dumps({"issues": [_issue(boxA=[0.785, 575, 0.835, 606])]}))
    still_bad = _StubRun([json.dumps({"issues": [_issue(boxA=[0.785, 575, 0.835, 606])]})])
    again = fr.repair_boxes(still_bad, "s", "u", [], [], bad, None)
    assert len(still_bad.asked) == 1 and again["outcome"] == "invalid_location"
    broke = _StubRun([], room=10)
    assert fr.repair_boxes(broke, "s", "u", [], [], bad, None) is bad and broke.asked == []
    fine = fr.parse_first_look(json.dumps({"issues": [_issue()]}))
    assert fr.repair_boxes(_StubRun([]), "s", "u", [], [], fine, None) is fine


def test_two_dimension_strings_feet_apart_can_still_be_one_dimension():
    """Two candidates were rejected as "9-10 ft apart": a dimension string sits
    on its dimension line, and two disciplines draw that line at different
    distances outside the plan."""
    issue = _issue(checkId="G01", element="dimension between grids 3 and 4", whatA="30'-0\"", whatB="29'-6\"",
                   boxA=_frac(WIN_A, 1200, 400), boxB=_frac(WIN_B, 1346, 430 + 90))
    assert fr.is_dimension_claim(issue)
    assert fr.boxes_apart(issue, {"a": WIN_A, "b": WIN_B}, 9.0) is None


def test_an_agreeing_span_does_not_settle_its_segments():
    """Rejected because grids 2 and 3.7 agree — but the finding was about the
    segments 2–2.3 and 3.5–3.7. Here one segment's line is not on sheet B."""
    q = ("A3.34 shows 10'-1\" for 2–2.3 and 6'-1\" for 3.5–3.7 between grid lines 2 and 3.7; "
         "A3.13 shows 10'-0\" and 5'-10\".")
    no_23 = {"grid": {"y": {k: v for k, v in A3_13["grid"]["y"].items() if k != "2.3"}}, "scales": [9.0]}
    assert ("2", "2.3") in fr.named_pairs(q) and ("3.5", "3.7") in fr.named_pairs(q)
    assert fr.grid_spacing_agrees(q, A3_34, no_23) is None
    differs = {"grid": {"y": {**A3_13["grid"]["y"], "3.5": 769.9}}, "scales": [9.0]}
    assert fr.grid_spacing_agrees(q, A3_34, differs) is None
    why = fr.grid_spacing_agrees(q, A3_34, A3_13)  # every pair measured and agreeing
    assert why and why.count("apart on both sheets") == 3 and "2 and 2.3" in why and "3.5 and 3.7" in why


def test_a_column_mark_is_not_a_grid_segment():
    assert fr.named_pairs("C-13 at E/4 is 22x22 on A3.05") == []


def test_the_close_look_can_say_unclear():
    verdict = fr.parse_verdict('{"decision": "unclear", "reason": "the column is cut off"}')
    assert verdict["decision"] == "unclear"
    pair = {"a": {"level": "1"}, "b": {"level": "1"}}
    assert fr.rule_out(pair, verdict, "") == "the column is cut off"


def test_the_prompts_no_longer_turn_unsure_into_no_issue():
    first, close = fr.discovery_system(), fr.verify_system()
    assert "never second-guess" not in first
    assert "or you are not sure, return no issue" not in first
    assert "few inches" not in first and "3 inches" in first
    for word in ('"unclear"', '"misaligned"', '"agree"'):
        assert word in first
    assert '"unclear"' in close and "or you cannot tell" not in close
    assert "never pixels or thousandths" in first


def test_the_summary_tells_nothing_new_from_nothing_wrong():
    """The real run: 20 possible problems, 5 rejected before the close look,
    13 on it, one kept and rejected by a rule, one kept and already dismissed.
    "0 findings" alone said none of that."""
    dismissed = "dismissed earlier — restore it under “Dismissed” in Needs your review to accept it"
    tiles = [
        ("done", "issues", [{"verdict": {"decision": "reject", "reason": "x"}}] * 18
         + [{"verdict": {"decision": "keep", "subject": "C-10.S", "foundAgain": dismissed, "fingerprint": "fp1"}},
            {"verdict": {"decision": "unclear", "reason": "cut off"}}], []),
        ("done", "agree", [], []),
        ("done", "unclear", [], []),
        ("done", "misaligned", [], []),
        ("done", None, [], []),
        ("done", "invalid_location", [], [{"reason": "invalid_location"}]),
        ("done", "issues", [_issue()], [{"reason": "invalid_location", "repairAsked": True}]),
        ("failed", None, [], []),
        ("pending", None, [], []),
    ]
    s = fr.scan_summary(tiles, 0, 48, 423)
    assert s["newFindings"] == 0
    assert s["foundAgain"] == [{"subject": "C-10.S", "where": dismissed, "fingerprint": "fp1"}]
    assert (s["possibleProblems"], s["rejected"], s["unclear"], s["notChecked"]) == (21, 18, 1, 1)
    assert s["unplaceable"] == 1  # the repaired one is not counted as lost
    assert s["areas"] == {"issues": 2, "agree": 1, "unclear": 1, "misaligned": 1, "unstated": 1,
                          "invalid_location": 1, "failed": 1, "pending": 1}
    assert (s["areasTotal"], s["pagesCompared"], s["pagesRead"]) == (9, 48, 423)


# The same export, replayed: all 498 first-look replies were parsed again, and
# exactly three issues had a location that could not be trusted — the mixed-unit
# C-25 above and these two, each one box written x-first on one image and
# y-first on the other (Gemini's trained box order is [ymin, xmin, ymax, xmax]).
@pytest.mark.parametrize("box_a, box_b", [
    ([409, 731, 434, 760], [731, 409, 762, 434]),  # C-1, A3.22 / S2.102: "38 ft apart"
    ([458, 118, 488, 149], [117, 458, 150, 488]),  # C-1, A3.24 / S2.105: "40 ft apart"
])
def test_a_box_pair_written_two_ways_is_asked_again_not_measured(box_a, box_b):
    look = fr.parse_first_look(json.dumps({"issues": [_issue(boxA=box_a, boxB=box_b)]}))
    assert look["issues"] == [] and look["outcome"] == "invalid_location"
    assert "x and y swapped" in look["dropped"][0]["detail"]
    assert fr.box_repair_prompt(look) is not None


def test_boxes_on_the_diagonal_are_not_called_swapped():
    assert not fr.transposed_pair([0.40, 0.41, 0.45, 0.46], [0.41, 0.40, 0.46, 0.45])
    assert not fr.transposed_pair([0.1, 0.2, 0.3, 0.4], [0.1, 0.2, 0.3, 0.4])


@pytest.mark.parametrize("box", [
    {"left": 0.1, "top": 0.2, "right": 0.3, "bottom": 0.4},
    {"x0": 0.1, "y0": 0.2, "x1": 0.3, "y1": 0.4},
    {"left": 100, "top": 200, "right": 300, "bottom": 400},
])
def test_named_edges_read_whatever_order_the_keys_come_in(box):
    flipped = dict(reversed(list(box.items())))
    assert fr.read_box(box) == fr.read_box(flipped) == (pytest.approx([0.1, 0.2, 0.3, 0.4]), None)


def test_a_box_object_missing_an_edge_is_refused():
    box, why = fr.read_box({"left": 0.1, "top": 0.2, "right": 0.3})
    assert box is None and "four edges" in why


def test_the_prompt_asks_for_named_edges():
    assert '"left"' in fr.discovery_system() and '"bottom"' in fr.discovery_system()


def test_an_empty_provider_account_is_said_plainly():
    """Verbatim from a real scan that stopped on Gemini's prepaid credit."""
    raw = ("402 RESOURCE_EXHAUSTED. {'error': {'code': 402, 'message': 'Your prepayment credits are depleted. "
           "Please go to AI Studio at https://ai.studio/projects to manage your project and billing.', "
           "'status': 'RESOURCE_EXHAUSTED'}}")
    msg = fr.provider_failure_message(Exception(raw))
    assert msg.startswith("the AI provider refused the request because the account has no credit left")
    assert "press Resume" in msg and "already checked is saved" in msg
    claude = fr.provider_failure_message(Exception("Your credit balance is too low to access the Anthropic API."))
    assert "no credit left" in claude


@pytest.mark.parametrize("raw, says", [
    ("429 RESOURCE_EXHAUSTED. Quota exceeded for metric generate_content_requests", "rate limit or quota"),
    ("429 RESOURCE_EXHAUSTED. You exceeded your current quota, please check your plan and billing details.",
     "rate limit or quota"),  # mentions billing, and is still a quota
    ("401 Unauthorized: invalid x-api-key", "rejected the API key"),
])
def test_other_account_errors_say_what_to_do(raw, says):
    assert says in fr.provider_failure_message(Exception(raw))


def test_an_ordinary_error_is_left_as_it_is():
    assert fr.provider_failure_message(ValueError("page 12 could not be opened")) == "page 12 could not be opened"


def test_a_height_claimed_from_a_plan_needs_printed_evidence():
    """A reviewer rejected a JETRIGHT package that read "multiple horizontal
    segments at different elevations" off pale lines on a mezzanine plan."""
    claim = "P1.03 shows the edge as multiple horizontal segments at different elevations."
    words = '1/2"ø G TO RTU-4 80MBH OFFICE 202 CORRIDOR 203'
    assert "height or elevation" in fr.level_claim_unsupported(claim, "1/2 G TO RTU-4 80MBH OFFICE 202")
    assert fr.level_claim_unsupported(claim, 'SLAB STEP 1\'-0" T.O.S. EL. 100\'-0"') is None
    assert fr.level_claim_unsupported("Column C-3 is missing on E2.02.", words) is None
    # "Level 1" is a floor's name, not a claim about height.
    assert fr.level_claim_unsupported("Column C-3 on Level 1 is missing.", words) is None


def test_both_looks_are_told_background_linework_is_not_design():
    for prompt in (fr.discovery_system(), fr.verify_system()):
        assert "BACKGROUND linework" in prompt and "moves in plan, not up" in prompt
