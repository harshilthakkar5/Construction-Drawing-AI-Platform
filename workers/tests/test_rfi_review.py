"""The rfi-review job: the parsers, the rules a model is not trusted with, the
crop geometry — and, when a database is available, the whole job against real
SQL and the client's own drawings, with the model stubbed.

The stub is not a shortcut around the model; it is how the tests say what the
job must do with ANY reply: an id it never issued is dropped, a conflict on
one page is rejected, a question naming a number nothing showed is rejected,
and a call that fails is a failed run, never "no RFIs found".
"""

import json
import os
import re
import shutil
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import fitz  # noqa: E402
import pytest  # noqa: E402

import rfi_review  # noqa: E402
from rfi_review import Evidence  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "apps/api/prisma/schema.prisma"
LLM_TS = ROOT / "apps/api/src/llm.ts"
# The client's S2.105 / A3.01 set (page 2 is /Rotate 90). Not checked in.
CLIENT_PDF = Path(os.environ.get("RFI_REVIEW_TEST_PDF") or "/nonexistent")


def ev(eid: str, kind: str = "text", page: int = 1, text: str = "", doc: str = "d") -> Evidence:
    return Evidence(
        id=eid,
        kind=kind,
        document_id=doc,
        page_number=page,
        combined_page_number=page,
        sheet_number=f"S{page}",
        side=None,
        bbox={"x": 0, "y": 0, "width": 10, "height": 10},
        chunk_id=None if kind in ("page", "crop") else f"c-{eid}",
        text=text,
    )


# --- mirrors ---------------------------------------------------------------------


def test_the_review_model_defaults_match_the_api_estimate():
    """The plan screen prices the model llm.ts resolves; the worker runs the one
    this module resolves. A drift shows one model's price for another's work."""
    ts = LLM_TS.read_text()
    claude = re.search(r'DEFAULT_RFI_REVIEW_MODEL\s*=\s*"([^"]+)"', ts).group(1)
    gemini = re.search(r'DEFAULT_RFI_REVIEW_GEMINI_MODEL\s*=\s*"([^"]+)"', ts).group(1)
    src = (ROOT / "workers/src/rfi_review.py").read_text()
    assert f'"RFI_REVIEW_MODEL", "{claude}"' in src
    assert f'"RFI_REVIEW_GEMINI_MODEL", "{gemini}"' in src
    for var in ("RFI_REVIEW_MODEL", "RFI_REVIEW_GEMINI_MODEL"):
        assert var in ts


def test_statuses_match_the_enum():
    body = re.search(r"enum RfiReviewStatus \{([^}]*)\}", SCHEMA.read_text()).group(1)
    values = [line.strip() for line in body.splitlines() if line.strip() and not line.strip().startswith("//")]
    assert tuple(values) == rfi_review.STATUSES


def test_every_check_the_catalogue_has_is_known_to_the_worker():
    assert len(rfi_review.CHECKS) == 16
    assert {"G01", "G02", "F04", "C01", "C03", "B03"} <= set(rfi_review.CHECKS)


# --- parsing ---------------------------------------------------------------------


def test_an_observation_citing_an_id_the_server_never_issued_is_dropped():
    raw = json.dumps(
        {
            "observations": [
                {"checkId": "C01", "statement": "C-6 at 3/C", "evidenceIds": ["ev1", "ev77"]},
                {"checkId": "C01", "statement": "C-7 at 4/C", "evidenceIds": ["ev77"]},
                {"checkId": "X9", "statement": "?", "evidenceIds": ["ev1"]},
            ]
        }
    )
    got, dropped = rfi_review.parse_observations(raw, {"ev1", "ev2"}, {"C01"})
    assert [o["evidenceIds"] for o in got] == [["ev1"]]
    assert dropped == 2


def test_no_json_is_none_and_an_empty_list_is_an_answer():
    assert rfi_review.parse_observations("I could not read the drawing.", {"ev1"}, {"C01"}) is None
    assert rfi_review.parse_observations('```json\n{"observations": []}\n```', {"ev1"}, {"C01"}) == ([], 0)


def test_a_candidate_needs_a_known_kind_and_evidence():
    raw = json.dumps(
        {
            "candidates": [
                {"checkId": "C01", "kind": "conflict", "issue": "x", "evidenceIds": ["ev1", "ev2"]},
                {"checkId": "C01", "kind": "opinion", "issue": "x", "evidenceIds": ["ev1"]},
                {"checkId": "C01", "kind": "missing", "issue": "x", "evidenceIds": []},
            ]
        }
    )
    got, dropped = rfi_review.parse_candidates(raw, {"ev1", "ev2"}, {"C01"})
    assert len(got) == 1 and dropped == 2


def test_a_problem_given_two_verdicts_gets_neither():
    raw = json.dumps(
        {
            "decisions": [
                {"index": 0, "decision": "keep", "subject": "a", "question": "b"},
                {"index": 0, "decision": "reject"},
                {"index": 1, "decision": "keep", "subject": "a", "question": "b"},
                {"index": 5, "decision": "keep"},
                {"index": True, "decision": "keep"},
            ]
        }
    )
    got = rfi_review.parse_decisions(raw, 2)
    assert set(got) == {1}


