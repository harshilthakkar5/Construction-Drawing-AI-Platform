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

    monkeypatch.setattr(rfi_review, "SHARED_SET_READING", True)  # off by default

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

    monkeypatch.setattr(rfi_review, "SHARED_SET_READING", True)  # off by default

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

    monkeypatch.setattr(rfi_review, "SHARED_SET_READING", True)  # off by default

    seeded = _seed(database, checks=("C01",))
    other = _second_run_in_set(database, seeded)
    monkeypatch.setattr(llm, "complete", FakeModel(fail_stage="discovery"))
    rfi_review.run(seeded["run"])
    assert _row(database, seeded["run"])[:2] == ("failed", "discovery")
    assert _row(database, other)[:2] == ("failed", "discovery")
    assert _candidates(database, seeded["project"]) == []


@needs_db
def test_switched_off_each_element_reads_alone(database, monkeypatch):  # the default
    import llm

    seeded = _seed(database, checks=("C01",))
    other = _second_run_in_set(database, seeded)
    monkeypatch.setattr(rfi_review, "SHARED_SET_READING", False)
    model = FakeModel()
    monkeypatch.setattr(llm, "complete", model)
    rfi_review.run(seeded["run"])
    rfi_review.run(other)
    assert [c["stage"] for c in model.calls].count("discovery") == 2


# --- An element served thinly by the shared reading is read again alone ---------------------


def _found(*answered):
    return {"observations": [{"checkId": c, "statement": "x", "evidenceIds": []} for c in answered],
            "inventory": [], "gaps": [], "notApplicable": []}


def test_answered_share_counts_any_kind_of_answer_once_per_question():
    found = _found("Q1", "Q1", "OTHER")
    found["gaps"] = [{"checkId": "Q2", "field": "f", "reason": "r"}]
    found["inventory"] = [{"checkId": "Q1", "entity": "pit", "fields": []}]
    assert rfi_review.answered_share(["Q1", "Q2", "Q3", "Q4"], found) == 0.5
    assert rfi_review.answered_share([], found) == 1.0


def test_thin_shares_is_relative_to_the_best_served_sibling():
    found = _found("G1", "G2", "G3", "G4", "P1")
    grid, pits = ["G1", "G2", "G3", "G4", "G5"], ["P1", "P2", "P3", "P4", "P5"]
    # Pits answered on 20% against the grid's 80%: under 0.6 x 80%.
    assert rfi_review.thin_shares([grid, pits], found, ratio=0.6) == [1]
    # Every element answered sparsely alike is no element short-changed.
    assert rfi_review.thin_shares([["G1", "X"] * 5, ["P1", "Y"] * 5], _found("G1", "P1"), ratio=0.6) == []
    # Switched off, a single element, or nothing answered: nothing re-read.
    assert rfi_review.thin_shares([grid, pits], found, ratio=0) == []
    assert rfi_review.thin_shares([pits], found, ratio=0.6) == []
    assert rfi_review.thin_shares([grid, pits], _found(), ratio=0.6) == []


def _prep(run_id, name, asked, evidence):
    return rfi_review.SimpleNamespace(
        run_id=run_id, run={"id": run_id, "limits": {}}, scope={"library": {"element": {"name": name}}},
        asked=asked, catalogue={c: {"label": c, "originalQuestion": c, "requiredObservations": []} for c in asked},
        reading=(evidence, []), max_input=200_000, max_batches=4, already="",
        checked="", usage=None, evidence={e.id: e for e in evidence}, batches=[evidence], sent=[], unread=[],
        batch_of={},
    )


def _shared_reading(monkeypatch, answered):
    grid = _prep("run-grid", "Grid lines", ["G1", "G2", "G3", "G4", "G5"], [ev("ev1", "text", page=1)])
    pits = _prep("run-pits", "Elevator pits", ["P1", "P2", "P3", "P4", "P5"], [ev("ev1", "text", page=2)])
    alone_calls = []
    monkeypatch.setattr(rfi_review, "_ask", lambda *a, **k: _found(*answered))
    monkeypatch.setattr(rfi_review, "_discover_alone", lambda p: alone_calls.append(p.run_id) or {
        **_found("P1", "P2", "P3"), "notes": [], "calls": 1})
    return rfi_review._discover_together([grid, pits]), alone_calls


def test_a_thinly_served_element_is_read_again_on_its_own(monkeypatch):
    shares, alone = _shared_reading(monkeypatch, ["G1", "G2", "G3", "G4", "P1"])
    assert alone == ["run-pits"]
    assert [o["checkId"] for o in shares[1]["observations"]] == ["P1", "P2", "P3"]
    assert "read again on its own" in shares[1]["notes"][0] and "20%" in shares[1]["notes"][0]
    # The well-served element keeps its share of the one reading.
    assert [o["checkId"] for o in shares[0]["observations"]] == ["G1", "G2", "G3", "G4"]
    assert "Read together" in shares[0]["notes"][0]


def test_an_evenly_served_set_is_read_once(monkeypatch):
    shares, alone = _shared_reading(monkeypatch, ["G1", "G2", "G3", "P1", "P2", "P3"])
    assert alone == [] and all("Read together" in s["notes"][0] for s in shares)


