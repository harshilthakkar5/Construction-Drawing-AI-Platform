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


# --- The same whole-sheet pictures, cached across a set's element reviews ---------------------


def test_the_shared_opening_is_the_same_for_every_question_set():
    # The cache is a prefix match: the instructions before the pictures must not vary.
    assert rfi_review.discovery_system() == rfi_review.discovery_system()
    question = rfi_review.CHECKS["C01"]["originalQuestion"]
    assert question not in rfi_review.discovery_system() and question in rfi_review.discovery_questions(["C01"])


def test_whole_sheets_open_the_message_in_one_order_with_labels_no_review_owns():
    a_crop, a_s2, a_s1 = _pic("ev1", b"crop-a"), _pic("ev2", b"S2", page=2, kind="page"), _pic("ev3", b"S1", page=1, kind="page")
    for e, sheet in ((a_s2, "S1.111"), (a_s1, "S1.101"), (a_crop, "S1.101")):
        e.sheet_number = sheet
    ordered, labels, preface, n = rfi_review.sheets_first([a_crop, a_s2, a_s1])
    assert [e.id for e in ordered] == ["ev3", "ev2", "ev1"] and n == 2
    assert labels[:2] == ["Whole sheet 1 of 2: S1.101", "Whole sheet 2 of 2: S1.111"]
    assert "whole sheet 1 = ev3 (S1.101)" in preface and "whole sheet 2 = ev2 (S1.111)" in preface
    # Another review numbers the same sheets differently: its opening is still identical.
    b_s1, b_s2 = _pic("ev9", b"S1", page=1, kind="page"), _pic("ev4", b"S2", page=2, kind="page")
    b_s1.sheet_number, b_s2.sheet_number = "S1.101", "S1.111"
    b_ordered, b_labels, _, _ = rfi_review.sheets_first([b_s2, b_s1, _pic("ev5", b"crop-b")])
    assert [e.image for e in b_ordered[:2]] == [e.image for e in ordered[:2]] and b_labels[:2] == labels[:2]
    # No whole sheets: exactly what was sent before.
    crops = [_pic("ev1", b"c")]
    assert rfi_review.sheets_first(crops) == (crops, [crops[0].label()], "", 0)


def test_the_cache_mark_is_sent_only_when_asked_and_reaches_the_transport(monkeypatch):
    import llm
    sent = []
    monkeypatch.setattr(llm, "complete", lambda system, prompt, **kw: sent.append((prompt, kw)) or llm.Reply(
        text="{}", stop_reason="end_turn", model="stub", input_tokens=1, output_tokens=1))
    run = {"id": "r1", "projectId": "p", "limits": {}}
    usage = rfi_review.ReviewUsage("claude", "claude-sonnet-5-5", "medium")
    sheet = _pic("ev2", b"S1", kind="page")
    for cache in (False, True):
        rfi_review._ask("discovery", rfi_review.discovery_system(), "body", lambda raw: {}, run=run, usage=usage,
                        images=[_pic("ev1", b"crop"), sheet], sheets_first_order=True, cache_sheets=cache)
    (plain, kw_plain), (cached, kw_cached) = sent
    assert "cache_images" not in kw_plain and kw_cached["cache_images"] == 1 and kw_cached["cache_ttl"] is None
    assert kw_cached["images"] == [b"S1", b"crop"] and plain.startswith("The whole-sheet pictures")
    # A batch waits minutes before it runs: it asks for the hour-long cache.
    monkeypatch.setattr(llm, "complete_batch", lambda prompts, **kw: sent.append(("batch", kw)) or kw["replies"].update(
        call=llm.Reply(text="{}", stop_reason="end_turn", model="stub", input_tokens=1, output_tokens=1)))
    rfi_review._ask("discovery", rfi_review.discovery_system(), "body", lambda raw: {}, run=dict(run, limits={"useBatch": True}),
                    usage=usage, images=[sheet], sheets_first_order=True, cache_sheets=True)
    assert sent[-1][1]["cache_images"] == 1 and sent[-1][1]["cache_ttl"] == "1h"