# --- the rules the model is not trusted with --------------------------------------


def _decision(**over) -> dict:
    base = {"decision": "keep", "subject": "Column C-6 at 3/C", "question": "Which is right?", "why": "",
            "priority": "normal", "confidence": "medium", "reason": ""}
    base.update(over)
    return base


def test_a_description_cannot_be_the_only_support():
    evidence = {"ev1": ev("ev1", "description", 1, "C-6 at 3/C"), "ev2": ev("ev2", "description", 2, "C-6")}
    cand = {"checkId": "C01", "kind": "missing", "element": "", "location": "", "evidenceIds": ["ev1", "ev2"]}
    assert "description" in rfi_review.guard(cand, _decision(), evidence, [])
    evidence["ev3"] = ev("ev3", "text", 2, "C-6 at 3/C")
    cand["evidenceIds"].append("ev3")
    assert rfi_review.guard(cand, _decision(), evidence, []) is None


def test_a_conflict_needs_two_pages():
    evidence = {"ev1": ev("ev1", "text", 1, "C-6 at 3/C"), "ev2": ev("ev2", "crop", 1)}
    cand = {"checkId": "C01", "kind": "conflict", "element": "", "location": "", "evidenceIds": ["ev1", "ev2"]}
    assert "two sources" in rfi_review.guard(cand, _decision(), evidence, [])
    evidence["ev3"] = ev("ev3", "page", 2)
    cand["evidenceIds"].append("ev3")
    assert rfi_review.guard(cand, _decision(), evidence, []) is None


def test_the_question_may_not_introduce_a_number_or_a_mark():
    evidence = {"ev1": ev("ev1", "text", 1, "COLUMN C-6 AT GRID 3/C"), "ev2": ev("ev2", "crop", 2)}
    cand = {"checkId": "C01", "kind": "conflict", "element": "", "location": "", "evidenceIds": ["ev1", "ev2"]}
    assert "14" in rfi_review.guard(cand, _decision(question="Is C-6 a 14 x 30 column?"), evidence, [])
    assert "C-9" in rfi_review.guard(cand, _decision(question="Is it C-9?"), evidence, [])


def test_a_member_size_is_checked_digit_by_digit():
    """The digits in W14x90 touch letters, which the scan's number pattern
    skips — so a size nothing showed would pass unchecked."""
    evidence = {"ev1": ev("ev1", "text", 1, "COLUMN C-6 AT GRID 3/C"), "ev2": ev("ev2", "crop", 2)}
    cand = {"checkId": "C01", "kind": "conflict", "element": "", "location": "", "evidenceIds": ["ev1", "ev2"]}
    assert rfi_review.guard(cand, _decision(question="Should C-6 be a W14x90 at 3/C?"), evidence, []) is not None
    evidence["ev1"].text += " W14X90"
    assert rfi_review.guard(cand, _decision(question="Should C-6 be a W14x90 at 3/C?"), evidence, []) is None


def test_what_discovery_saw_in_an_image_can_be_quoted():
    """The only record of what a crop SHOWS is the observation made from it, so
    a number read there is grounded — but only for a candidate citing it."""
    evidence = {"ev1": ev("ev1", "text", 1, "COLUMN C-6 AT GRID 3/C"), "ev2": ev("ev2", "crop", 2)}
    obs = [{"checkId": "C01", "statement": "the close-up shows C-6 (14 x 30)", "evidenceIds": ["ev2"],
            "element": "", "location": ""}]
    cand = {"checkId": "C01", "kind": "conflict", "element": "", "location": "", "evidenceIds": ["ev1", "ev2"]}
    assert rfi_review.guard(cand, _decision(question="Is C-6 14 x 30 at 3/C?"), evidence, obs) is None
    cand["evidenceIds"] = ["ev1", "ev9"]
    evidence["ev9"] = ev("ev9", "page", 3)
    assert rfi_review.guard(cand, _decision(question="Is C-6 14 x 30 at 3/C?"), evidence, obs) is not None


def test_the_fingerprint_is_the_issue_not_its_wording():
    evidence = {"ev1": ev("ev1", "text", 1, "COLUMN C-6 AT GRID 3/C"), "ev2": ev("ev2", "crop", 2),
                "ev3": ev("ev3", "description", 3, "C-6")}
    a = {"checkId": "C01", "kind": "conflict", "element": "Column C-6", "location": "3/C", "evidenceIds": ["ev1", "ev2"]}
    b = dict(a, element="column c6 ", evidenceIds=["ev2", "ev1", "ev3"])
    assert rfi_review.fingerprint(a, evidence, []) == rfi_review.fingerprint(b, evidence, [])
    assert rfi_review.fingerprint(a, evidence, []) != rfi_review.fingerprint(dict(a, kind="missing"), evidence, [])
    assert rfi_review.fingerprint(a, evidence, []) != rfi_review.fingerprint(dict(a, checkId="C02"), evidence, [])


# --- crops -----------------------------------------------------------------------


