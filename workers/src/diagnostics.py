"""Diagnostic export of one RFI run: everything the model was given and gave back.

Off unless `RFI_DIAGNOSTICS=on`. With it on, a full AI scan or a targeted
review writes ONE folder (and a zip of it, uploaded beside the project's other
files so the API can hand it out) holding:

  manifest.json          what is in the folder, the run's settings, every call
  README.md              how to read it
  run/                   the request as the person made it, documents, pages
  calls/<id>-<stage>/    per model call, per attempt:
      request.json       the FINAL assembled request, captured inside the
                         transport immediately before it is sent — never an
                         earlier template — with each image replaced by the
                         path of the exact bytes that were sent
      request.md         the same, readable: system, then the user turn in order
      response.json      the raw reply as the provider returned it
      response.md        its text, stop reason, model and token usage
      error.txt          a call that raised, with the error
      meta.json          stage, ids, settings, images and what the code did next
  images/                every image or crop actually sent, byte for byte
  evidence/              retrieved chunks, page mapping, what was left out
  postprocess.jsonl      every decision the code made after a reply: parse
                         failures, rules that rejected a finding, merges,
                         deduplication, where the clouds went
  output/                the findings saved and the marked-up RFI package

What is NEVER written: API keys, authorization headers, cookies, credentials,
signed URLs, and the model's private reasoning. A thinking block in a reply is
kept as a marker that it was there, with its text removed; the transport never
asks for reasoning to be returned and this module does not either.

Recording is a side path. Every hook is a no-op when no run is being recorded,
and every failure inside one is logged and swallowed: a diagnostic export must
never be the reason an RFI run fails.
"""

from __future__ import annotations

import base64
import contextlib
import contextvars
import hashlib
import io
import itertools
import json
import os
import re
import shutil
import threading
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import logutil

log = logutil.get("diagnostics")

FORMAT_VERSION = 1


def enabled() -> bool:
    return (os.environ.get("RFI_DIAGNOSTICS") or "").strip().lower() in ("1", "on", "true", "yes")


def root() -> Path:
    configured = os.environ.get("RFI_DIAGNOSTICS_DIR")
    return Path(configured) if configured else Path(__file__).resolve().parents[1] / "diagnostics"


def upload_enabled() -> bool:
    return (os.environ.get("RFI_DIAGNOSTICS_UPLOAD") or "on").strip().lower() not in ("0", "off", "false", "no")


_recorder: contextvars.ContextVar["Recorder | None"] = contextvars.ContextVar("diag_recorder", default=None)
_call: contextvars.ContextVar["Call | None"] = contextvars.ContextVar("diag_call", default=None)


def current() -> "Recorder | None":
    return _recorder.get()


def active_call() -> "Call | None":
    return _call.get()


# --- Scrubbing (pure; tested) ------------------------------------------------------

# Keys whose VALUES never belong in an export, whatever they hold.
_SECRET_KEYS = {
    "authorization", "x-api-key", "api_key", "apikey", "api-key", "x-goog-api-key", "cookie",
    "set-cookie", "headers", "sdk_http_response", "password", "secret", "token", "access_token",
}
_SIGNED_URL = re.compile(
    r"https?://[^\s\"']*(?:X-Amz-Signature|X-Amz-Credential|X-Goog-Signature|Signature=|[?&]sig=|[?&]token=)[^\s\"']*",
    re.I,
)
_KEY_SHAPES = re.compile(r"\b(?:sk-ant-[A-Za-z0-9_\-]{10,}|AIza[0-9A-Za-z_\-]{20,}|sk-[A-Za-z0-9]{20,})\b")
THINKING_REMOVED = "[removed: the model's private reasoning is not recorded]"


def scrub_text(text: str) -> str:
    text = _SIGNED_URL.sub("[signed URL removed]", text)
    return _KEY_SHAPES.sub("[credential removed]", text)