def test_a_failed_reread_keeps_the_thin_share_instead_of_failing_the_review(monkeypatch):
    _, _ = _shared_reading(monkeypatch, ["G1"])  # sets up _ask
    def fail(p):
        raise rfi_review.StageFailed("discovery", "provider down")
    monkeypatch.setattr(rfi_review, "_discover_alone", fail)
    grid = _prep("run-grid", "Grid lines", ["G1", "G2"], [ev("ev1", "text", page=1)])
    pits = _prep("run-pits", "Elevator pits", ["P1", "P2"], [ev("ev1", "text", page=2)])
    shares = rfi_review._discover_together([grid, pits])
    assert not isinstance(shares[1], BaseException)
    assert shares[1]["observations"] == [] and "failed" in shares[1]["notes"][-1]


def _race(monkeypatch, claimed_siblings):
    """This job's own review was taken by another worker's claim at the same
    instant, and this job's claim got `claimed_siblings` instead."""
    import contextlib
    rows = {i: {"id": i, "status": "queued", "setId": "set-1", "projectId": "p"} for i in ["mine", *claimed_siblings]}
    done = []
    monkeypatch.setattr(rfi_review, "load_run", lambda i: rows.get(i))
    monkeypatch.setattr(rfi_review, "_claim_set", lambda lead: [rows[i] for i in claimed_siblings])
    monkeypatch.setattr(rfi_review, "_claim", lambda i: False)
    monkeypatch.setattr(rfi_review, "_diag_start", lambda r: None)
    monkeypatch.setattr(rfi_review, "_heartbeat", lambda *ids: contextlib.nullcontext())
    monkeypatch.setattr(rfi_review, "_run", lambda r: done.append([r["id"]]) or {"ran": r["id"]})
    monkeypatch.setattr(rfi_review, "_run_group", lambda g: done.append([r["id"] for r in g]) or {"set": {}})
    monkeypatch.setattr(rfi_review, "SHARED_SET_READING", True)
    return rfi_review.run("mine"), done


def test_a_job_that_lost_its_own_review_still_reviews_the_siblings_it_claimed(monkeypatch):
    assert _race(monkeypatch, ["pits"]) == ({"ran": "pits"}, [["pits"]])
    assert _race(monkeypatch, ["pits", "caps"])[1] == [["pits", "caps"]]


def test_a_job_that_claimed_nothing_steps_aside(monkeypatch):
    assert _race(monkeypatch, []) == ({"skipped": "being reviewed with its set"}, [])


_TEST_DB = __import__("os").environ.get("RFI_TEST_DATABASE_URL")


@__import__("pytest").fixture
def bare_db(monkeypatch):
    import config
    import db

    monkeypatch.setattr(config, "DATABASE_URL", _TEST_DB)
    monkeypatch.setattr(db, "_pool", None)
    monkeypatch.setattr(db, "_pool_unavailable", True)
    return db


def _waiting_set(db, n=3):
    user, project, set_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    runs = [str(uuid.uuid4()) for _ in range(n)]
    with db.connect() as conn:
        conn.execute("INSERT INTO users (id, email, name, \"passwordHash\") VALUES (%s, %s, 'set test', 'x')",
                     (user, f"{user}@test.invalid"))
        conn.execute('INSERT INTO projects (id, name, "ownerId") VALUES (%s, \'set test\', %s)', (project, user))
        conn.execute('INSERT INTO rfi_sets (id, "projectId", name, sheets, items, "updatedAt") VALUES (%s, %s, %s, %s, %s, now())',
                     (set_id, project, "race", json.dumps(["S1.101"]), json.dumps([])))
        for r in runs:
            conn.execute(
                'INSERT INTO rfi_review_runs (id, "projectId", "createdById", target, "checkIds", status, provider, '
                '"thinkingRequested", scope, "scopeHash", "setId") '
                "VALUES (%s, %s, %s, %s, '[]', 'queued', 'claude', 'medium', '{}', %s, %s)",
                (r, project, user, json.dumps({"type": "sheet", "values": ["S1.101"]}), r, set_id),
            )
    return runs


@__import__("pytest").mark.skipif(not _TEST_DB, reason="set RFI_TEST_DATABASE_URL to a migrated database")
def test_two_jobs_claiming_one_set_at_once_do_not_split_it(bare_db):
    import threading

    runs = _waiting_set(bare_db)
    lead = rfi_review.load_run(runs[0])
    # A claim already in progress elsewhere: hold the set's lock in an open transaction.
    with bare_db.connect() as holder:
        holder.execute("SELECT pg_advisory_xact_lock(%s)", (bare_db._lock_key(f"rfi-set:{lead['setId']}"),))
        got: list = []
        t = threading.Thread(target=lambda: got.append(rfi_review._claim_set(lead)))
        t.start()
        t.join(0.5)
        assert t.is_alive(), "a second claim must wait for the first to commit"
    t.join(5)
    assert sorted(r["id"] for r in got[0]) == sorted(runs)

    # Two claims started together: one takes every review, the other none.
    runs = _waiting_set(bare_db)
    lead = rfi_review.load_run(runs[0])
    gate, results = threading.Barrier(2), []
    def claim():
        gate.wait()
        results.append(len(rfi_review._claim_set(lead)))
    threads = [threading.Thread(target=claim) for _ in range(2)]
    [t.start() for t in threads]
    [t.join(10) for t in threads]
    assert sorted(results) == [0, 3]


def test_reading_together_is_off_unless_asked_for():
    for value, expected in ((None, False), ("", False), ("false", False), ("yes", False),
                            ("true", True), (" TRUE ", True)):
        assert rfi_review.shared_reading_on(value) is expected