def test_a_crop_is_padded_to_a_minimum_and_clamped_to_the_page():
    page = fitz.Rect(0, 0, 1000, 800)
    box = rfi_review.crop_box({"x": 5, "y": 5, "width": 10, "height": 10}, page)
    assert box.x0 == 0 and box.y0 == 0
    assert box.width >= rfi_review.CROP_MIN_PT / 2
    wide = rfi_review.crop_box({"x": 100, "y": 100, "width": 400, "height": 20}, page)
    assert wide.width == pytest.approx(400 + 2 * rfi_review.CROP_PAD_PT)


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_a_crop_frames_its_chunk_at_every_rotation(monkeypatch, rotation):
    """Chunk boxes are in the unrotated page; rendering is in the displayed one.
    Whatever region is rendered must contain the words the chunk was cut from."""
    doc = fitz.open()
    page = doc.new_page(width=1200, height=800)
    page.insert_text((700, 600), "COLUMN C-6 (14 x 30)", fontsize=12)
    page.set_rotation(rotation)
    word = [w for w in page.get_text("words") if w[4] == "C-6"][0]
    bbox = {"x": word[0], "y": word[1], "width": word[2] - word[0], "height": word[3] - word[1]}
    shown: list[fitz.Rect] = []

    import vlm

    monkeypatch.setattr(vlm, "render", lambda p, edge: b"png")
    monkeypatch.setattr(vlm, "render_crop", lambda p, rect, edge: shown.append(rect) or b"png")
    scope = {
        "sides": [{"label": "S1", "pageIds": ["p"]}],
        "pages": [{"pageId": "p", "documentId": "d", "pageNumber": 1, "combinedPageNumber": 1,
                   "sheetNumber": "S1", "visual": True, "crops": [{"chunkId": "c", "bbox": bbox}]}],
        "chunks": [],
    }
    items = rfi_review.image_evidence(scope, lambda d, n: page, start=1)
    assert [e.kind for e in items] == ["page", "crop"]
    assert items[1].side == "S1" and items[1].chunk_id == "c"
    back = fitz.Rect(shown[0] * page.derotation_matrix).normalize()
    assert "C-6" in page.get_text("text", clip=back)
    stored = items[1].bbox
    assert "C-6" in page.get_text("text", clip=fitz.Rect(stored["x"], stored["y"], stored["x"] + stored["width"],
                                                         stored["y"] + stored["height"]))


# --- the whole job, against a real database ----------------------------------------

TEST_DB = os.environ.get("RFI_TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(
    not TEST_DB or not CLIENT_PDF.exists(),
    reason="set RFI_TEST_DATABASE_URL to a migrated database (and RFI_REVIEW_TEST_PDF to the S2.105/A3.01 set)",
)


@pytest.fixture
def database(monkeypatch):
    import config
    import db
    import llm
    import rfi_scan
    import storage

    monkeypatch.setattr(config, "DATABASE_URL", TEST_DB)
    monkeypatch.setattr(db, "_pool", None)
    monkeypatch.setattr(db, "_pool_unavailable", True)
    monkeypatch.setattr(rfi_scan, "_redis", False)
    monkeypatch.setattr(rfi_review, "HEARTBEAT_SECONDS", 0.05)
    monkeypatch.setattr(rfi_review, "CALL_RETRY_DELAYS", (0.01, 0.01))
    monkeypatch.setattr(storage, "download_to_file", lambda key, path: shutil.copy(CLIENT_PDF, path))
    monkeypatch.setattr(llm, "available", lambda provider: True)
    stored: dict[str, bytes] = {}
    monkeypatch.setattr(storage, "put_bytes", lambda key, data, content_type: stored.__setitem__(key, data))
    db.stored_images = stored
    return db


def _chunks_of(page: fitz.Page, limit: int) -> list[dict]:
    """Real text blocks off the client sheet, in the unrotated space chunks use."""
    out = []
    for b in page.get_text("blocks"):
        text = b[4].strip()
        if len(text) > 20:
            out.append({"text": text, "bbox": {"x": b[0], "y": b[1], "width": b[2] - b[0], "height": b[3] - b[1]}})
        if len(out) == limit:
            break
    return out


