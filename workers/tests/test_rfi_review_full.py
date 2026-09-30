"""The full targeted review (RFI-A): catalogue-driven discovery with an
inventory, gaps and not-applicable claims; discovery split into calls by the
run's input limit; per-question outcomes for all 16; the token ceiling; a
revoked user; needs-evidence searches; the measured aids (C03 offsets, G02
level index); and the historical-RFI filter.

Every model here is a stub. These tests say what the code does with ANY
reply — they say nothing about how well a real model reviews drawings, and
nothing in this file may be quoted as an accuracy figure.
"""

import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest  # noqa: E402

import rfi_review  # noqa: E402
import review_aids  # noqa: E402
import rfi_sources  # noqa: E402
from plan_match import Element  # noqa: E402
from rfi_review import Evidence  # noqa: E402
from test_rfi_review import CLIENT_PDF, FakeModel, _candidates, _row, _seed, database, ev, needs_db  # noqa: E402,F401

ALL = list(rfi_review.CHECKS)


# --- discovery parsing -------------------------------------------------------------


def test_discovery_keeps_inventory_gaps_and_not_applicable_only_with_server_evidence():
    raw = json.dumps({
        "observations": [{"checkId": "C01", "statement": "C-6 at 3/C", "evidenceIds": ["ev1"]}],
        "inventory": [
            {"checkId": "C01", "entity": "column C-6", "location": "3/C", "fields": [
                {"name": "size", "value": "14 x 30", "state": "supported", "evidenceIds": ["ev1"]},
                {"name": "base elevation", "value": "invented", "state": "supported", "evidenceIds": ["ev999"]},
                {"name": "top", "state": "unknown"},
            ]},
            {"checkId": "X99", "entity": "nothing"},
        ],
        "gaps": [{"checkId": "C01", "field": "base elevation", "reason": "not shown"}, {"checkId": "C01", "field": ""}],
        "notApplicable": [
            {"checkId": "B01", "reason": "no beams on this slab plan", "evidenceIds": ["ev1"]},
            {"checkId": "B02", "reason": "trust me", "evidenceIds": []},
        ],
    })
    got = rfi_review.parse_discovery(raw, {"ev1"}, {"C01", "B01", "B02"})
    fields = {f["name"]: f for f in got["inventory"][0]["fields"]}
    assert fields["size"]["state"] == "supported" and fields["size"]["value"] == "14 x 30"
    # Claimed support with evidence the server never issued is not support.
    assert fields["base elevation"] == {"name": "base elevation", "value": None, "state": "unknown", "evidenceIds": [], "derived": False}
    assert len(got["inventory"]) == 1
    assert [g["field"] for g in got["gaps"]] == ["base elevation"]
    # "Does not apply" is a claim about the drawing: without evidence it is dropped.
    assert [n["checkId"] for n in got["notApplicable"]] == ["B01"]


def test_needs_evidence_without_anything_to_search_for_is_an_ordinary_rfi():
    raw = json.dumps({"candidates": [
        {"checkId": "C01", "kind": "missing", "disposition": "needs_evidence", "searchFor": "", "issue": "x", "evidenceIds": ["ev1"]},
        {"checkId": "C01", "kind": "missing", "disposition": "needs_evidence", "searchFor": "COLUMN SCHEDULE", "issue": "y", "evidenceIds": ["ev1"]},
    ]})
    got, _ = rfi_review.parse_candidates(raw, {"ev1"}, {"C01"})
    assert [c["disposition"] for c in got] == ["rfi", "needs_evidence"]
    assert got[1]["searched"] == []


# --- batching ----------------------------------------------------------------------


def _img(eid, page, doc="d"):
    e = ev(eid, "page", page=page, doc=doc)
    e.image = b"png"
    return e


