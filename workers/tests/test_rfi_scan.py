"""The scan job: the wording guards, the batch alignment, the enum mirrors — and,
when a database is available, the whole job run against real SQL.

The wording guards are the one place a model's output reaches an RFI, so they
are tested hardest: every way a reply could smuggle in a fact the drawings do
not contain has to fall back to the template, never through to the RFI.
"""

import json
import os
import re
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402

import rfi_scan  # noqa: E402
from rfi_checks import Finding  # noqa: E402

SCHEMA = Path(__file__).resolve().parents[2] / "apps/api/prisma/schema.prisma"


def finding(**over) -> Finding:
    base = dict(
        check_type="dangling_reference",
        fingerprint="dangling_reference:abc",
        confidence="high",
        subject="Sheet S-501 referenced but not in the drawing set",
        question="S-101 references sheet S-501, but no sheet S-501 is in the drawing set.",
        evidence=[
            {
                "documentId": "d",
                "pageNumber": 3,
                "combinedPageNumber": 12,
                "sheetNumber": "S-101",
                "bbox": None,
                "chunkId": "c",
                "quote": "PILE CAP PER 5/S-501 AT GRID 7/D",
                "role": "finding",
            }
        ],
        facts={"missingSheet": "S-501", "referencedFrom": "S-101"},
    )
    base.update(over)
    return Finding(**base)


# --- the grounding guard --------------------------------------------------------


def test_wording_built_from_the_facts_passes():
    ok, why = rfi_scan.wording_is_grounded(
        "Missing sheet S-501. Sheet S-101 calls out detail 5/S-501 at grid 7/D; "
        "please issue S-501.",
        finding(),
    )
    assert ok, why


def test_a_sheet_the_finding_does_not_contain_is_refused():
    ok, why = rfi_scan.wording_is_grounded("Please issue S-502.", finding())
    assert not ok and "S-502" in why


def test_separators_do_not_matter_to_the_guard():
    # "S501" and "S-501" are the same sheet.
    assert rfi_scan.wording_is_grounded("Please issue S501.", finding())[0]


def test_an_invented_number_is_refused():
    # The dangerous one: a value nobody measured, in a contractual question.
    ok, why = rfi_scan.wording_is_grounded(
        "Please issue S-501 showing the 12 inch pile cap.", finding()
    )
    assert not ok and "12" in why


def test_a_page_number_does_not_license_the_same_number_as_a_value():
    # The evidence is on page 12. That must not make "12 inch" grounded: on a
    # 400-page set nearly every small number is somebody's page number.
    ok, _ = rfi_scan.wording_is_grounded("S-501 shows a 12 inch pile cap.", finding())
    assert not ok


def test_a_page_reference_to_the_evidence_page_is_allowed():
    assert rfi_scan.wording_is_grounded("See page 12: S-501 is referenced.", finding())[0]


def test_a_page_reference_to_another_page_is_refused():
    ok, why = rfi_scan.wording_is_grounded("See page 13: S-501 is referenced.", finding())
    assert not ok and "13" in why


def test_numbers_from_the_drawing_itself_are_allowed():
    # 5 and 7 come from the quote "5/S-501 AT GRID 7/D".
    assert rfi_scan.wording_is_grounded("Detail 5 at grid 7/D refers to S-501.", finding())[0]


def test_an_invented_code_section_is_refused():
    ok, _ = rfi_scan.wording_is_grounded("Per IBC 1705, please issue S-501.", finding())
    assert not ok


# --- parsing a batched reply ----------------------------------------------------


def reply(*items) -> str:
    return json.dumps({"items": list(items)})


def good(index=0, subject="Sheet S-501 not issued", question="S-101 references S-501, which is not in the set. Please issue it."):
    return {"index": index, "subject": subject, "question": question}


def test_a_well_formed_item_is_kept():
    assert rfi_scan.parse_wording(reply(good()), [finding()]) == {
        0: ("Sheet S-501 not issued", "S-101 references S-501, which is not in the set. Please issue it.")
    }


def test_a_code_fence_is_tolerated():
    raw = "```json\n" + reply(good()) + "\n```"
    assert 0 in rfi_scan.parse_wording(raw, [finding()])


def test_an_index_outside_the_batch_is_dropped():
    assert rfi_scan.parse_wording(reply(good(index=3)), [finding()]) == {}


def test_an_index_answered_twice_drops_both():
    # Two answers for one finding: neither can be trusted, so neither is kept.
    assert rfi_scan.parse_wording(reply(good(), good()), [finding()]) == {}