def _seed(db, checks=("G01", "C01"), limits: dict | None = None) -> dict:
    project, document, user = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    pdf = fitz.open(CLIENT_PDF)
    sheets = [("S2.105", "structural"), ("A3.01", "architectural")]
    scope = {"sides": [], "pages": [], "chunks": []}
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO users (id, email, name, \"passwordHash\") VALUES (%s, %s, 'review test', 'x')",
            (user, f"{user}@test.invalid"),
        )
        conn.execute('INSERT INTO projects (id, name, "ownerId") VALUES (%s, \'rfi review test\', %s)', (project, user))
        conn.execute(
            'INSERT INTO documents (id, "projectId", filename, "spacesKey", pages, status) '
            "VALUES (%s, %s, 'set.pdf', 'k', 2, 'completed')",
            (document, project),
        )
        for n, (sheet, discipline) in enumerate(sheets, start=1):
            page_id = str(uuid.uuid4())
            conn.execute(
                'INSERT INTO pages (id, "documentId", "pageNumber", "combinedPageNumber", "sheetNumber", discipline) '
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (page_id, document, n, n, sheet, discipline),
            )
            crops = []
            for i, c in enumerate(_chunks_of(pdf[n - 1], 4)):
                chunk_id = str(uuid.uuid4())
                conn.execute(
                    'INSERT INTO chunks (id, "pageId", text, bbox, "tokenCount", kind) VALUES (%s, %s, %s, %s, 20, \'text\')',
                    (chunk_id, page_id, c["text"], json.dumps(c["bbox"])),
                )
                scope["chunks"].append({"chunkId": chunk_id, "pageId": page_id, "kind": "text", "text": c["text"],
                                        "bbox": c["bbox"], "tokenCount": 20, "score": 1 / (60 + i)})
                if i == 0:
                    crops.append({"chunkId": chunk_id, "bbox": c["bbox"]})
            scope["sides"].append({"label": sheet, "pageIds": [page_id]})
            scope["pages"].append({"pageId": page_id, "documentId": document, "pageNumber": n,
                                   "combinedPageNumber": n, "sheetNumber": sheet, "discipline": discipline,
                                   "pdfWidth": None, "pdfHeight": None, "role": f"side:{n - 1}",
                                   "visual": True, "crops": crops})
        run_id = str(uuid.uuid4())
        conn.execute(
            'INSERT INTO rfi_review_runs (id, "projectId", "createdById", target, "checkIds", status, provider, '
            '"thinkingRequested", scope, "scopeHash", limits) VALUES (%s, %s, %s, %s, %s, \'queued\', \'claude\', \'low\', %s, \'h\', %s)',
            (run_id, project, user, json.dumps({"type": "compare", "values": ["S2.105", "A3.01"]}),
             json.dumps(list(checks)), json.dumps(scope), json.dumps(limits) if limits else None),
        )
    return {"project": project, "run": run_id, "scope": scope, "user": user}


class FakeModel:
    """Answers each stage from the prompt it was actually sent, citing evidence
    ids it finds there — plus one it invents, which the job must drop."""

    def __init__(self, keep_question: str | None = None, fail_stage: str | None = None, on_call=None,
                 busy: dict | None = None):
        self.calls: list[dict] = []
        # {stage: n}: the first n calls of that stage fail, like a provider 503.
        self.busy = dict(busy or {})
        self.keep_question = keep_question or (
            "S2.105 shows column C-6 at this crossing and A3.01 shows no column there. Which is correct?"
        )
        self.fail_stage = fail_stage
        self.on_call = on_call

    def __call__(self, system, user, **kw):
        import llm

        stage = "discovery" if "DISCOVERY" in system else "reasoning" if "REASONING" in system else "verification"
        self.calls.append({"stage": stage, "user": user, **kw})
        if self.on_call:
            self.on_call(stage)
        if stage == self.fail_stage:
            return None
        if self.busy.get(stage, 0) > 0:
            self.busy[stage] -= 1
            return None
        if stage == "discovery":
            s = re.search(r'<evidence id="(ev\d+)" kind="text" sheet="S2.105"', user).group(1)
            a = re.search(r'<evidence id="(ev\d+)" kind="text" sheet="A3.01"', user).group(1)
            crop = re.search(r'<evidence id="(ev\d+)" kind="crop" sheet="S2.105"', user).group(1)
            body = {"observations": [
                {"checkId": "C01", "element": "column C-6", "location": "3/C",
                 "statement": "S2.105 shows column C-6 at this crossing", "evidenceIds": [s, crop]},
                {"checkId": "C01", "element": "column", "location": "3/C",
                 "statement": "A3.01 shows no column at this crossing", "evidenceIds": [a]},
                {"checkId": "C01", "statement": "invented", "evidenceIds": ["ev999"]},
            ]}
            self.ids = (s, a, crop)
        elif stage == "reasoning":
            s, a, crop = self.ids
            body = {"candidates": [
                {"checkId": "C01", "kind": "conflict", "element": "column C-6", "location": "3/C",
                 "issue": "column shown on one sheet only", "whyClarificationRequired": "the sheets disagree",
                 "evidenceIds": [s, crop, a]},
                {"checkId": "C01", "kind": "conflict", "element": "column", "location": "",
                 "issue": "one page only", "evidenceIds": [s]},
            ]}
        else:
            body = {"decisions": [
                {"index": 0, "decision": "keep", "subject": "Column C-6 on S2.105 but not A3.01",
                 "question": self.keep_question, "why": "the plans disagree", "priority": "high",
                 "confidence": "medium"},
                {"index": 1, "decision": "keep", "subject": "One sheet", "question": "Is it right?"},
            ]}
        return llm.Reply(text=json.dumps(body), stop_reason="end_turn", model="stub-model", input_tokens=100,
                         output_tokens=50, thinking="effort=low")


def _row(db, run_id):
    with db.connect() as conn:
        return conn.execute(
            'SELECT status::text, stage, error, usage, notes, "thinkingSent", "heartbeatAt" FROM rfi_review_runs WHERE id = %s',
            (run_id,),
        ).fetchone()