def test_discovery_keeps_a_page_together_and_puts_pairs_first():
    items = [ev("ev1", page=1, text="a" * 400), _img("ev2", 1), ev("ev3", page=2, text="b" * 400), _img("ev4", 2)]
    pair = [_img("ev5", 1), _img("ev6", 2)]
    batches, unread = rfi_review.discovery_batches(items, pair, max_input=2500 + 5000, max_batches=5)
    assert [[e.id for e in b] for b in batches] == [["ev5", "ev6", "ev1"], ["ev2", "ev3", "ev4"]] or all(
        len({(e.document_id, e.page_number) for e in b if e.id not in ("ev5", "ev6")}) <= 2 for b in batches
    )
    assert batches[0][0].id == "ev5" and unread == []


def test_what_does_not_fit_the_call_cap_is_returned_unread_lowest_rank_last():
    items = [_img(f"ev{i}", page=i) for i in range(1, 7)]
    batches, unread = rfi_review.discovery_batches(items, [], max_input=2500 + 2 * rfi_review.IMAGE_TOKENS, max_batches=2)
    assert [e.id for b in batches for e in b] == ["ev1", "ev2", "ev3", "ev4"]
    assert [e.id for e in unread] == ["ev5", "ev6"]


def test_a_page_larger_than_one_call_is_split_not_dropped():
    items = [_img(f"ev{i}", page=1) for i in range(1, 5)]
    batches, unread = rfi_review.discovery_batches(items, [], max_input=2500 + rfi_review.IMAGE_TOKENS, max_batches=9)
    assert len(batches) == 4 and unread == []


# --- outcomes ----------------------------------------------------------------------


def _obs(cid):
    return {"checkId": cid, "statement": "seen", "evidenceIds": ["ev1"]}


def test_every_question_gets_an_outcome_and_none_is_a_silent_pass():
    selected = ["G01", "C01", "C02", "B01", "F01", "FL01"]
    got = rfi_review.check_results(
        ALL, selected, {"F04": {"reason": "not chosen"}},
        observations=[_obs("C01"), _obs("C02"), _obs("FL01")],
        gaps=[{"checkId": "F01", "field": "bottom elevation", "reason": "not shown"}],
        not_applicable=[{"checkId": "B01", "reason": "no beams here", "evidenceIds": ["ev1"]}],
        found={"C01": 2},
    )
    assert len(got) == 16
    assert got["C01"]["outcome"] == "candidate_found" and got["C01"]["candidates"] == 2
    assert got["C02"]["outcome"] == "complete_no_issue"
    assert got["B01"]["outcome"] == "not_applicable" and got["B01"]["reason"] == "no beams here"
    assert got["F01"]["outcome"] == "insufficient_evidence" and got["F01"]["gaps"] == ["bottom elevation: not shown"]
    assert got["G01"]["outcome"] == "insufficient_evidence"
    assert got["F04"] == {"outcome": "not_selected", "reason": "not chosen", "observations": 0, "candidates": 0, "gaps": []}


def test_unread_evidence_never_reports_no_issue():
    got = rfi_review.check_results(ALL, ["C02"], {}, [_obs("C02")], [], [], {}, omitted=True)
    assert got["C02"]["outcome"] == "insufficient_evidence" and "not read" in got["C02"]["reason"]


def test_a_budget_stop_fails_what_it_did_not_finish_but_keeps_what_it_found():
    got = rfi_review.check_results(ALL, ["G01", "C01"], {}, [_obs("C01")], [], [], {"G01": 1}, stopped="reached its token budget")
    assert got["G01"]["outcome"] == "candidate_found"
    assert got["C01"]["outcome"] == "failed" and "token budget" in got["C01"]["reason"]


# --- the rules on aids --------------------------------------------------------------


def _decision(**kw):
    return {"decision": "keep", "reason": "", "subject": "Column C-6", "question": "Is C-6 on grid 3?", "why": "", "priority": "normal", "confidence": "medium", **kw}