def scrub(value):
    """A copy with secrets, signed URLs and reasoning text removed."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if isinstance(k, str) and k.lower() in _SECRET_KEYS:
                continue
            out[k] = scrub(v)
        # A thinking block (Anthropic) or a thought part (Gemini) is kept as a
        # marker that reasoning happened — the text is not ours to keep.
        if out.get("type") == "thinking" and "thinking" in out:
            out["thinking"] = THINKING_REMOVED
            out.pop("signature", None)
        if out.get("type") == "redacted_thinking":
            out["data"] = THINKING_REMOVED
        if out.get("thought") is True and "text" in out:
            out["text"] = THINKING_REMOVED
            out.pop("thought_signature", None)
        return out
    if isinstance(value, (list, tuple)):
        return [scrub(v) for v in value]
    if isinstance(value, str):
        return scrub_text(value)
    return value


def _jsonable(value):
    if isinstance(value, (bytes, bytearray)):
        return {"bytes": len(value), "sha256": hashlib.sha256(value).hexdigest()}
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "model_dump"):
        try:
            return _jsonable(value.model_dump(mode="json", exclude_none=True))
        except Exception:
            pass
    if hasattr(value, "name") and hasattr(value, "value"):  # enums
        return getattr(value, "name")
    return str(value)


def image_size(data: bytes) -> tuple[int | None, int | None]:
    """Width and height of a PNG or JPEG without a decoder."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    try:
        import fitz

        pix = fitz.Pixmap(data)
        return pix.width, pix.height
    except Exception:
        return None, None


# --- One model call -------------------------------------------------------------------