def _candidates(db, project):
    with db.connect() as conn:
        return conn.execute(
            'SELECT "checkType", origin, "reviewRunId", subject, question, "questionSource", evidence, priority, '
            'reasoning, status::text, fingerprint FROM rfi_candidates WHERE "projectId" = %s ORDER BY "checkType"',
            (project,),
        ).fetchall()


@needs_db
def test_a_review_saves_verified_candidates_and_reports_into_its_row(database, monkeypatch):
    import llm

    seeded = _seed(database)
    model = FakeModel()
    monkeypatch.setattr(llm, "complete", model)
    result = rfi_review.run(seeded["run"])

    assert [c["stage"] for c in model.calls] == ["discovery", "reasoning", "verification"]
    discovery = model.calls[0]
    # Two visual pages: an overview and one crop each, every image labelled.
    assert len(discovery["images"]) == 4 and len(discovery["image_labels"]) == 4
    assert discovery["image_labels"][0].endswith("S2.105, whole sheet")
    assert discovery["thinking"] == "low" and discovery["claude_model"] == rfi_review.REVIEW_MODEL
    assert model.calls[1]["images"] is None  # reasoning is text only

    status, stage, error, usage, notes, sent, beat = _row(database, seeded["run"])
    assert (status, error) == ("ready", None) and beat is not None
    assert usage["stages"]["discovery"]["calls"] == 1 and usage["total"]["calls"] == 3
    assert usage["total"]["inputTokens"] == 300 and sent == ["effort=low"]
    assert any("1 observation(s) dropped" in n for n in notes)
    assert any("two sources" in n for n in notes)  # the one-page conflict, rejected by the code

    with database.connect() as conn:
        results, manifest, coverage = conn.execute(
            'SELECT "checkResults", "evidenceManifest", coverage FROM rfi_review_runs WHERE id = %s', (seeded["run"],)
        ).fetchone()
        tagged = conn.execute(
            'SELECT stage, attempt FROM usage_events WHERE "reviewRunId" = %s ORDER BY "createdAt"', (seeded["run"],)
        ).fetchall()
    # Every one of the 16 questions has an outcome; the unselected say so.
    assert len(results) == 16 and results["C01"]["outcome"] == "candidate_found"
    assert results["G01"]["outcome"] == "candidate_found" and results["F04"]["outcome"] == "not_selected"
    # Every id the server issued is in the manifest, and each picture was kept.
    assert {m["evidenceId"] for m in manifest} >= set(re.findall(r'id="(ev\d+)"', discovery["user"]))
    pictures = [m for m in manifest if m["kind"] in ("page", "crop")]
    assert pictures and all(m["imageKey"] in database.stored_images for m in pictures)
    assert coverage["omissions"] == []
    # The ledger knows which run and stage paid for each call.
    assert [t[0] for t in tagged] == ["discovery", "reasoning", "verification"] or tagged == []

    rows = _candidates(database, seeded["project"])
    by_check = {r[0]: r for r in rows}
    review = by_check["C01"]
    assert review[1:3] == ("targeted_review", seeded["run"])
    assert review[5] == "model" and review[7] == "high"
    pages = {(e["pageNumber"], e["kind"]) for e in review[6]}
    assert pages == {(1, "text"), (1, "crop"), (2, "text")}
    assert all(e["sourceTrust"] for e in review[6])
    # G01 ran the scan's exact grid comparison on these two sheets.
    assert result["gridFindings"] >= 1
    grid = by_check["grid_mismatch"]
    assert grid[1:3] == ("targeted_review", seeded["run"]) and grid[5] == "template"


@needs_db
def test_a_rerun_keeps_a_dismissed_finding_dismissed(database, monkeypatch):
    import llm

    seeded = _seed(database, checks=("C01",))
    monkeypatch.setattr(llm, "complete", FakeModel())
    rfi_review.run(seeded["run"])
    with database.connect() as conn:
        conn.execute("UPDATE rfi_candidates SET status = 'dismissed' WHERE \"projectId\" = %s", (seeded["project"],))
        again = str(uuid.uuid4())
        conn.execute(
            # createdById too: without it the access re-check cancels the run
            # before it saves anything, and this test passed without a re-run.
            'INSERT INTO rfi_review_runs (id, "projectId", "createdById", target, "checkIds", status, scope, "scopeHash") '
            "SELECT %s, \"projectId\", \"createdById\", target, \"checkIds\", 'queued', scope, 'h' FROM rfi_review_runs WHERE id = %s",
            (again, seeded["run"]),
        )
    monkeypatch.setattr(llm, "complete", FakeModel(keep_question="S2.105 and A3.01 disagree about C-6. Which is correct?"))
    assert rfi_review.run(again).get("cancelled") is None
    rows = _candidates(database, seeded["project"])
    assert len(rows) == 1
    assert rows[0][9] == "dismissed" and rows[0][2] == seeded["run"]
    # The second run found the problem again. It must say where the existing
    # finding is, not read as "no problems" because it added nothing.
    with database.connect() as conn:
        results, notes = conn.execute(
            'SELECT "checkResults", notes FROM rfi_review_runs WHERE id = %s', (again,)
        ).fetchone()
    assert results["C01"]["outcome"] == "candidate_found" and results["C01"]["candidates"] == 0
    assert "dismissed earlier" in results["C01"]["reason"]
    assert any("Found again" in n and "dismissed earlier" in n for n in notes)