def test_an_aid_cannot_be_the_only_support_and_its_numbers_are_not_grounding():
    evidence = {"ev1": ev("ev1", "aid", text="MEASURED ... C-6 near 3/C: centre 5'-7\" right of grid line 3."), "ev2": ev("ev2", page=2, text="COLUMN C-6 AT 3/C")}
    alone = {"checkId": "C03", "kind": "ambiguity", "element": "C-6", "location": "3/C", "evidenceIds": ["ev1"]}
    assert "derived" in rfi_review.guard(alone, _decision(), evidence, [])
    both = dict(alone, evidenceIds=["ev1", "ev2"])
    assert rfi_review.guard(both, _decision(), evidence, []) is None
    # 5'-7" exists only in the aid: the question may not state it.
    assert "states 5" in rfi_review.guard(both, _decision(question="Is C-6 offset 5'-7\" from grid 3?"), evidence, [])


def test_thinking_is_a_budget_only_when_the_plan_set_one():
    assert rfi_review.thinking_setting({"limits": {"maxThinkingTokens": 4096, "thinkingEffort": "low"}}) == "budget:4096"
    assert rfi_review.thinking_setting({"limits": {"maxThinkingTokens": None, "thinkingEffort": "high"}}) == "high"
    assert rfi_review.thinking_setting({"thinkingRequested": "low"}) == "low"


def test_the_transport_maps_a_budget_to_what_each_model_takes():
    import llm

    assert llm._claude_stage_thinking("claude-haiku-4-5", "budget:3000") == ({"type": "enabled", "budget_tokens": 3000}, None, 3000)
    assert llm._claude_stage_thinking("claude-sonnet-5", "budget:3000")[1] == {"effort": "medium"}
    assert llm._stage_thinking_intended("gemini-2.5-flash", "budget:3000") == {"thinking_budget": 3000}
    assert llm._stage_thinking_intended("gemini-3.6-flash", "budget:9000") == {"thinking_level": "high"}
    assert llm.thinking_budget("budget:10") == 1024 and llm.thinking_budget("low") is None


def test_usage_rows_are_tagged_with_the_run_that_paid_for_them():
    import usage

    assert usage.current_tag() is None
    with usage.tagged("run-1", "discovery", 2):
        assert usage.current_tag() == {"reviewRunId": "run-1", "stage": "discovery", "attempt": 2}
    assert usage.current_tag() is None


# --- C03: column offsets from the grid (four scenarios) -------------------------------

PTFT = 18.0  # 1/4" = 1'-0"
GRID = ({"3": 100.0, "4": 400.0}, {"C": 100.0, "D": 400.0})


def _col(cx, cy, w=36.0, h=18.0):
    return Element(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)


def test_c03_a_column_on_its_crossing_is_on_both_lines():
    offsets = review_aids.column_grid_offsets(*GRID, [_col(100.0, 400.0)], PTFT)
    text = review_aids.describe_offsets("S2.105", offsets, PTFT)
    assert offsets[0].crossing == "3/D"
    assert "centre on grid line 3, on grid line D" in text
    assert "(2'-0\" x 1'-0\")" in text


def test_c03_an_offset_column_is_measured_at_the_printed_scale_with_its_direction():
    offsets = review_aids.column_grid_offsets(*GRID, [_col(100.0 + 21.0, 100.0 - 9.0)], PTFT)
    text = review_aids.describe_offsets("S2.105", offsets, PTFT)
    # 21pt at 18pt/ft is 1'-2"; 9pt is 0'-6".
    assert "1'-2\" right of grid line 3" in text and "0'-6\" above grid line C" in text
    assert text.startswith("MEASURED on S2.105 from the PDF geometry, not printed on the drawing")


def test_c03_with_no_grid_measures_nothing():
    assert review_aids.column_grid_offsets({}, {}, [_col(100.0, 100.0)], PTFT) == []
    assert review_aids.column_grid_offsets({"3": 100.0}, {}, [_col(100.0, 100.0)], PTFT) == []


def test_c03_with_no_scale_reports_points_and_says_they_cannot_be_compared():
    offsets = review_aids.column_grid_offsets(*GRID, [_col(110.0, 100.0)], None)
    text = review_aids.describe_offsets("S2.105", offsets, None)
    assert "10 pt right of grid line 3" in text and "cannot be compared with printed dimensions" in text


def test_c03_a_column_far_from_every_crossing_belongs_to_none():
    assert review_aids.column_grid_offsets(*GRID, [_col(250.0, 250.0)], PTFT) == []