def test_a_set_read_alone_reads_the_most_sheeted_element_first_then_the_rest_with_the_cache(monkeypatch):
    import threading
    pits = _prep("run-pits", "Elevator pits", ["P1"], [_pic("ev1", b"S1", kind="page")])
    grid = _prep("run-grid", "Grid lines", ["G1"], [_pic("ev1", b"S1", kind="page"), _pic("ev2", b"S2", page=2, kind="page")])
    caps = _prep("run-caps", "Pile caps", ["C1"], [_pic("ev1", b"S1", kind="page")])
    order, first_done = [], threading.Event()
    def alone(p, cache=False):
        assert cache, "every reading of a set marks its sheets for the cache"
        if order:
            assert first_done.is_set(), "the others wait until the first reading has landed"
        order.append(p.run_id)
        if p.run_id == "run-grid":
            first_done.set()
        return {**_found(), "notes": [], "calls": 1}
    monkeypatch.setattr(rfi_review, "_discover_alone", alone)
    shares = rfi_review._discover_in_turn([pits, grid, caps])
    assert order[0] == "run-grid" and sorted(order[1:]) == ["run-caps", "run-pits"]
    assert "Read after Grid lines" in shares[0]["notes"][0] and shares[1]["notes"] == []


def test_one_element_s_failed_reading_fails_only_that_element(monkeypatch):
    a, b = _prep("a", "A", ["Q"], []), _prep("b", "B", ["Q"], [])
    def alone(p, cache=False):
        if p.run_id == "b":
            raise rfi_review.StageFailed("discovery", "provider down")
        return {**_found(), "notes": [], "calls": 1}
    monkeypatch.setattr(rfi_review, "_discover_alone", alone)
    out = rfi_review._discover_in_turn([a, b])
    assert isinstance(out[1], rfi_review.StageFailed) and not isinstance(out[0], BaseException)


def test_one_hour_cache_writes_are_priced_at_two_times_input():
    import usage
    from types import SimpleNamespace as NS
    plain = NS(cache_creation_input_tokens=1000, cache_creation=NS(ephemeral_1h_input_tokens=0, ephemeral_5m_input_tokens=1000))
    hour = NS(cache_creation_input_tokens=1000, cache_creation=NS(ephemeral_1h_input_tokens=1000, ephemeral_5m_input_tokens=0))
    assert usage.cache_write_tokens(plain) == 1000
    # Priced at 1.25x per write token everywhere: 1600 x 1.25 = 2000 = 1000 at 2x.
    assert usage.cache_write_tokens(hour) == 1600
    assert usage.cache_write_tokens(NS(cache_creation_input_tokens=0)) == 0


# --- One RFI, one set of pictures, released when its work ends ----------------------------------


def _held(run_id, name, pictures):
    p = _prep(run_id, name, ["Q"], pictures)
    p.notes, p.sent, p.batch_of = [], list(pictures), {e.id: 1 for e in pictures}
    p.batches = [list(pictures)]
    return p


def test_every_element_of_an_rfi_opens_with_the_same_whole_sheets():
    s1, s2 = _pic("ev1", b"S1", page=1, kind="page"), _pic("ev2", b"S2", page=2, kind="page")
    s1.sheet_number, s2.sheet_number = "S1.101", "S1.111"
    grid = _held("g", "Grid lines", [s1, s2, _pic("ev3", b"crop-grid")])
    caps = _held("c", "Pile caps", [_pic("ev1", b"S1", page=1, kind="page")])
    assert rfi_review.share_whole_sheets([grid, caps]) == 1
    # Pile caps now holds S1.111 too, under an id of its own, in its first call.
    added = [e for e in caps.evidence.values() if e.image == b"S2"]
    assert len(added) == 1 and added[0].id == "ev2" and added[0] in caps.batches[0] and caps.batch_of["ev2"] == 1
    assert "Also shown 1 whole sheet(s)" in caps.notes[0] and grid.notes == []
    # So both open with the same pictures in the same order.
    first = [rfi_review.sheets_first([e for e in p.batches[0] if e.visual])[0][:2] for p in (grid, caps)]
    assert [e.image for e in first[0]] == [e.image for e in first[1]] == [b"S1", b"S2"]
    # Close-ups are each element's own; nothing is added twice.
    assert not any(e.image == b"crop-grid" for e in caps.evidence.values())
    assert rfi_review.share_whole_sheets([grid, caps]) == 0