class Call:
    """One logical model call: a stage asking one question, which the
    transport may SEND several times (a refused thinking setting, a refused
    media field, a busy provider) — each send is an attempt, recorded."""

    def __init__(self, recorder: "Recorder", number: int, stage: str, meta: dict):
        self.recorder = recorder
        self.id = f"c{number:04d}"
        self.stage = stage
        self.meta = dict(meta)
        self.folder = recorder.folder / "calls" / f"{self.id}-{_safe(stage)}"
        self.folder.mkdir(parents=True, exist_ok=True)
        self.attempts: dict[str | None, int] = {}
        self.images_meta: dict[str | None, list[dict]] = {}
        self.events: list[dict] = []
        self.sent_images: list[dict] = []
        self.replies: list[dict] = []
        self._lock = threading.Lock()

    # The caller says what each image IS (source page, crop, render settings);
    # the transport says what was actually sent. Matched by position.
    def describe_images(self, images: list[dict], entry: str | None = None) -> None:
        self.images_meta[entry] = list(images)

    def note(self, **data) -> None:
        with self._lock:
            self.events.append(_jsonable(scrub(data)))

    def _dir(self, entry: str | None) -> Path:
        path = self.folder if entry is None else self.folder / _safe(entry)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def sent(self, provider: str, request: dict, entry: str | None = None) -> None:
        with self._lock:
            attempt = self.attempts.get(entry, 0) + 1
            self.attempts[entry] = attempt
        folder = self._dir(entry)
        described = self.images_meta.get(entry) or []
        counter = itertools.count()
        records: list[dict] = []

        def keep(data: bytes, media_type: str) -> dict:
            n = next(counter)
            ext = "png" if "png" in media_type else "jpg" if "jpeg" in media_type else "bin"
            name = f"{self.id}{'-' + _safe(entry) if entry else ''}-a{attempt}-{n + 1:02d}.{ext}"
            path = self.recorder.folder / "images" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            width, height = image_size(data)
            record = {
                "file": f"images/{name}",
                "order": n + 1,
                "mediaType": media_type,
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "widthPx": width,
                "heightPx": height,
                "source": described[n] if n < len(described) else None,
            }
            records.append(record)
            return {"[image]": record["file"], "bytes": len(data), "sha256": record["sha256"]}

        def walk(value):
            if isinstance(value, dict):
                source = value.get("source")
                if value.get("type") == "image" and isinstance(source, dict) and source.get("type") == "base64":
                    data = base64.b64decode(source.get("data") or b"")
                    return {"type": "image", "source": keep(data, source.get("media_type") or "image/png")}
                inline = value.get("inline_data")
                if isinstance(inline, dict) and isinstance(inline.get("data"), (bytes, bytearray, str)):
                    raw = inline["data"]
                    data = base64.b64decode(raw) if isinstance(raw, str) else bytes(raw)
                    rest = {k: walk(v) for k, v in value.items() if k != "inline_data"}
                    return {**rest, "inline_data": keep(data, inline.get("mime_type") or "image/png")}
                return {k: walk(v) for k, v in value.items()}
            if isinstance(value, (list, tuple)):
                return [walk(v) for v in value]
            return value

        try:
            body = scrub(_jsonable(walk(request)))
            name = f"request-{attempt}" if attempt > 1 else "request"
            _write_json(folder / f"{name}.json", {"provider": provider, "attempt": attempt, "entry": entry, "request": body})
            (folder / f"{name}.md").write_text(_request_markdown(self, provider, attempt, entry, body, records))
            with self._lock:
                self.sent_images += [dict(r, attempt=attempt, entry=entry) for r in records]
        except Exception as exc:  # recording never breaks a run
            log.warning("diagnostics: could not record request %s: %s", self.id, exc)

    def received(self, provider: str, response, entry: str | None = None) -> None:
        attempt = self.attempts.get(entry, 1)
        folder = self._dir(entry)
        name = f"response-{attempt}" if attempt > 1 else "response"
        try:
            raw = scrub(_jsonable(response))
            _write_json(folder / f"{name}.json", {"provider": provider, "attempt": attempt, "entry": entry, "response": raw})
            (folder / f"{name}.md").write_text(_response_markdown(provider, raw))
            with self._lock:
                self.replies.append({"entry": entry, "attempt": attempt, **_response_facts(provider, raw)})
        except Exception as exc:
            log.warning("diagnostics: could not record response %s: %s", self.id, exc)

    def failed(self, exc: object, entry: str | None = None) -> None:
        attempt = self.attempts.get(entry, 1)
        folder = self._dir(entry)
        try:
            name = f"error-{attempt}" if attempt > 1 else "error"
            (folder / f"{name}.txt").write_text(scrub_text(f"{type(exc).__name__}: {exc}"))
            with self._lock:
                self.replies.append({"entry": entry, "attempt": attempt, "error": scrub_text(str(exc))[:500]})
        except Exception as e:
            log.warning("diagnostics: could not record error %s: %s", self.id, e)

    def reply(self, reply) -> None:
        """The transport's normalized result — what the stage code went on to use."""
        if reply is None:
            self.note(event="no_reply", detail="the transport returned nothing usable (unavailable or failed)")
            return
        self.note(
            event="normalized_reply",
            model=getattr(reply, "model", None),
            stopReason=getattr(reply, "stop_reason", None),
            inputTokens=getattr(reply, "input_tokens", None),
            outputTokens=getattr(reply, "output_tokens", None),
            thinkingTokens=getattr(reply, "thinking_tokens", None),
            cacheReadTokens=getattr(reply, "cache_read_tokens", None),
            thinkingSent=getattr(reply, "thinking", None),
            thinkingAdjusted=getattr(reply, "thinking_adjusted", None),
        )

    def close(self) -> dict:
        summary = {
            "callId": self.id,
            "stage": self.stage,
            "folder": str(self.folder.relative_to(self.recorder.folder)),
            "meta": _jsonable(scrub(self.meta)),
            "attempts": {(str(k) if k else "single"): v for k, v in self.attempts.items()},
            "images": self.sent_images,
            "replies": self.replies,
            "events": self.events,
        }
        try:
            _write_json(self.folder / "meta.json", summary)
        except Exception as exc:
            log.warning("diagnostics: could not close %s: %s", self.id, exc)
        return {k: summary[k] for k in ("callId", "stage", "folder", "meta", "attempts", "replies")} | {
            "imageCount": len(self.sent_images)
        }


# --- One run ------------------------------------------------------------------------------