# --- G02: the level index ---------------------------------------------------------


def test_g02_lists_levels_and_elevations_with_their_sheets():
    text = review_aids.level_index([
        ("S2.105", "LEVEL 5 FORMING PLAN  T.O.S. EL. 48'-0\""),
        ("A3.01", "LEVEL 5 ENLARGED PLAN  LEVEL 6 ABOVE"),
    ])
    assert "LEVEL 5: S2.105, A3.01" in text and "LEVEL 6: A3.01" in text
    assert "48'-0\"" in text and "not a finding" in text
    assert review_aids.level_index([("S1", "GENERAL NOTES")]) is None


# --- historical RFIs are never input ---------------------------------------------------


def test_an_rfi_form_reads_as_one_and_a_drawing_note_does_not():
    form = "BIM RFI:\n Date Issued:\n Author:\n Plan/Sheet:\n Discrepancies in dimensions and column location."
    assert rfi_sources.looks_like_rfi_text(form)
    assert rfi_sources.looks_like_rfi_text("REQUEST FOR INFORMATION  RFI NO. 12  QUESTION: ...  RESPONSE: ...")
    assert not rfi_sources.looks_like_rfi_text("GENERAL NOTES: SUBMIT AN RFI FOR ANY DISCREPANCY. REQUEST FOR INFORMATION SHALL ...")
    assert not rfi_sources.looks_like_rfi_text("LEVEL 5 FORMING PLAN  COLUMN C-6")


def test_the_filename_rule_is_the_apis():
    ts = (Path(__file__).resolve().parents[2] / "apps/api/src/rfiSources.ts").read_text()
    assert "rfi_sources.py" in ts


# --- against a real database ---------------------------------------------------------


@needs_db
def test_a_person_removed_from_the_project_stops_the_run_before_any_call(database, monkeypatch):
    import llm

    seeded = _seed(database, checks=("C01",))
    with database.connect() as conn:
        other = str(uuid.uuid4())
        conn.execute("INSERT INTO users (id, email, name, \"passwordHash\") VALUES (%s, %s, 'other', 'x')", (other, f"{other}@test.invalid"))
        conn.execute('UPDATE projects SET "ownerId" = %s WHERE id = %s', (other, seeded["project"]))
    model = FakeModel()
    monkeypatch.setattr(llm, "complete", model)
    assert rfi_review.run(seeded["run"])["reason"] == "access revoked"
    assert model.calls == []
    status, _, error, *_ = _row(database, seeded["run"])
    assert status == "cancelled" and "no longer has access" in error


@needs_db
def test_a_member_keeps_access(database, monkeypatch):
    import llm

    seeded = _seed(database, checks=("C01",))
    with database.connect() as conn:
        other = str(uuid.uuid4())
        conn.execute("INSERT INTO users (id, email, name, \"passwordHash\") VALUES (%s, %s, 'other', 'x')", (other, f"{other}@test.invalid"))
        conn.execute('UPDATE projects SET "ownerId" = %s WHERE id = %s', (other, seeded["project"]))
        conn.execute('INSERT INTO project_members ("projectId", "userId") VALUES (%s, %s)', (seeded["project"], seeded["user"]))
    monkeypatch.setattr(llm, "complete", FakeModel())
    assert rfi_review.run(seeded["run"]).get("kept") == 1


@needs_db
def test_the_token_ceiling_stops_before_a_call_and_the_run_is_partial(database, monkeypatch):
    import llm

    # The stub bills 150 tokens a call: discovery and reasoning fit under 200,
    # verification would not be sent.
    seeded = _seed(database, checks=("G01", "C01"), limits={"maxTotalTokens": 200})
    model = FakeModel()
    monkeypatch.setattr(llm, "complete", model)
    result = rfi_review.run(seeded["run"])
    assert [c["stage"] for c in model.calls] == ["discovery", "reasoning"]
    assert result["partial"] is True and result["kept"] == 0
    with database.connect() as conn:
        status, results, coverage = conn.execute(
            'SELECT status::text, "checkResults", coverage FROM rfi_review_runs WHERE id = %s', (seeded["run"],)
        ).fetchone()
    assert status == "partial"
    assert results["C01"]["outcome"] == "failed" and "token budget" in results["C01"]["reason"]
    # What the exact grid comparison found before the stop is kept.
    assert results["G01"]["outcome"] == "candidate_found"
    assert any("300" in o or "budget" in o for o in coverage["omissions"])
    assert {r[0] for r in _candidates(database, seeded["project"])} == {"grid_mismatch"}


