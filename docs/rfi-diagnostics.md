# RFI diagnostic export

An optional record of EVERYTHING one full AI scan or one targeted review gave the
model, what the model returned, and what the code did with it afterwards. It
exists so a wrong RFI can be traced back to the exact prompt, picture or rule
that produced it, instead of guessed at. Normal behaviour does not change: with
the switch off nothing is recorded and no code path differs.

## Turning it on

1. On the WORKER, set `RFI_DIAGNOSTICS=on` (in `.env` or the worker's env file)
   and restart the worker.
2. Run a full AI scan, or start a targeted review, as usual.
3. When it finishes, the RFIs tab shows **Diagnostics (.zip)** beside the
   run's other downloads. The zip is the whole folder.

A run started before the switch was on has no export; the button says so.

Where the files live:

- Locally: `workers/diagnostics/{full-scan|review}-{runId}-{UTC time}/`
  (`RFI_DIAGNOSTICS_DIR` moves it). Gitignored.
- Object storage: `projects/{projectId}/rfi-diagnostics/{kind}-{runId}.zip`,
  served through `GET /projects/:id/rfis/diagnostics/:kind/:runId`, which checks
  project membership and that the run belongs to the project, then returns a
  one-hour download link. In Docker the local folder is inside the worker
  container, so the download is the way to get it.
  `RFI_DIAGNOSTICS_UPLOAD=false` skips the upload.

## What is inside

```
manifest.json          run id, kind, status, every call, every file, conventions, what was excluded
README.md              how to read the folder
run/run.json           the request as made: settings, limits, provider/model, documents in scope
run/settings.json      prompt texts + their sha256, prompt-template version, output schema,
                       token limits, reasoning setting, temperature, tile/crop settings
run/plan.json          (full scan) the planned sheet pairs and tiles, and every page left out + why
evidence/pages.json    every page used: file, page (ONE-based), combined page, sheet number,
                       rotation, size; sheet revision is "unknown" here (the package
                       cover reads each finding sheet's issue line off its title block)
evidence/retrieval.json (review) retrieval queries, filters, limits, scores, chunk text and
                       whether it is extracted text or a model-written description;
                       pages the cap left out
evidence/evidence.json (review) the evidence items as sent, and retrieved items NOT sent
calls/cNNNN-<stage>/   one folder per model call, numbered in the order calls started
  request.json         the exact request handed to the provider SDK, captured just before sending
  request.md           the same, readable: system, messages in order, each image with its file,
                       source document/page/sheet, crop rectangle + coordinate system, render DPI,
                       pixel size and encoding; any text cut for length, with the removed part
  response.json / .md  the raw reply: text, structured output, tool calls, stop reason, token usage
  error.txt            when the call failed
  meta.json            stage, attempt list (retries, fallbacks), settings, what the code did
                       with the reply (parsed / parse error)
images/                every image actually sent, byte for byte, named after its call
postprocess.jsonl      every decision after a reply, in order: parsed issues, rules that
                       rejected one and why, findings saved or found again, with call ids
output/findings.json   the findings the run saved, each linked to the calls and evidence behind it
output/rfi-package.pdf the marked-up RFI package for those findings, when it could be rendered
```

Images are never re-rendered for the export: the bytes are copied out of the
request the SDK was given (base64 for Claude, `inline_data` for Gemini, batch
entries included).

## What is NOT inside

- API keys, authorization headers, cookies, credentials.
- Signed / presigned URLs (the download link itself is never written into it).
- The model's private reasoning. A thinking block or thought part is replaced
  by a marker saying one was returned; its text is not kept. Requests never ask
  for reasoning to be shown.

Drawing content IS included — page text, crops and the marked-up PDF — so treat
an export as confidential as the drawings themselves. Each export is
megabytes, which is why the switch is off by default.

## Conventions

- `pageNumber` and `combinedPageNumber` are ONE-based.
- Tile windows, crop rectangles and model boxes are in DISPLAY space (after
  `/Rotate`); stored evidence boxes and PDF clouds are in UNROTATED space. Model
  boxes are fractions 0–1 of the image they refer to.
- `documents.revision` is the upload version of the file, not the drawing's
  revision; the printed sheet revision is `unknown`.
- Temperature: Gemini calls send `0`; Claude calls send none (provider default).

## How it is built

`workers/src/diagnostics.py`. A recorder is started per run (`diagnostics.start`)
and held in a ContextVar; each stage opens a `diagnostics.call(...)` around its
model call; the transports in `workers/src/llm.py` report the exact request,
reply or error at the SDK boundary. Pool threads inherit the context. Every
failure inside the recorder is logged and swallowed — a broken export must
never break a scan. Tests: `workers/tests/test_diagnostics.py` (stub models; it
proves what is recorded, nothing about accuracy).