class Recorder:
    def __init__(self, kind: str, run_id: str, project_id: str, info: dict):
        self.kind = kind
        self.run_id = run_id
        self.project_id = project_id
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.folder = root() / f"{kind}-{run_id}-{stamp}"
        for sub in ("run", "calls", "images", "evidence", "output"):
            (self.folder / sub).mkdir(parents=True, exist_ok=True)
        self.started = datetime.now(timezone.utc).isoformat()
        self.info = info
        self._numbers = itertools.count(1)
        self._lock = threading.Lock()
        self._event_seq = itertools.count(1)
        self.calls: list[dict] = []
        self.write_json("run/run.json", info)

    @contextlib.contextmanager
    def call(self, stage: str, **meta):
        with self._lock:
            number = next(self._numbers)
        record = Call(self, number, stage, meta)
        token = _call.set(record)
        try:
            yield record
        finally:
            _call.reset(token)
            summary = record.close()
            with self._lock:
                self.calls.append(summary)

    def event(self, kind: str, **data) -> None:
        """One decision the code made after a reply (postprocess.jsonl)."""
        try:
            line = {"seq": next(self._event_seq), "at": datetime.now(timezone.utc).isoformat(), "event": kind}
            line.update(_jsonable(scrub(data)))
            with self._lock:
                with open(self.folder / "postprocess.jsonl", "a", encoding="utf-8") as f:
                    f.write(json.dumps(line, ensure_ascii=False) + "\n")
        except Exception as exc:
            log.warning("diagnostics: could not record event %s: %s", kind, exc)

    def write_json(self, relpath: str, data) -> None:
        try:
            path = self.folder / relpath
            path.parent.mkdir(parents=True, exist_ok=True)
            _write_json(path, _jsonable(scrub(data)))
        except Exception as exc:
            log.warning("diagnostics: could not write %s: %s", relpath, exc)

    def write_bytes(self, relpath: str, data: bytes) -> None:
        try:
            path = self.folder / relpath
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        except Exception as exc:
            log.warning("diagnostics: could not write %s: %s", relpath, exc)

    def finish(self, status: str, summary: dict | None = None) -> str | None:
        """Write the manifest and README, zip the folder and upload the zip.
        Returns the object key, or None when it was kept on disk only."""
        try:
            manifest = {
                "formatVersion": FORMAT_VERSION,
                "kind": self.kind,
                "runId": self.run_id,
                "projectId": self.project_id,
                "startedAt": self.started,
                "finishedAt": datetime.now(timezone.utc).isoformat(),
                "status": status,
                "summary": summary or {},
                "conventions": CONVENTIONS,
                "calls": sorted(self.calls, key=lambda c: c["callId"]),
                "files": sorted(
                    str(p.relative_to(self.folder)).replace(os.sep, "/")
                    for p in self.folder.rglob("*") if p.is_file()
                ),
                "excluded": EXCLUDED,
            }
            _write_json(self.folder / "manifest.json", _jsonable(manifest))
            (self.folder / "README.md").write_text(README.format(kind=self.kind, run_id=self.run_id))
        except Exception as exc:
            log.warning("diagnostics: could not write the manifest: %s", exc)
            return None
        key = None
        try:
            data = _zip(self.folder)
            zip_path = self.folder.with_suffix(".zip")
            zip_path.write_bytes(data)
            if upload_enabled():
                import storage
                from generated import rfi_diagnostics_key

                key = rfi_diagnostics_key(self.project_id, self.kind, self.run_id)
                storage.put_bytes(key, data, "application/zip")
            log.info("diagnostics: %s %s written to %s (%.1f MB)%s", self.kind, self.run_id[:8], self.folder,
                     len(data) / 1e6, f", uploaded as {key}" if key else "")
        except Exception as exc:
            log.warning("diagnostics: export kept on disk only (%s): %s", self.folder, exc)
            key = None
        return key


def start(kind: str, run_id: str, project_id: str, info: dict) -> Recorder | None:
    """Begin recording a run when RFI_DIAGNOSTICS is on; None otherwise. The
    recorder is bound to the current context, so calls made by worker threads
    started with a copy of it (fullscan.run_in_context) are recorded too."""
    if not enabled():
        return None
    try:
        recorder = Recorder(kind, run_id, project_id, info)
    except Exception as exc:
        log.warning("diagnostics: could not start recording %s %s: %s", kind, run_id[:8], exc)
        return None
    _recorder.set(recorder)
    log.info("diagnostics: recording %s %s into %s", kind, run_id[:8], recorder.folder)
    return recorder


def stop() -> None:
    _recorder.set(None)