@needs_db
def test_a_small_input_limit_splits_discovery_and_says_what_it_did_not_read(database, monkeypatch):
    import llm

    seeded = _seed(database, checks=("C01",), limits={"maxInputTokens": 2500 + 2 * rfi_review.IMAGE_TOKENS + 400, "maxBatches": 1})
    calls = []

    def quiet(system, user, **kw):
        calls.append(("DISCOVERY" in system, len(kw.get("images") or [])))
        return llm.Reply(text=json.dumps({"observations": []}), stop_reason="end_turn", model="stub", input_tokens=10, output_tokens=5)

    monkeypatch.setattr(llm, "complete", quiet)
    result = rfi_review.run(seeded["run"])
    assert result["batches"] == 1 and result["unread"] > 0 and result["partial"] is True
    # Nothing observed: reasoning and verification are not paid for.
    assert calls == [(True, calls[0][1])]
    with database.connect() as conn:
        status, results, coverage, manifest = conn.execute(
            'SELECT status::text, "checkResults", coverage, "evidenceManifest" FROM rfi_review_runs WHERE id = %s', (seeded["run"],)
        ).fetchone()
    assert status == "partial" and "were not read" in coverage["omissions"][0]
    assert results["C01"]["outcome"] == "insufficient_evidence"
    assert any(m["batch"] is None for m in manifest) and any(m["batch"] == 1 for m in manifest)


@needs_db
def test_needs_evidence_searches_the_project_and_shows_verification_what_it_found(database, monkeypatch):
    import llm

    seeded = _seed(database, checks=("C01",))
    # A schedule elsewhere in the project, NOT in the planned scope.
    with database.connect() as conn:
        doc, page, chunk = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
        conn.execute(
            'INSERT INTO documents (id, "projectId", filename, "spacesKey", pages, status) VALUES (%s, %s, \'s6.pdf\', \'k\', 1, \'completed\')',
            (doc, seeded["project"]),
        )
        conn.execute(
            'INSERT INTO pages (id, "documentId", "pageNumber", "combinedPageNumber", "sheetNumber") VALUES (%s, %s, 1, 3, \'S6.01\')',
            (page, doc),
        )
        conn.execute(
            'INSERT INTO chunks (id, "pageId", text, bbox, "tokenCount", kind) VALUES (%s, %s, %s, %s, 10, \'text\')',
            (chunk, page, "COLUMN SCHEDULE: MARK C-6 SIZE 14 X 30 CONCRETE", json.dumps({"x": 1, "y": 1, "width": 9, "height": 9})),
        )
        conn.execute('INSERT INTO chunk_identifiers ("chunkId", identifier) SELECT %s, unnest(cdip_identifiers(%s))', (chunk, "COLUMN SCHEDULE: MARK C-6"))

    model = FakeModel()
    original = model.__call__

    def with_search(system, user, **kw):
        reply = original(system, user, **kw)
        if "REASONING" in system:
            body = json.loads(reply.text)
            body["candidates"][0].update(disposition="needs_evidence", searchFor="column schedule C-6")
            return llm.Reply(text=json.dumps(body), stop_reason="end_turn", model="stub", input_tokens=100, output_tokens=50)
        return reply

    monkeypatch.setattr(llm, "complete", with_search)
    rfi_review.run(seeded["run"])
    verification = [c for c in model.calls if c["stage"] == "verification"][0]["user"]
    assert "COLUMN SCHEDULE: MARK C-6" in verification and '"searched": ["ev' in verification
    with database.connect() as conn:
        coverage = conn.execute("SELECT coverage FROM rfi_review_runs WHERE id = %s", (seeded["run"],)).fetchone()[0]
    assert {"query": "C01: column schedule C-6", "found": 1, "stage": "verification"} in coverage["searchLog"]
    saved = [r for r in _candidates(database, seeded["project"]) if r[0] == "C01"][0]
    assert any(e.get("role") == "context" and e["sheetNumber"] == "S6.01" for e in saved[6])