def test_a_drifted_item_fails_the_guard_of_the_finding_its_index_names():
    # Index 1 carries wording meant for index 0. The guard catches it because
    # S-501 is not in finding 1's facts or quotes.
    other = finding(
        fingerprint="open_item_note:x",
        check_type="open_item_note",
        subject="Open item on S-102",
        question="The drawings note \"PILE TIP ELEV TBD\" on S-102.",
        evidence=[dict(finding().evidence[0], sheetNumber="S-102", quote="PILE TIP ELEV TBD")],
        facts={"note": "PILE TIP ELEV TBD", "shownOn": "S-102"},
    )
    parsed = rfi_scan.parse_wording(reply(good(index=1)), [finding(), other])
    assert parsed == {}


@pytest.mark.parametrize(
    "item",
    [
        {"index": 0, "subject": "x", "question": "S-101 references S-501, which is not in the set."},
        {"index": 0, "subject": "Sheet S-501 not issued", "question": "Why?"},
        {"index": 0, "subject": 5, "question": "S-101 references S-501, which is not in the set."},
        {"index": "0", "subject": "Sheet S-501", "question": "S-101 references S-501, which is not in the set."},
    ],
)
def test_malformed_items_are_dropped(item):
    assert rfi_scan.parse_wording(reply(item), [finding()]) == {}


@pytest.mark.parametrize("raw", ["", "not json", "[]", '{"items": "no"}', None])
def test_an_unusable_reply_words_nothing(raw):
    assert rfi_scan.parse_wording(raw, [finding()]) == {}


# --- word(): when the model is not used -----------------------------------------


def test_no_key_means_templates_and_a_note_saying_so(monkeypatch):
    monkeypatch.setattr(rfi_scan, "AI_WORDING", True)
    monkeypatch.setattr(rfi_scan.llm, "available", lambda provider: False)
    worded, note = rfi_scan.word([finding()], "project")
    assert worded == {}
    assert "not set" in note


def test_wording_switched_off_makes_no_call(monkeypatch):
    monkeypatch.setattr(rfi_scan, "AI_WORDING", False)
    monkeypatch.setattr(rfi_scan.llm, "complete", lambda *a, **k: pytest.fail("called the model"))
    worded, note = rfi_scan.word([finding()], "project")
    assert worded == {} and "off" in note


def test_word_batches_and_keys_by_fingerprint(monkeypatch):
    monkeypatch.setattr(rfi_scan, "AI_WORDING", True)
    monkeypatch.setattr(rfi_scan, "BATCH_SIZE", 2)
    monkeypatch.setattr(rfi_scan.llm, "available", lambda provider: True)
    calls = []

    class Reply:
        def __init__(self, text):
            self.text = text

    def fake_complete(system, user, **kwargs):
        calls.append(kwargs)
        n = user.count("<finding>")
        return Reply(reply(*[good(index=i) for i in range(n)]))

    monkeypatch.setattr(rfi_scan.llm, "complete", fake_complete)
    findings = [finding(fingerprint=f"dangling_reference:{i}") for i in range(5)]
    worded, note = rfi_scan.word(findings, "project")
    assert note is None
    assert len(calls) == 3, "5 findings at 2 per call"
    assert set(worded) == {f.fingerprint for f in findings}
    assert all(c["kind"] == "rfi" and c["project_id"] == "project" for c in calls)


def test_a_failed_call_leaves_that_batch_on_templates(monkeypatch):
    monkeypatch.setattr(rfi_scan, "AI_WORDING", True)
    monkeypatch.setattr(rfi_scan.llm, "available", lambda provider: True)
    monkeypatch.setattr(rfi_scan.llm, "complete", lambda *a, **k: None)
    assert rfi_scan.word([finding()], "project") == ({}, None)


# --- the values the worker writes into Postgres enums ---------------------------


def _enum(name: str) -> set[str]:
    block = re.search(rf"enum {name} \{{(.*?)\}}", SCHEMA.read_text(), re.S)
    assert block, f"{name} not found in schema.prisma"
    return {
        line.strip()
        for line in block.group(1).splitlines()
        if line.strip() and not line.strip().startswith("//")
    }


@pytest.mark.parametrize(
    "enum, values",
    [
        ("RfiScanStatus", rfi_scan.SCAN_STATUSES),
        ("RfiCandidateStatus", rfi_scan.CANDIDATE_STATUSES),
        ("RfiConfidence", rfi_scan.CONFIDENCES),
    ],
)
def test_worker_enum_values_match_the_schema(enum, values):
    assert _enum(enum) == set(values)


