"""The diagnostic export (RFI_DIAGNOSTICS=on): what it captures, what it
never captures, and a full scan recorded end to end through the REAL
transport, with only the provider's SDK client faked.

The fake client is the only stub: llm.complete builds the request exactly as
it would for Anthropic, the hook captures it at the SDK boundary, and the
export is checked against what the fake client actually RECEIVED — so a test
that passed while the export described a different request is not possible.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import diagnostics  # noqa: E402

CLIENT_PDF = Path(os.environ.get("RFI_REVIEW_TEST_PDF") or "/nonexistent")
TEST_DB = os.environ.get("RFI_TEST_DATABASE_URL")
FAKE_KEY = "sk-ant-api03-THISISNOTAREALKEYBUTLOOKSLIKEONE0123456789"
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAIAAAADCAIAAAA2iEnWAAAAEklEQVR4nGP8z8DAwMDAxMDAAAAIIwECuSV8RwAAAABJRU5ErkJggg=="
)


# --- pure ------------------------------------------------------------------------------


def test_secrets_signed_urls_and_reasoning_are_removed():
    raw = {
        "headers": {"x-api-key": FAKE_KEY},
        "Authorization": "Bearer abc",
        "note": f"key {FAKE_KEY} and https://bucket.example/x.pdf?X-Amz-Signature=deadbeef&X-Amz-Credential=a",
        "content": [
            {"type": "thinking", "thinking": "SECRET CHAIN OF THOUGHT", "signature": "sig"},
            {"type": "redacted_thinking", "data": "opaque"},
            {"type": "text", "text": "the answer"},
        ],
        "parts": [{"text": "a thought", "thought": True}, {"text": "visible"}],
    }
    out = json.dumps(diagnostics.scrub(raw))
    for secret in (FAKE_KEY, "Bearer abc", "X-Amz-Signature", "SECRET CHAIN OF THOUGHT", "opaque", "a thought", '"sig"'):
        assert secret not in out
    assert "the answer" in out and "visible" in out
    assert "[signed URL removed]" in out and diagnostics.THINKING_REMOVED in out


def test_nothing_is_recorded_when_the_mode_is_off(monkeypatch, tmp_path):
    monkeypatch.delenv("RFI_DIAGNOSTICS", raising=False)
    monkeypatch.setenv("RFI_DIAGNOSTICS_DIR", str(tmp_path))
    assert diagnostics.start("full-scan", "r1", "p1", {}) is None
    with diagnostics.call("discovery") as c:
        assert c is None
        diagnostics.sent("anthropic", {"model": "m"})  # a no-op, not an error
    assert list(tmp_path.iterdir()) == []


def test_a_request_is_kept_exactly_with_each_image_as_a_file(monkeypatch, tmp_path):
    monkeypatch.setenv("RFI_DIAGNOSTICS", "on")
    monkeypatch.setenv("RFI_DIAGNOSTICS_DIR", str(tmp_path))
    monkeypatch.setenv("RFI_DIAGNOSTICS_UPLOAD", "off")
    rec = diagnostics.start("review", "run-1", "p1", {"request": "x"})
    try:
        with rec.call("discovery", attempt=1) as c:
            c.describe_images([{"sheetNumber": "S1.01", "crop": [0, 0, 10, 10]}])
            request = {
                "model": "claude-test", "max_tokens": 100,
                "system": [{"type": "text", "text": "SYSTEM TEXT"}],
                "messages": [{"role": "user", "content": [
                    {"type": "text", "text": "Image A"},
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                 "data": base64.b64encode(PNG).decode()}},
                    {"type": "text", "text": "USER TEXT"},
                ]}],
            }
            diagnostics.sent("anthropic", request)
            diagnostics.received("anthropic", {"model": "claude-test-20260101", "stop_reason": "end_turn",
                                               "content": [{"type": "text", "text": "{}"}], "usage": {"input_tokens": 5}})
    finally:
        rec.finish("ready")
        diagnostics.stop()
    folder = rec.folder
    image = folder / "images" / "c0001-a1-01.png"
    assert image.read_bytes() == PNG
    body = json.loads((folder / "calls/c0001-discovery/request.json").read_text())
    assert "data" not in json.dumps(body)  # no base64 inside the JSON
    assert body["request"]["messages"][0]["content"][1]["source"]["sha256"] == hashlib.sha256(PNG).hexdigest()
    md = (folder / "calls/c0001-discovery/request.md").read_text()
    assert md.index("SYSTEM TEXT") < md.index("Image A") < md.index("[IMAGE 1]") < md.index("USER TEXT")
    assert "2x3 px" in md and "S1.01" in md and "Temperature: not sent" in md
    meta = json.loads((folder / "calls/c0001-discovery/meta.json").read_text())
    assert meta["images"][0]["widthPx"] == 2 and meta["replies"][0]["modelReturned"] == "claude-test-20260101"
    manifest = json.loads((folder / "manifest.json").read_text())
    assert manifest["calls"][0]["callId"] == "c0001" and "images/c0001-a1-01.png" in manifest["files"]
    assert (folder.parent / f"{folder.name}.zip").exists()


def test_a_broken_recorder_never_breaks_the_call(monkeypatch, tmp_path):
    monkeypatch.setenv("RFI_DIAGNOSTICS", "on")
    monkeypatch.setenv("RFI_DIAGNOSTICS_DIR", str(tmp_path))
    rec = diagnostics.start("review", "run-2", "p1", {})
    try:
        with rec.call("discovery") as c:
            c.sent("anthropic", {"messages": [{"content": [{"type": "image", "source": {"type": "base64", "data": "!!"}}]}]})
            c.received("anthropic", object())
    finally:
        diagnostics.stop()


# --- a full scan, recorded end to end -----------------------------------------------------


class _Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def model_dump(self, **_):
        def dump(v):
            if isinstance(v, _Obj):
                return v.model_dump()
            if isinstance(v, list):
                return [dump(x) for x in v]
            return v
        return {k: dump(v) for k, v in self.__dict__.items()}


class FakeAnthropic:
    """Answers like the stub in test_fullscan_run, but at the SDK boundary,
    and records every request it was handed."""

    def __init__(self):
        self.requests: list[dict] = []
        self.messages = self

    def create(self, **request):
        self.requests.append(request)
        system = request["system"][0]["text"]
        content = request["messages"][0]["content"]
        if "confirm or reject" in system:
            user = content[-1]["text"]
            sheet = user.split("<sheet_a>")[1].split(",")[0]
            body = {"decision": "keep", "reason": "drawn on one only", "subject": f"Column on {sheet}",
                    "question": f"Sheet {sheet} shows a column here that the other sheet does not. Which is correct?",
                    "confidence": "high", "priority": "normal"}
        else:
            label = content[0]["text"]
            tile = int(label.rsplit("window ", 1)[1]) - 1
            body = {"issues": [] if tile != 1 else [{
                "checkId": "C01", "kind": "missing", "element": "column at window 2", "whatA": "a column",
                "whatB": "nothing", "boxA": [0.4, 0.4, 0.6, 0.6], "boxB": [0.4, 0.4, 0.6, 0.6], "confidence": "high"}]}
        return _Obj(
            id="msg_1", model="claude-stub-20260101", stop_reason="end_turn", type="message", role="assistant",
            content=[_Obj(type="thinking", thinking="PRIVATE REASONING", signature="s"),
                     _Obj(type="text", text=json.dumps(body))],
            usage=_Obj(input_tokens=4000, output_tokens=300, cache_read_input_tokens=0, cache_creation_input_tokens=0),
        )


needs_db = pytest.mark.skipif(
    not TEST_DB or not CLIENT_PDF.exists(),
    reason="set RFI_TEST_DATABASE_URL to a migrated database and RFI_REVIEW_TEST_PDF to the S2.105/A3.01 set",
)


@needs_db
def test_a_full_scan_exports_every_request_image_reply_and_decision(monkeypatch, tmp_path):
    import shutil

    import config
    import db
    import fullscan
    import fullscan_run as fr
    import llm
    import storage

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from test_fullscan_run import _seed

    monkeypatch.setenv("RFI_DIAGNOSTICS", "on")
    monkeypatch.setenv("RFI_DIAGNOSTICS_DIR", str(tmp_path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    monkeypatch.setattr(config, "DATABASE_URL", TEST_DB)
    monkeypatch.setattr(db, "_pool", None)
    monkeypatch.setattr(db, "_pool_unavailable", True)
    monkeypatch.setattr(fullscan, "HEARTBEAT_SECONDS", 0.05)
    monkeypatch.setattr(fr, "RETRY_DELAY", 0.01)
    monkeypatch.setattr(storage, "download_to_file", lambda key, path: shutil.copy(CLIENT_PDF, path))
    stored: dict[str, bytes] = {}
    monkeypatch.setattr(storage, "put_bytes", lambda key, data, content_type: stored.__setitem__(key, data))
    fake = FakeAnthropic()
    monkeypatch.setattr(llm, "anthropic_client", lambda: fake)

    seed = _seed(db)
    fullscan.handle(seed["scan"], "run")

    folders = [p for p in tmp_path.iterdir() if p.is_dir()]
    assert len(folders) == 1
    folder = folders[0]
    manifest = json.loads((folder / "manifest.json").read_text())
    assert manifest["status"] == "ready" and manifest["runId"] == seed["scan"]
    stages = sorted(c["stage"] for c in manifest["calls"])
    assert stages == ["discovery", "discovery", "discovery", "verification"]
    assert len(fake.requests) == 4

    # Every image the SDK was handed is in images/, byte for byte, in order.
    sent = [base64.b64decode(b["source"]["data"]) for r in fake.requests
            for b in r["messages"][0]["content"] if b["type"] == "image"]
    exported = sorted((folder / "images").iterdir())
    assert len(exported) == len(sent) == 8
    assert {hashlib.sha256(x).hexdigest() for x in sent} == {hashlib.sha256(p.read_bytes()).hexdigest() for p in exported}

    # Each image says what it is: the page, the crop and its coordinate system.
    meta = json.loads((folder / "calls" / "c0001-discovery" / "meta.json").read_text())
    source = meta["images"][0]["source"]
    assert source["sheetNumber"] == "S2.105" and source["pageNumberBase"] == 1 and len(source["crop"]) == 4
    assert "display space" in source["cropCoordinates"] and meta["meta"]["promptVersion"] == fr.PROMPT_VERSION

    # The decisions after the replies, and the finding linked to its calls.
    events = [json.loads(line) for line in (folder / "postprocess.jsonl").read_text().splitlines()]
    saved = [e for e in events if e["event"] == "finding_saved"]
    assert len(saved) == 1 and saved[0]["firstLookCalls"] and saved[0]["closeLookCalls"]
    findings = json.loads((folder / "output" / "findings.json").read_text())["findings"]
    assert len(findings) == 1 and findings[0]["linkedCalls"]["closeLookCalls"]
    assert (folder / "output" / "rfi-package.pdf").read_bytes()[:4] == b"%PDF"
    pages = json.loads((folder / "evidence" / "pages.json").read_text())
    assert {p["sheetNumber"] for p in pages["pages"]} == {"S2.105", "A3.01"}
    assert all(p["sheetRevision"] == "unknown" for p in pages["pages"])

    # Never: the key, the private reasoning.
    everything = "".join(p.read_text(errors="ignore") for p in folder.rglob("*") if p.suffix in (".json", ".md", ".jsonl", ".txt"))
    assert FAKE_KEY not in everything and "PRIVATE REASONING" not in everything
    assert diagnostics.THINKING_REMOVED in everything

    # And the zip went where the API serves it from.
    assert f"projects/{seed['project']}/rfi-diagnostics/full-scan-{seed['scan']}.zip" in stored


@needs_db
def test_a_targeted_review_exports_its_evidence_calls_and_findings(monkeypatch, tmp_path):
    import llm
    import rfi_review

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import test_rfi_review as tr

    monkeypatch.setenv("RFI_DIAGNOSTICS", "on")
    monkeypatch.setenv("RFI_DIAGNOSTICS_DIR", str(tmp_path))
    import shutil

    import config
    import db
    import rfi_scan
    import storage

    monkeypatch.setattr(config, "DATABASE_URL", TEST_DB)
    monkeypatch.setattr(db, "_pool", None)
    monkeypatch.setattr(db, "_pool_unavailable", True)
    monkeypatch.setattr(rfi_scan, "_redis", False)
    monkeypatch.setattr(rfi_review, "HEARTBEAT_SECONDS", 0.05)
    monkeypatch.setattr(storage, "download_to_file", lambda key, path: shutil.copy(CLIENT_PDF, path))
    monkeypatch.setattr(storage, "put_bytes", lambda key, data, content_type: None)
    monkeypatch.setattr(llm, "available", lambda provider: True)
    seeded = tr._seed(db, checks=("C01",))
    monkeypatch.setattr(llm, "complete", tr.FakeModel())
    rfi_review.run(seeded["run"])

    folder = next(p for p in tmp_path.iterdir() if p.is_dir())
    manifest = json.loads((folder / "manifest.json").read_text())
    assert [c["stage"] for c in sorted(manifest["calls"], key=lambda c: c["callId"])] == ["discovery", "reasoning", "verification"]
    discovery = json.loads((folder / "calls" / "c0001-discovery" / "meta.json").read_text())
    assert discovery["meta"]["promptVersion"] == rfi_review.PROMPT_VERSION
    evidence = json.loads((folder / "evidence" / "evidence.json").read_text())
    texts = [i for i in evidence["items"] if i["kind"] == "text"]
    assert texts and all(i["text"] and i["origin"] == "text extracted from the PDF" for i in texts)
    retrieval = json.loads((folder / "evidence" / "retrieval.json").read_text())
    assert retrieval["chunks"] and all("score" in c for c in retrieval["chunks"])
    events = [json.loads(line)["event"] for line in (folder / "postprocess.jsonl").read_text().splitlines()]
    assert "discovery_parsed" in events and "finding_saved" in events and "rejected_after_verification" in events
    findings = json.loads((folder / "output" / "findings.json").read_text())
    assert findings["findings"] and findings["callsByStage"]["verification"]
    assert (folder / "output" / "rfi-package.pdf").exists()