@needs_db
def test_an_ungrounded_question_is_rejected_not_saved(database, monkeypatch):
    import llm

    seeded = _seed(database, checks=("C01",))
    monkeypatch.setattr(llm, "complete", FakeModel(keep_question="Should C-6 be a W14x90 at 3/C?"))
    rfi_review.run(seeded["run"])
    assert _candidates(database, seeded["project"]) == []
    assert any("W14" in n or "90" in n for n in _row(database, seeded["run"])[4])


@needs_db
def test_a_failed_call_fails_the_run_and_saves_nothing(database, monkeypatch):
    import llm

    seeded = _seed(database, checks=("C01",))
    monkeypatch.setattr(llm, "complete", FakeModel(fail_stage="reasoning"))
    result = rfi_review.run(seeded["run"])
    status, stage, error, *_ = _row(database, seeded["run"])
    assert (status, stage) == ("failed", "reasoning") and "failed 3 times" in error
    assert result["stage"] == "reasoning"
    assert _candidates(database, seeded["project"]) == []


@needs_db
def test_a_cancel_between_stages_stops_the_run(database, monkeypatch):
    import llm

    seeded = _seed(database, checks=("C01",))

    def cancel(stage):
        if stage == "discovery":
            with database.connect() as conn:
                conn.execute("UPDATE rfi_review_runs SET status = 'cancelled' WHERE id = %s", (seeded["run"],))

    model = FakeModel(on_call=cancel)
    monkeypatch.setattr(llm, "complete", model)
    assert rfi_review.run(seeded["run"]) == {"cancelled": True}
    assert [c["stage"] for c in model.calls] == ["discovery"]
    assert _row(database, seeded["run"])[0] == "cancelled"


@needs_db
def test_a_scope_whose_evidence_was_reprocessed_goes_stale_before_any_call(database, monkeypatch):
    import llm

    seeded = _seed(database, checks=("C01",))
    with database.connect() as conn:
        conn.execute("DELETE FROM chunks WHERE id = %s", (seeded["scope"]["chunks"][0]["chunkId"],))
    model = FakeModel()
    monkeypatch.setattr(llm, "complete", model)
    assert "stale" in rfi_review.run(seeded["run"])
    assert model.calls == []
    status, _, error, *_ = _row(database, seeded["run"])
    assert status == "stale" and "re-processed" in error


@needs_db
def test_a_busy_provider_is_asked_again_instead_of_failing_the_run(database, monkeypatch):
    """A Gemini 503 "high demand" on the reasoning call failed a whole review
    and threw away the discovery call already paid for."""
    import llm

    seeded = _seed(database, checks=("C01",))
    model = FakeModel(busy={"reasoning": 2})
    monkeypatch.setattr(llm, "complete", model)
    rfi_review.run(seeded["run"])
    status, _, error, usage, *_ = _row(database, seeded["run"])
    assert (status, error) == ("ready", None)
    assert [c["stage"] for c in model.calls] == ["discovery", "reasoning", "reasoning", "reasoning", "verification"]
    assert usage["stages"]["reasoning"]["failedCalls"] == 2 and usage["stages"]["reasoning"]["calls"] == 1


# --- C03: occurrences measured before an offset is called missing ----------------


class ColumnClaimModel:
    """Makes the claim the first real forming-plan review made: ONE missing-
    offset RFI over several column marks, cited to a close-up and the C03
    aid. What the job does with it is the test; the stub says nothing about
    what a real model would claim."""

    def __init__(self, marks: str):
        self.marks = marks
        self.calls: list[dict] = []

    def __call__(self, system, user, **kw):
        import llm

        stage = "discovery" if "DISCOVERY" in system else "reasoning" if "REASONING" in system else "verification"
        self.calls.append({"stage": stage, "user": user, **kw})
        if stage == "discovery":
            crop = re.search(r'<evidence id="(ev\d+)" kind="crop" sheet="S2.105"', user).group(1)
            aid = re.search(r'<evidence id="(ev\d+)" kind="aid" sheet="S2.105"', user).group(1)
            self.ids = [crop, aid]
            body = {"observations": [{"checkId": "C03", "element": f"columns {self.marks}", "location": "level 5",
                                      "statement": f"no offset dimension is shown beside {self.marks}",
                                      "evidenceIds": self.ids}]}
        elif stage == "reasoning":
            body = {"candidates": [{"checkId": "C03", "kind": "missing", "element": f"columns {self.marks}",
                                    "location": "level 5 forming plan", "issue": "no offset dimension from grid",
                                    "impact": "columns could be cast in the wrong place, a structural risk",
                                    "evidenceIds": self.ids}]}
        else:
            body = {"decisions": [{"index": 0, "decision": "keep", "subject": f"Missing grid offsets for {self.marks}",
                                   "question": f"Please provide offsets from grid for {self.marks}.",
                                   "why": "no dimension is shown", "priority": "high", "confidence": "high"}]}
        return llm.Reply(text=json.dumps(body), stop_reason="end_turn", model="stub-model", input_tokens=100,
                         output_tokens=50, thinking="effort=low")