def test_every_confidence_a_check_can_emit_is_a_real_enum_value():
    import rfi_checks

    source = Path(rfi_checks.__file__).read_text()
    emitted = set(re.findall(r'"(high|medium|low)"', source))
    assert emitted <= set(rfi_scan.CONFIDENCES)


# --- the whole job, against a real database -------------------------------------
#
# Skipped unless RFI_TEST_DATABASE_URL points at a MIGRATED database. Unit tests
# cannot reach the SQL — the upsert's conflict clause, the enum casts, the
# array cast on an empty list — and SQL that has never run is the one part of
# this job a mock would happily agree with.

TEST_DB = os.environ.get("RFI_TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(not TEST_DB, reason="set RFI_TEST_DATABASE_URL to a migrated database")


@pytest.fixture
def database(monkeypatch):
    import config
    import db

    monkeypatch.setattr(config, "DATABASE_URL", TEST_DB)
    monkeypatch.setattr(db, "_pool", None)
    monkeypatch.setattr(db, "_pool_unavailable", True)  # one connection per call
    monkeypatch.setattr(rfi_scan, "AI_WORDING", False)
    return db


def _seed(db) -> tuple[str, str]:
    project, document = str(uuid.uuid4()), str(uuid.uuid4())
    pages = {
        "S-101": str(uuid.uuid4()),
        "S-102": str(uuid.uuid4()),
        "S-500": str(uuid.uuid4()),
    }
    texts = {
        "S-101": ("PILE CAP PC4 PER 5/S-501", ["PC4", "S501"]),
        "S-102": ("PC-4 AT GRID 9/D\nTOP OF PILE ELEV TBD", ["PC4"]),
        "S-500": ("PILE CAP SCHEDULE\nPC1 6'X6'\nPC2 8'X8'\nPC3 10'X10'", ["PC1", "PC2", "PC3"]),
    }
    with db.connect() as conn:
        conn.execute("INSERT INTO projects (id, name) VALUES (%s, 'rfi scan test')", (project,))
        conn.execute(
            'INSERT INTO documents (id, "projectId", filename, "spacesKey", pages, status) '
            "VALUES (%s, %s, 'set.pdf', 'k', 3, 'completed')",
            (document, project),
        )
        for n, (sheet, page_id) in enumerate(pages.items(), start=1):
            conn.execute(
                'INSERT INTO pages (id, "documentId", "pageNumber", "combinedPageNumber", '
                '"sheetNumber") VALUES (%s, %s, %s, %s, %s)',
                (page_id, document, n, n, sheet),
            )
            text, identifiers = texts[sheet]
            chunk_id = str(uuid.uuid4())
            conn.execute(
                'INSERT INTO chunks (id, "pageId", text, bbox, "tokenCount", kind) '
                "VALUES (%s, %s, %s, %s, 10, 'text')",
                (chunk_id, page_id, text, json.dumps({"x": 1, "y": 2, "width": 3, "height": 4})),
            )
            for ident in identifiers:
                conn.execute(
                    'INSERT INTO chunk_identifiers ("chunkId", identifier) VALUES (%s, %s)',
                    (chunk_id, ident),
                )
    return project, document


def _scan(db, project) -> str:
    scan = str(uuid.uuid4())
    with db.connect() as conn:
        conn.execute('INSERT INTO rfi_scans (id, "projectId") VALUES (%s, %s)', (scan, project))
    return scan


@needs_db
def test_a_scan_writes_candidates_and_reports_into_its_row(database):
    project, _ = _seed(database)
    scan = _scan(database, project)
    result = rfi_scan.run(project, scan)

    assert result["byCheck"] == {
        "dangling_reference": 1,
        "unscheduled_mark": 1,
        "open_item_note": 1,
    }
    with database.connect() as conn:
        status, findings, notes = conn.execute(
            'SELECT status, findings, notes FROM rfi_scans WHERE id = %s', (scan,)
        ).fetchone()
        rows = conn.execute(
            'SELECT "checkType", confidence, status, "questionSource", evidence '
            'FROM rfi_candidates WHERE "projectId" = %s ORDER BY "checkType"',
            (project,),
        ).fetchall()
    assert status == "completed" and findings == 3
    assert any("RFI_AI_WORDING=false" in n for n in notes)
    assert [r[0] for r in rows] == ["dangling_reference", "open_item_note", "unscheduled_mark"]
    assert all(r[2] == "pending" and r[3] == "template" for r in rows)
    mark = rows[2]
    assert mark[1] == "high", "PC4 is called out on two sheets"
    assert mark[4][0]["bbox"] == {"x": 1, "y": 2, "width": 3, "height": 4}


@needs_db
def test_a_rescan_is_idempotent_and_respects_decisions(database):
    project, _ = _seed(database)
    rfi_scan.run(project, _scan(database, project))
    with database.connect() as conn:
        conn.execute(
            "UPDATE rfi_candidates SET status = 'dismissed', subject = 'kept' "
            "WHERE \"projectId\" = %s AND \"checkType\" = 'open_item_note'",
            (project,),
        )
    rfi_scan.run(project, _scan(database, project))
    with database.connect() as conn:
        rows = conn.execute(
            'SELECT "checkType", status, subject FROM rfi_candidates WHERE "projectId" = %s',
            (project,),
        ).fetchall()
    assert len(rows) == 3, "the same three gaps, not six"
    dismissed = [r for r in rows if r[0] == "open_item_note"][0]
    assert dismissed[1:] == ("dismissed", "kept"), "a person's decision survives a re-scan"


@needs_db
def test_a_rescan_words_only_what_is_new(database, monkeypatch):
    # Wording costs a model call per batch. A finding already accepted,
    # dismissed or pending keeps its question, so an unchanged set re-scans
    # for free — and a finding added since is the only one sent.
    sent: list[list[str]] = []

    def fake_word(findings, project_id):
        sent.append(sorted(f.check_type for f in findings))
        return {}, None

    monkeypatch.setattr(rfi_scan, "word", fake_word)
    project, _ = _seed(database)
    rfi_scan.run(project, _scan(database, project))
    rfi_scan.run(project, _scan(database, project))
    assert sent[0] == ["dangling_reference", "open_item_note", "unscheduled_mark"]
    assert sent[1] == [], "nothing changed, so nothing is re-worded"


@needs_db
def test_a_decided_finding_is_left_exactly_as_it_was_decided(database):
    project, _ = _seed(database)
    first = _scan(database, project)
    rfi_scan.run(project, first)
    with database.connect() as conn:
        conn.execute(
            "UPDATE rfi_candidates SET status = 'dismissed' "
            "WHERE \"projectId\" = %s AND \"checkType\" = 'unscheduled_mark'",
            (project,),
        )
    second = _scan(database, project)
    rfi_scan.run(project, second)
    with database.connect() as conn:
        rows = dict(
            conn.execute(
                'SELECT "checkType", "scanId" FROM rfi_candidates WHERE "projectId" = %s',
                (project,),
            ).fetchall()
        )
    # The dismissed finding still belongs to the scan it was decided from;
    # the pending ones moved to the new scan.
    assert rows["unscheduled_mark"] == first
    assert rows["open_item_note"] == second


@needs_db
def test_a_gap_fixed_on_the_drawings_is_removed_from_review(database):
    project, _ = _seed(database)
    rfi_scan.run(project, _scan(database, project))
    with database.connect() as conn:
        # A revision filled in the TBD.
        conn.execute(
            "UPDATE chunks SET text = replace(text, 'TBD', '-42''-0\"') "
            'WHERE "pageId" IN (SELECT id FROM pages WHERE "documentId" IN '
            '(SELECT id FROM documents WHERE "projectId" = %s))',
            (project,),
        )
    scan = _scan(database, project)
    result = rfi_scan.run(project, scan)
    assert result["resolved"] == 1
    with database.connect() as conn:
        notes = conn.execute("SELECT notes FROM rfi_scans WHERE id = %s", (scan,)).fetchone()[0]
    assert any("no longer appear" in n for n in notes)


@needs_db
def test_a_project_with_no_findings_completes_cleanly(database):
    # Exercises the empty-array path of the stale-candidate DELETE.
    project = str(uuid.uuid4())
    with database.connect() as conn:
        conn.execute("INSERT INTO projects (id, name) VALUES (%s, 'empty')", (project,))
    result = rfi_scan.run(project, _scan(database, project))
    assert result["findings"] == 0


@needs_db
def test_a_failed_scan_is_marked_failed(database, monkeypatch):
    project, _ = _seed(database)
    scan = _scan(database, project)
    monkeypatch.setattr(rfi_scan.rfi_checks, "run_all", lambda *a: 1 / 0)
    with pytest.raises(ZeroDivisionError):
        rfi_scan.run(project, scan)
    with database.connect() as conn:
        status, error = conn.execute(
            "SELECT status, error FROM rfi_scans WHERE id = %s", (scan,)
        ).fetchone()
    assert status == "failed" and "division" in error