@needs_db
def test_an_excluded_document_is_never_found_by_a_search(database):
    project = str(uuid.uuid4())
    with database.connect() as conn:
        conn.execute("INSERT INTO projects (id, name) VALUES (%s, 'excluded search')", (project,))
        for included in (True, False):
            doc, page, chunk = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
            conn.execute(
                'INSERT INTO documents (id, "projectId", filename, "spacesKey", pages, status, "includeInRfiAnalysis") '
                "VALUES (%s, %s, 'x.pdf', 'k', 1, 'completed', %s)",
                (doc, project, included),
            )
            conn.execute('INSERT INTO pages (id, "documentId", "pageNumber", "combinedPageNumber") VALUES (%s, %s, 1, 1)', (page, doc))
            conn.execute(
                'INSERT INTO chunks (id, "pageId", text, bbox, "tokenCount", kind) VALUES (%s, %s, %s, \'{}\', 5, \'text\')',
                (chunk, page, f"PILE CAP SCHEDULE {'LIVE' if included else 'OLD RFI'}"),
            )
    hits = rfi_review.resolving_search(project, "pile cap schedule", set())
    assert [h["text"] for h in hits] == ["PILE CAP SCHEDULE LIVE"]


@needs_db
def test_an_rfi_form_is_excluded_at_ingest_unless_a_person_decided(database):
    project = str(uuid.uuid4())
    with database.connect() as conn:
        conn.execute("INSERT INTO projects (id, name) VALUES (%s, 'ingest exclusion')", (project,))
        docs = {}
        for name, reason in (("fresh", None), ("decided", "Included in RFI review by a person")):
            doc = str(uuid.uuid4())
            docs[name] = doc
            conn.execute(
                'INSERT INTO documents (id, "projectId", filename, "spacesKey", pages, status, "rfiExclusionReason") '
                "VALUES (%s, %s, 'scan_0042.pdf', 'k', 1, 'completed', %s)",
                (doc, project, reason),
            )
            conn.execute(
                'INSERT INTO pages (id, "documentId", "pageNumber", "combinedPageNumber", text) VALUES (%s, %s, 1, 1, %s)',
                (str(uuid.uuid4()), doc, "BIM RFI: Date Issued: Author: Plan/Sheet: A3.27"),
            )
        assert rfi_sources.exclude_if_rfi(conn, docs["fresh"]) is True
        assert rfi_sources.exclude_if_rfi(conn, docs["decided"]) is False
        rows = dict(conn.execute('SELECT id, "includeInRfiAnalysis" FROM documents WHERE "projectId" = %s', (project,)).fetchall())
    assert rows == {docs["fresh"]: False, docs["decided"]: True}


@needs_db
def test_c03_hands_discovery_the_measured_offsets_as_an_aid(database, monkeypatch):
    import llm

    seeded = _seed(database, checks=("C03", "G02"))
    seen = []

    def quiet(system, user, **kw):
        seen.append(user)
        return llm.Reply(text=json.dumps({"observations": []}), stop_reason="end_turn", model="stub", input_tokens=10, output_tokens=5)

    monkeypatch.setattr(llm, "complete", quiet)
    rfi_review.run(seeded["run"])
    assert 'kind="aid"' in seen[0]
    assert "MEASURED on A3.01 from the PDF geometry" in seen[0]
    with database.connect() as conn:
        notes = conn.execute("SELECT notes FROM rfi_review_runs WHERE id = %s", (seeded["run"],)).fetchone()[0]
    assert any(n.startswith("C03 aid:") for n in notes)
