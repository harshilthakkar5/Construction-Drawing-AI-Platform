"""One reading for a named RFI's elements: the first review of a set to start
reads every waiting sibling's sheets together, once, and each review keeps
its own questions' share. Every model here is a stub: these tests say what
the code does with a reply, never how well a real model reads drawings."""

import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import rfi_review  # noqa: E402
from test_rfi_review import FakeModel, _candidates, _row, _seed, database, ev, needs_db  # noqa: E402,F401


def _pic(eid, data, page=1, kind="crop"):
    e = ev(eid, kind, page=page)
    e.image = data
    return e


def test_one_copy_of_each_shared_piece_and_each_review_keeps_its_own_ids():
    a_text, a_sheet, a_crop = ev("ev1", "text", page=1), _pic("ev2", b"sheet", kind="page"), _pic("ev3", b"crop-pits")
    b_text, b_sheet, b_crop = ev("ev1", "text", page=1), _pic("ev2", b"sheet", kind="page"), _pic("ev3", b"crop-caps")
    b_text.chunk_id = a_text.chunk_id  # the same chunk, held by both reviews
    main, first, maps = rfi_review.shared_pool([([a_text, a_sheet, a_crop], []), ([b_text, b_sheet, b_crop], [])])
    # The chunk and the whole sheet are read once; the two close-ups are different pictures.
    assert [e.id for e in main] == ["ev1", "ev2", "ev3", "ev4"] and first == []
    assert maps[0] == {"ev1": "ev1", "ev2": "ev2", "ev3": "ev3"}
    assert maps[1] == {"ev1": "ev1", "ev2": "ev2", "ev4": "ev3"}


def test_each_review_gets_only_its_questions_with_its_own_evidence_ids():
    pool = {"ev1": ev("ev1", "text"), "ev2": _pic("ev2", b"x"), "ev9": _pic("ev9", b"other element's crop")}
    own_ev = {"ev5": ev("ev5", "text"), "ev6": _pic("ev6", b"x")}
    p = rfi_review.SimpleNamespace(asked=["CE-1-Q1", "G01"], evidence=dict(own_ev), batches=[], sent=[], unread=[], batch_of={})
    found = {
        "observations": [
            {"checkId": "CE-1-Q1", "statement": "pit slab 8 in", "evidenceIds": ["ev1", "ev9"]},
            {"checkId": "CE-2-Q1", "statement": "pile cap PC1", "evidenceIds": ["ev2"]},
            {"checkId": "G01", "statement": "grid 3", "evidenceIds": ["ev2"]},
        ],
        "inventory": [{"checkId": "CE-1-Q1", "entity": "pit", "fields": [{"name": "t", "evidenceIds": ["ev1"]}]},
                      {"checkId": "CE-2-Q1", "entity": "cap", "fields": []}],
        "gaps": [{"checkId": "CE-2-Q1", "field": "size", "reason": "x"}],
        "notApplicable": [],
    }
    batches = [[pool["ev1"], pool["ev2"], pool["ev9"]]]
    share = rfi_review.split_reading(p, found, {"ev1": "ev5", "ev2": "ev6"}, pool, batches, [], 2, "Elevator pits")
    assert [o["checkId"] for o in share["observations"]] == ["CE-1-Q1", "G01"]
    # The other element's close-up it cited is copied in under a new id of this review's.
    assert share["observations"][0]["evidenceIds"] == ["ev5", "ev7"] and p.evidence["ev7"].image == b"other element's crop"
    assert share["inventory"][0]["fields"][0]["evidenceIds"] == ["ev5"] and len(share["inventory"]) == 1
    assert share["gaps"] == [] and share["calls"] == 1
    # It was in the shared call, so it counts as read by this review too.
    assert [e.id for e in p.sent] == ["ev5", "ev6", "ev7"] and p.batch_of == {"ev5": 1, "ev6": 1, "ev7": 1}
    assert "billed on the review of Elevator pits" in share["notes"][0]