@contextlib.contextmanager
def call(stage: str, **meta):
    """A call scope when a run is being recorded, a no-op otherwise."""
    recorder = current()
    if recorder is None:
        yield None
        return
    with recorder.call(stage, **meta) as c:
        yield c


def event(kind: str, **data) -> None:
    recorder = current()
    if recorder is not None:
        recorder.event(kind, **data)


# --- Transport hooks (llm.py) --------------------------------------------------------------


def sent(provider: str, request: dict, entry: str | None = None) -> None:
    c = _call.get()
    if c is not None:
        c.sent(provider, request, entry)


def received(provider: str, response, entry: str | None = None) -> None:
    c = _call.get()
    if c is not None:
        c.received(provider, response, entry)


def failed(exc: object, entry: str | None = None) -> None:
    c = _call.get()
    if c is not None:
        c.failed(exc, entry)


def replied(reply) -> None:
    c = _call.get()
    if c is not None:
        c.reply(reply)


# --- Formatting -----------------------------------------------------------------------------


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(name))[:80]


def _write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def _zip(folder: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(folder.rglob("*")):
            if p.is_file():
                z.write(p, f"{folder.name}/{p.relative_to(folder).as_posix()}")
    return buf.getvalue()


def _text_of(block) -> str:
    if isinstance(block, str):
        return block
    if isinstance(block, dict):
        if "text" in block:
            return str(block["text"])
    return ""


def _request_markdown(call: Call, provider: str, attempt: int, entry, body: dict, images: list[dict]) -> str:
    lines = [f"# {call.id} — {call.stage} — attempt {attempt}" + (f" — entry {entry}" if entry else ""), ""]
    lines.append(f"Provider: {provider}")
    settings = {k: body.get(k) for k in ("model", "max_tokens", "thinking", "output_config", "temperature") if k in body}
    if provider == "gemini":
        config = body.get("config") or {}
        settings = {"model": body.get("model"), **{k: config.get(k) for k in (
            "max_output_tokens", "temperature", "thinking_config", "response_mime_type") if k in config}}
    lines.append("Settings: " + json.dumps(settings, ensure_ascii=False, default=str))
    if provider != "gemini" and "temperature" not in body:
        lines.append("Temperature: not sent (the provider's default applies)")
    lines.append("")
    lines.append("## System instructions")
    system = body.get("system") if provider != "gemini" else (body.get("config") or {}).get("system_instruction")
    if isinstance(system, list):
        for block in system:
            lines.append(_text_of(block))
    elif system:
        lines.append(str(system))
    else:
        lines.append("(none)")
    lines.append("")
    lines.append("## User turn, in the order sent")
    by_file = {i["file"]: i for i in images}
    if provider == "gemini":
        parts = body.get("contents")
        if isinstance(parts, dict) or (isinstance(parts, list) and parts and isinstance(parts[0], dict) and "parts" in parts[0]):
            parts = [p for msg in (parts if isinstance(parts, list) else [parts]) for p in msg.get("parts", [])]
        sequence = parts if isinstance(parts, list) else [parts]
    else:
        content = (body.get("messages") or [{}])[0].get("content")
        sequence = content if isinstance(content, list) else [content]
    for item in sequence:
        image = None
        if isinstance(item, dict):
            image = (item.get("source") or {}).get("[image]") or (item.get("inline_data") or {}).get("[image]")
        if image:
            info = by_file.get(image, {})
            lines.append(f"[IMAGE {info.get('order')}] {image} — {info.get('widthPx')}x{info.get('heightPx')} px, "
                         f"{info.get('bytes')} bytes")
            if info.get("source"):
                lines.append("    source: " + json.dumps(info["source"], ensure_ascii=False, default=str))
        else:
            text = _text_of(item) if not isinstance(item, str) else item
            lines.append(text)
        lines.append("")
    return "\n".join(lines)


def _response_facts(provider: str, raw: dict) -> dict:
    if not isinstance(raw, dict):
        return {}
    if provider == "gemini":
        usage = raw.get("usage_metadata") or {}
        candidates = raw.get("candidates") or [{}]
        return {
            "modelReturned": raw.get("model_version"),
            "finishReason": (candidates[0] or {}).get("finish_reason"),
            "usage": usage,
        }
    return {
        "modelReturned": raw.get("model"),
        "finishReason": raw.get("stop_reason"),
        "usage": raw.get("usage"),
    }


def _response_markdown(provider: str, raw) -> str:
    facts = _response_facts(provider, raw) if isinstance(raw, dict) else {}
    lines = ["# Response", "", f"Model returned: {facts.get('modelReturned')}",
             f"Finish/stop reason: {facts.get('finishReason')}",
             "Usage: " + json.dumps(facts.get("usage"), default=str), "", "## Text"]
    texts = []
    if isinstance(raw, dict):
        if provider == "gemini":
            for cand in raw.get("candidates") or []:
                for part in ((cand or {}).get("content") or {}).get("parts") or []:
                    if part.get("text") and part.get("text") != THINKING_REMOVED:
                        texts.append(part["text"])
                    elif part.get("text") == THINKING_REMOVED:
                        texts.append("[a reasoning part was returned; its text is not recorded]")
        else:
            for block in raw.get("content") or []:
                if block.get("type") == "text":
                    texts.append(block.get("text", ""))
                elif block.get("type") in ("thinking", "redacted_thinking"):
                    texts.append("[a thinking block was returned; its text is not recorded]")
                elif block.get("type") == "tool_use":
                    texts.append("[tool call] " + json.dumps(block, default=str))
    lines.append("\n\n".join(texts) if texts else "(no text)")
    return "\n".join(lines)


CONVENTIONS = {
    "pageNumber": "ONE-based page index within its own PDF file (page 1 is the first page).",
    "combinedPageNumber": "ONE-based page number across the project's virtual combined set (documents ordered by upload time, then id).",
    "displaySpace": "PDF points (1/72 in) as the sheet is SEEN, after its /Rotate is applied; origin top-left, y down. Tile windows, crop rectangles and the boxes a model returns are in this space.",
    "unrotatedSpace": "PDF points in the page's own coordinate system before /Rotate; origin top-left, y down. Stored evidence boxes, chunk boxes and PDF annotations (clouds) are in this space.",
    "modelBoxes": "Boxes a model returns are fractions 0-1 of the image they refer to: [x0, y0, x1, y1].",
    "sheetRevision": "The sheet revision printed in a title block is not extracted into this export: it is 'unknown' here. The marked-up RFI package (output/rfi-package.pdf) reads each finding sheet's issue line and date off its title block for its cover. documents.revision is the upload version of the file (1 = first upload), not the drawing's revision.",
    "images": "Every file under images/ is the exact byte stream sent to the provider. Nothing is re-rendered for this export.",
}

EXCLUDED = [
    "API keys, authorization headers, cookies and credentials",
    "signed / presigned URLs",
    "the text of the model's private reasoning (thinking blocks and thought parts are marked, not kept)",
]

README = """# RFI diagnostic export — {kind} {run_id}

Everything the AI was given and returned for this run, and what the code did
with it. Start at manifest.json; every path below is relative to this folder.

1. run/run.json — the request as the person made it (settings, limits, chosen
   provider and model) and the documents in scope.
2. evidence/pages.json — every page used: file, page number (ONE-based),
   combined page number, printed sheet number, rotation, size; sheet revision
   is 'unknown' (not extracted).
3. calls/ — one folder per model call, numbered in the order calls STARTED.
   request.md is the readable version of request.json, which is the exact
   request handed to the provider SDK. Images are listed in the order sent,
   with the file under images/ and where the picture came from.
   response.md / response.json are the raw reply. meta.json says which stage
   asked, the settings, every attempt (a retry is a new attempt), and what
   the code did with the reply.
4. postprocess.jsonl — every decision after a reply, one JSON per line, in
   order: parsed findings, rules that rejected one and why, findings saved or
   found again, evidence boxes and clouds.
5. output/ — the findings this run saved (findings.json) and, when they could
   be rendered, the marked-up RFI package (rfi-package.pdf).

Not in this export: credentials, signed URLs and the model's private
reasoning. Drawing content IS included — keep this folder as confidential as
the drawings themselves.
"""