def test_a_picture_is_rendered_once_for_the_rfi_and_released_when_it_ends(monkeypatch):
    renders = []
    def run_group_body(group, results):
        for _ in group:
            rfi_review.set_picture(("page", "d", 1, 1600), lambda: renders.append(1) or b"png")
        held.update(rfi_review._set_pictures.get())
        raise RuntimeError("a review failed")
    held = {}
    monkeypatch.setattr(rfi_review, "_run_group_with_pictures", run_group_body)
    try:
        rfi_review._run_group([{"id": "a"}, {"id": "b"}, {"id": "c"}])
    except RuntimeError:
        pass
    assert len(renders) == 1 and len(held) == 1
    # Released whether the work finished or failed; outside a set nothing is kept.
    assert rfi_review._set_pictures.get() is None
    rfi_review.set_picture(("page", "d", 1, 1600), lambda: renders.append(1) or b"png")
    assert len(renders) == 2


def test_an_hour_long_picture_cache_gives_the_system_block_the_same_hour():
    import llm
    # Anthropic refuses a 1-hour mark after a 5-minute one, and system comes first.
    assert llm._system_blocks("rules", True, "1h")[0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert llm._system_blocks("rules", True)[0]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in llm._system_blocks("rules", False, "1h")[0]


def test_a_refused_request_fails_that_element_and_not_the_set_s_job(monkeypatch):
    a, b = _prep("a", "A", ["Q"], []), _prep("b", "B", ["Q"], [])
    def alone(p, cache=False):
        if p.run_id == "a":
            raise RuntimeError("Error code: 400 - invalid_request_error")
        return {**_found(), "notes": [], "calls": 1}
    monkeypatch.setattr(rfi_review, "_discover_alone", alone)
    out = rfi_review._discover_in_turn([a, b])
    assert isinstance(out[0], rfi_review.StageFailed) and "400" in str(out[0]) and not isinstance(out[1], BaseException)


def test_a_named_rfis_elements_finish_side_by_side_and_one_failure_keeps_the_others(monkeypatch):
    """After the shared reading each element's reasoning, verification and re-check need nothing from
    its siblings; one after another, three elements waited ~22 minutes of batch queues."""
    import threading
    import time as _time
    import contextlib

    group = [{"id": f"run-{i}", "checkIds": []} for i in range(3)]
    preps = [rfi_review.SimpleNamespace(run_id=r["id"], run=r) for r in group]
    monkeypatch.setattr(rfi_review, "_prepare", lambda r: preps[group.index(r)])
    monkeypatch.setattr(rfi_review, "share_whole_sheets", lambda ps: 0)
    monkeypatch.setattr(rfi_review, "_discover_in_turn", lambda ps: [{"observations": []}] * len(ps))
    monkeypatch.setattr(rfi_review, "_heartbeat", lambda *ids: contextlib.nullcontext())
    monkeypatch.setattr(rfi_review, "SHARED_SET_READING", False)
    monkeypatch.setattr(rfi_review, "_set", lambda *a, **k: None)
    inside, most = [0], [0]
    lock = threading.Lock()
    marker = rfi_review.contextvars.ContextVar("marker", default=None)
    marker.set("from the job")
    seen = []

    def finish(p, discover):
        with lock:
            inside[0] += 1
            most[0] = max(most[0], inside[0])
        seen.append(marker.get())  # each thread runs in a copy of the job's context
        _time.sleep(0.2)
        with lock:
            inside[0] -= 1
        if p.run_id == "run-1":
            raise RuntimeError("verification blew up")
        return {"saved": p.run_id, "share": discover()}

    monkeypatch.setattr(rfi_review, "_finish", finish)
    out = rfi_review._run_group_with_pictures(group, {})["set"]
    assert most[0] == 3 and seen == ["from the job"] * 3
    assert out["run-0"]["saved"] == "run-0" and out["run-2"]["saved"] == "run-2"
    assert "failed" in out["run-1"]


def test_the_see_plan_dimensions_are_read_once_per_sheet(monkeypatch):
    import page_memo, review_aids
    calls = []
    monkeypatch.setattr(review_aids, "deferred_dimensions", lambda page, sheet: calls.append(sheet) or f"{sheet}: 1 dimension")
    class Page: pass
    a, b = page_memo.tag(Page(), "doc-x", 7), page_memo.tag(Page(), "doc-x", 7)
    name = "deferred_dimensions@1:S4.10"
    first = page_memo.memo(name, a, lambda: review_aids.deferred_dimensions(a, "S4.10"))
    second = page_memo.memo(name, b, lambda: review_aids.deferred_dimensions(b, "S4.10"))
    assert first == second == "S4.10: 1 dimension" and calls == ["S4.10"]