def _second_run_in_set(db, seeded, checks=("C01",)) -> str:
    set_id, other = str(uuid.uuid4()), str(uuid.uuid4())
    with db.connect() as conn:
        conn.execute(
            'INSERT INTO rfi_sets (id, "projectId", name, sheets, items, "updatedAt") VALUES (%s, %s, %s, %s, %s, now())',
            (set_id, seeded["project"], "Level 1", json.dumps(["S2.105", "A3.01"]), json.dumps([])),
        )
        conn.execute(
            'INSERT INTO rfi_review_runs (id, "projectId", "createdById", target, "checkIds", status, provider, '
            '"thinkingRequested", scope, "scopeHash", "setId") '
            "SELECT %s, \"projectId\", \"createdById\", target, %s, 'queued', provider, \"thinkingRequested\", scope, 'h2', %s "
            "FROM rfi_review_runs WHERE id = %s",
            (other, json.dumps(list(checks)), set_id, seeded["run"]),
        )
        conn.execute('UPDATE rfi_review_runs SET "setId" = %s WHERE id = %s', (set_id, seeded["run"]))
    return other


@needs_db
def test_a_set_reads_its_sheets_once_and_finishes_every_element(database, monkeypatch):
    import llm

    seeded = _seed(database, checks=("C01",))
    other = _second_run_in_set(database, seeded)
    model = FakeModel()
    monkeypatch.setattr(llm, "complete", model)

    result = rfi_review.run(seeded["run"])
    stages = [c["stage"] for c in model.calls]
    # ONE reading for both reviews, then each reasons and verifies on its own.
    assert stages.count("discovery") == 1 and stages.count("reasoning") == 2 and stages.count("verification") == 2
    assert set(result["set"]) == {seeded["run"], other}
    lead, follower = _row(database, seeded["run"]), _row(database, other)
    assert lead[0] == "ready" and follower[0] == "ready"
    assert lead[3]["stages"]["discovery"]["calls"] == 1 and "discovery" not in follower[3]["stages"]
    assert any("Read together with 1 other element" in n for n in follower[4])
    # Both found the same problem: saved once, found again by the second.
    assert len([r for r in _candidates(database, seeded["project"]) if r[0] == "C01"]) == 1
    # The sibling's own job, arriving later, has nothing left to do.
    assert rfi_review.run(other) == {"skipped": "ready"}


@needs_db
def test_a_sibling_held_by_a_live_worker_is_not_read_twice(database, monkeypatch):
    import llm

    seeded = _seed(database, checks=("C01",))
    other = _second_run_in_set(database, seeded)
    with database.connect() as conn:  # another worker started it a moment ago
        conn.execute('UPDATE rfi_review_runs SET status = \'running\', "heartbeatAt" = now() WHERE id = %s', (other,))
    model = FakeModel()
    monkeypatch.setattr(llm, "complete", model)
    result = rfi_review.run(seeded["run"])
    assert "set" not in result and [c["stage"] for c in model.calls] == ["discovery", "reasoning", "verification"]
    assert rfi_review.run(other) == {"skipped": "being reviewed with its set"}


@needs_db
def test_a_failed_shared_reading_fails_every_element_it_served(database, monkeypatch):
    import llm

    seeded = _seed(database, checks=("C01",))
    other = _second_run_in_set(database, seeded)
    monkeypatch.setattr(llm, "complete", FakeModel(fail_stage="discovery"))
    rfi_review.run(seeded["run"])
    assert _row(database, seeded["run"])[:2] == ("failed", "discovery")
    assert _row(database, other)[:2] == ("failed", "discovery")
    assert _candidates(database, seeded["project"]) == []


@needs_db
def test_switched_off_each_element_reads_alone(database, monkeypatch):
    import llm

    seeded = _seed(database, checks=("C01",))
    other = _second_run_in_set(database, seeded)
    monkeypatch.setattr(rfi_review, "SHARED_SET_READING", False)
    model = FakeModel()
    monkeypatch.setattr(llm, "complete", model)
    rfi_review.run(seeded["run"])
    rfi_review.run(other)
    assert [c["stage"] for c in model.calls].count("discovery") == 2