@needs_db
def test_a_grouped_column_claim_is_narrowed_to_the_occurrences_that_are_off_grid(database, monkeypatch):
    """C-12 (D/4.7) and C-17 (B1.6/4.6) stand on their crossings: removed.
    C-8 (C.1/5) and C-16 (E/5) stand off grid line 5: kept, each clouded at
    its own column. The question is rebuilt from the measured occurrences."""
    import llm

    seeded = _seed(database, checks=("C03",))
    model = ColumnClaimModel("C-8, C-12, C-16, C-17")
    monkeypatch.setattr(llm, "complete", model)
    rfi_review.run(seeded["run"])
    assert [c["stage"] for c in model.calls] == ["discovery", "reasoning", "verification"]
    rows = _candidates(database, seeded["project"])
    assert len(rows) == 1
    check, _, _, subject, question, source, evidence, _, reasoning, *_ = rows[0]
    assert check == "C03" and source == "template"
    assert "C-8 near C.1/5" in question and "C-16 near E/5" in question
    assert "C-12" not in question and "C-17" not in question
    assert "Removed (centred on grid lines both ways): C-12 near D/4.7; C-17 near B1.6/4.6" in reasoning
    assert "structural risk" not in reasoning  # an inferred consequence is not appended
    findings = [e for e in evidence if e["role"] == "finding"]
    assert sorted(e["occurrence"]["occurrence"] for e in findings) == ["C-16@E/5", "C-8@C.1/5"]
    page = fitz.open(CLIENT_PDF)[0]
    for e in findings:
        box = fitz.Rect(e["bbox"]["x"], e["bbox"]["y"], e["bbox"]["x"] + e["bbox"]["width"], e["bbox"]["y"] + e["bbox"]["height"])
        assert e["occurrence"]["mark"] in {w[4] for w in page.get_text("words", clip=box)}
        assert box.width * box.height < 0.005 * page.rect.width * page.rect.height
    # The picture the model looked at of this sheet is not a location.
    assert not [e for e in evidence if e["kind"] in ("page", "crop")]
    with database.connect() as conn:
        results, = conn.execute('SELECT "checkResults" FROM rfi_review_runs WHERE id = %s', (seeded["run"],)).fetchone()
    assert results["C03"]["outcome"] == "candidate_found"
    measured = {o["occurrence"]: o["status"] for o in results["C03"]["occurrences"]}
    assert measured["C-12@D/4.7"] == "located_by_grid" and measured["C-17@B1.6/4.6"] == "located_by_grid"


@needs_db
def test_a_claim_about_centred_columns_is_rejected_before_verification_is_paid_for(database, monkeypatch):
    import llm

    seeded = _seed(database, checks=("C03",))
    model = ColumnClaimModel("C-12, C-17")
    monkeypatch.setattr(llm, "complete", model)
    rfi_review.run(seeded["run"])
    assert [c["stage"] for c in model.calls] == ["discovery", "reasoning"]
    assert _candidates(database, seeded["project"]) == []
    status, _, _, _, notes, *_ = _row(database, seeded["run"])
    assert status == "ready"
    assert any("every occurrence it names is centred" in n for n in notes)
    with database.connect() as conn:
        results, = conn.execute('SELECT "checkResults" FROM rfi_review_runs WHERE id = %s', (seeded["run"],)).fetchone()
    # The sheet still has columns nobody located: the question is not "no issue".
    assert results["C03"]["outcome"] == "insufficient_evidence" and results["C03"]["gaps"]


# --- what the grid comparison looked at ------------------------------------------


def _page(pid: str, sheet: str, discipline: str | None):
    from rfi_checks import Page

    return Page(pid, "d", 1, 1, sheet, None, discipline)


def _system(pid: str):
    from rfi_grid import GridSystem

    return GridSystem(pid, "red 27", {"1": 0.0, "2": 100.0, "3": 200.0}, {"A": 0.0, "B": 100.0, "C": 200.0}, {})


def test_two_sheets_of_one_discipline_say_nothing_was_compared():
    """Run 4 of a real review: two structural sheets, "0 disagreements", and
    no way to tell that from a clean comparison."""
    pages = [_page("a", "S2.105", "structural"), _page("b", "S2.106", "structural")]
    note = rfi_review.grid_scope_note(pages, [_system("a"), _system("b")], 0, [])
    assert "Nothing was compared" in note and "Compare sheets" in note
    assert "S2.105 (structural)" in note and "S2.106 (structural)" in note


def test_sheets_of_two_disciplines_name_the_pair_compared():
    pages = [_page("a", "S2.105", "structural"), _page("b", "A3.01", "architectural")]
    note = rfi_review.grid_scope_note(pages, [_system("a"), _system("b")], 1, [])
    assert "Compared: S2.105 with A3.01" in note and "Nothing was compared" not in note


def test_pages_without_a_grid_are_named():
    pages = [_page("a", "S2.105", "structural"), _page("b", "S6.01", "structural")]
    note = rfi_review.grid_scope_note(pages, [_system("a")], 0, [])
    assert "No grid bubbles read on S6.01" in note
    none = rfi_review.grid_scope_note(pages, [], 0, ["Grid check found no grid bubbles on any sheet"])
    assert none.count("grid bubbles") == 1 and "nothing was compared" in none


# --- the column overlay, through the job ---------------------------------------------


@pytest.mark.skipif(not TEST_DB, reason="set RFI_TEST_DATABASE_URL to a migrated database")
def test_a_review_lays_an_enlarged_plan_over_its_overall_plan(database, monkeypatch):
    """RFI 015's shape, drawn: an enlarged plan (1/4") with one column the
    overall plan (1/8") does not show. C01's exact half must save it without
    any model involvement, and the model must be shown the SAME area of both
    sheets as labelled pairs."""
    import llm
    import storage

    import test_plan_match as drawn

    doc = drawn.build(drawn.COLUMNS + [(36, 20, 2, 2)])
    monkeypatch.setattr(storage, "download_to_file", lambda key, path: doc.save(path))
    project, document, run_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    scope = {"sides": [], "pages": [], "chunks": []}
    user = str(uuid.uuid4())
    with database.connect() as conn:
        conn.execute(
            "INSERT INTO users (id, email, name, \"passwordHash\") VALUES (%s, %s, 'overlay test', 'x')",
            (user, f"{user}@test.invalid"),
        )
        conn.execute('INSERT INTO projects (id, name, "ownerId") VALUES (%s, \'overlay test\', %s)', (project, user))
        conn.execute(
            'INSERT INTO documents (id, "projectId", filename, "spacesKey", pages, status) '
            "VALUES (%s, %s, 'exhibit.pdf', 'k', 2, 'completed')",
            (document, project),
        )
        for n, sheet in ((1, "A3.27"), (2, "A3.35")):
            page_id, chunk_id = str(uuid.uuid4()), str(uuid.uuid4())
            conn.execute(
                'INSERT INTO pages (id, "documentId", "pageNumber", "combinedPageNumber", "sheetNumber", discipline) '
                "VALUES (%s, %s, %s, %s, %s, 'architectural')",
                (page_id, document, n, n, sheet),
            )
            conn.execute(
                'INSERT INTO chunks (id, "pageId", text, bbox, "tokenCount", kind) '
                "VALUES (%s, %s, %s, '{\"x\": 300, \"y\": 990, \"width\": 200, \"height\": 12}', 5, 'text')",
                (chunk_id, page_id, f"CONCRETE EXHIBIT {sheet}"),
            )
            scope["sides"].append({"label": sheet, "pageIds": [page_id]})
            scope["pages"].append({"pageId": page_id, "documentId": document, "pageNumber": n,
                                   "combinedPageNumber": n, "sheetNumber": sheet, "discipline": "architectural",
                                   "role": f"side:{n - 1}", "visual": True, "crops": []})
            scope["chunks"].append({"chunkId": chunk_id, "pageId": page_id, "kind": "text",
                                    "text": f"CONCRETE EXHIBIT {sheet}", "bbox": None, "tokenCount": 5, "score": 0})
        conn.execute(
            'INSERT INTO rfi_review_runs (id, "projectId", "createdById", target, "checkIds", status, provider, "thinkingRequested", '
            'scope, "scopeHash") VALUES (%s, %s, %s, %s, %s, \'queued\', \'claude\', \'low\', %s, \'h\')',
            (run_id, project, user, json.dumps({"type": "compare", "values": ["A3.27", "A3.35"]}),
             json.dumps(["C01"]), json.dumps(scope)),
        )

    calls = []

    def quiet_model(system, user, **kw):
        calls.append(kw)
        body = {"observations": []} if "DISCOVERY" in system else {"candidates": []}
        return llm.Reply(text=json.dumps(body), stop_reason="end_turn", model="stub", input_tokens=10, output_tokens=5)

    monkeypatch.setattr(llm, "complete", quiet_model)
    result = rfi_review.run(run_id)

    assert result["columnFindings"] == 1
    labels = calls[0]["image_labels"]
    pairs = [label for label in labels if "Pair 1 of" in label]
    assert len(pairs) == 2
    assert "first half: A3.35 detail 1 (1/4\" = 1'-0\")" in pairs[0]
    assert "second half: the same area on A3.27 (1/8\" = 1'-0\")" in pairs[1]
    with database.connect() as conn:
        rows = conn.execute(
            'SELECT "checkType", origin, "questionSource", question, evidence FROM rfi_candidates WHERE "projectId" = %s',
            (project,),
        ).fetchall()
        notes = conn.execute('SELECT notes FROM rfi_review_runs WHERE id = %s', (run_id,)).fetchone()[0]
    assert [(r[0], r[1], r[2]) for r in rows] == [("column_mismatch", "targeted_review", "template")]
    assert "A3.35 shows a 2'-0\" x 2'-0\" column" in rows[0][3]
    assert {e["sheetNumber"] for e in rows[0][4]} == {"A3.35", "A3.27"}
    assert any("line up, 1 differ" in n for n in notes)
