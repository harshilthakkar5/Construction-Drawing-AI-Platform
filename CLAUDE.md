# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

Phases 1–5 done: project CRUD, direct-to-storage resumable multipart upload, worker pipeline
(per-page text/PNG/thumb + OCR fallback + status flow + resume), virtual page manifest, lazy
combined react-pdf viewer, portion detection (rule classifier + Haiku fallback), hybrid
chunking with bbox metadata, embeddings → Qdrant (payload-partitioned by project,
payloads refreshed after portion rebuilds), RAG chat with chunk-ID citations mapped to
document/page/bbox, portion filter, Redis retrieval cache, FR-23 persistence, and hierarchical
summaries (page → section → portion → project; page level is incremental and can use the
provider's batch API via SUMMARY_USE_BATCH=true; every item cites chunk IDs with the
jump page derived server-side from the first cited chunk).

Phase 6 (current): discipline detection is region-driven and summaries are user-approved. The
user drags ONE box over the title block per project (`sheet_regions`); the `scrape-region` job
applies it to every page (`workers/src/region.py`, rotation-aware, ported from the standalone
scraper in docs/reference/), stores `pages.sheetRegionText`, and only that string goes to Haiku
for the sheet number. Portions are stable rows UPSERTed on `(projectId, discipline)`, so a
re-scrape keeps portion IDs — and the summaries and chunk links hanging off them. Nothing is
summarized automatically: each discipline has a "Generate summary" button (`summarize-portion`
job), the project rollup is its own button, and a re-scrape that moves pages marks the affected
summaries `stale` instead of deleting them. Full spec:
docs/region-based-classification.md.

Phase 5 additions: FR-19 bbox highlighting (pages store pdfWidth/pdfHeight; chat sources carry
bbox+dims; summary items resolve via GET /projects/:id/chunks/:chunkId/location; overlay in
CombinedViewer scales bbox percentages); auth + RBAC (scrypt passwords in users, Redis
sessions, Project.ownerId + project_members owner/member; requireAuth on everything below
/health + /auth, requireProjectMember on /projects/:projectId; media GETs accept ?token=;
legacy ownerless projects open to any authenticated user); upload validation (%PDF magic,
filename sanitization, 2 GiB cap) + malware-scan hook (MALWARE_SCAN_URL, fails closed);
document revisions FR-4 (POST documents with replacesDocumentId → previousVersionId chain; on
completion the worker supersedes the old doc — excluded from manifest/numbering/summaries via
supersededAt filters, Qdrant points deleted — and reuses embeddings for unchanged chunks via
chunks.textHash + Qdrant vector fetch; reuse must run BEFORE old-point deletion); prompt
caching (cache_control on chat system prompt + retrieved-chunk block in apps/api/src/llm.ts
and on the summarizer system prompt in workers/src/summarize.py); Redis summaries cache
(cache:summaries:{projectId}, API reads / worker invalidates — key format duplicated in
apps/api/src/routes/summaries.ts ↔ workers/src/cache.py); OpenTelemetry metrics (API
Prometheus endpoint :9464 — HTTP/Qdrant/chat latency, queue depths; worker :9465 — job
durations; monitoring/ has Prometheus + provisioned Grafana dashboard, both in docker
compose); README covers DO App Platform/DOKS deployment. Worker logs every pipeline stage
(1/6 download … 6/6 finalize, LOG_LEVEL env, workers/src/logutil.py); DELETE /projects/:id
purges all stores — Postgres cascade + Spaces prefix + Qdrant points + Redis caches
(apps/api/src/cleanup.ts, each stage logged, failures non-blocking).

Chat and embedding need `ANTHROPIC_API_KEY` and the active embedding provider's key
(`VOYAGE_API_KEY`/`COHERE_API_KEY`/`GEMINI_API_KEY`); without them the worker skips
embedding (chunks wait with NULL embeddingId) and the chat endpoint returns 503. For offline
E2E, both APIs can be pointed at a stub via `ANTHROPIC_BASE_URL` and the embedding
provider's `{VOYAGE,COHERE,GEMINI}_BASE_URL`.

Common commands (see README.md for full setup, including the two `.env` copies and the Python
worker venv):

- `docker compose up -d` — local Postgres, Redis, Qdrant, MinIO (+ bucket init)
- `npm install` — all workspaces; also builds `packages/shared`
- `npm run prisma:migrate` / `npm run prisma:generate` — migrations / client (workspace `@cdip/api`)
- `npm run dev:api` (port 4000, `/health` checks Postgres/Redis/Qdrant) and `npm run dev:web` (port 3000)
- `npm run typecheck` / `npm run build` / `npm test` — all TS workspaces (tests: vitest in `apps/api`)
- Single test file: `npx vitest run src/manifest.test.ts` from `apps/api`
- Workers: `cd workers && python src/worker.py` (consumes process-document, scrape-region,
  summarize-portion and summarize-project; deps in `requirements.txt`;
  PaddleOCR is optional locally — the OCR wrapper degrades gracefully if it isn't installed, as
  does the Haiku classifier fallback when `ANTHROPIC_API_KEY` is unset)
- Python tests: `cd workers && python -m pytest tests/ -q` (dev deps in `requirements-dev.txt`)

Key invariant: the combined-numbering rule (documents ordered by `createdAt` then `id`, pages
1..N within each) is implemented twice — `apps/api/src/manifest.ts` and the recompute SQL in
`workers/src/db.py` (mirrored as a pure function in `workers/src/numbering.py`). Both sides read
ONE golden fixture, `packages/shared/fixtures/combined-numbering.json`, so changing the rule in
either language fails the other's tests. `numbering.verify_against_db` checks the SQL's actual
output against the rule when a database is available.

Object keys and queue contracts are no longer hand-synced: `workers/src/generated.py` is emitted
from `packages/shared/src/index.ts` by `packages/shared/codegen.mjs` (`npm run codegen`), and a
vitest test fails if the checked-in copy is stale. Adding a field to a job interface without
declaring it in `JOB_FIELDS` is a TypeScript compile error naming the missing field. The citation format is another
cross-cutting contract: the model is told to emit `[chunk:<uuid>]` (apps/api/src/answer.ts) and
`apps/api/src/citations.ts` (unit-tested) parses/renumbers it. That parser must tolerate the
shapes the model actually emits, not just the documented one: asked to cite two chunks for one
claim it writes `[chunk:a, chunk:b]`, which an `\[chunk:<id>\]` pattern matches NEITHER half of
— so both raw UUIDs rendered in the chat bubble. It now matches the bracket GROUP and pulls the
ids out of it, and a final sweep deletes any `chunk:<uuid>` still standing: a reader must never
see a UUID, whatever shape the model invents. The summary provider/model
defaults are duplicated across the process boundary too — `workers/src/summarize.py` runs the
summaries, `apps/api/src/llm.ts` resolves the same env vars only to quote what they will cost
(`summaryEstimate.ts`), so a drift shows a user one model's price for another model's work.
The embedding provider is the sharpest of these duplications — `workers/src/embedllm.py`
embeds the documents and `apps/api/src/embedding.ts` embeds the question they are searched
with, off the same env vars. A drift there does not degrade retrieval, it destroys it: a
cosine distance between two embedding spaces is noise, and nothing raises.

The API rate-limits five tiers (flood/general/auth/chat/summary, all Redis-backed and
env-tunable — see README) and can fork HTTP workers with `API_CLUSTER_WORKERS`, default 1.
`benchmarks/` holds the two things that had never been measured: worker pages/minute + peak RSS,
and API throughput/p95.

No lint config yet.

## What we are building

A web application that manages construction projects, ingests very large sets of construction
drawing PDFs (100 MB – 1 GB+ combined, 1000+ pages), and provides AI-powered hierarchical
summaries plus a project-scoped RAG chat assistant. Every AI statement must be traceable to the
exact PDF document, page, and bounding box, verifiable with one click.

## Core architectural principle

The AI NEVER chats directly with raw PDFs. PDFs are the source of truth for viewing/verification
only. The AI operates on a derived knowledge base: extracted text → markdown → chunks →
embeddings → summaries.

## Tech stack (fixed — do not substitute)

- Frontend: React + TypeScript, TanStack Query, Zustand, Tailwind CSS v4, shadcn/ui (Radix +
  class-variance-authority; components live in `apps/web/src/components/ui`, imported via the
  `@/` alias)
- PDF rendering: PDF.js / react-pdf (programmatic page jump + region highlight)
- Backend API: Node.js (Express), REST
- Processing workers: Python (PyMuPDF, pdfplumber, PaddleOCR, OpenCV)
- Database: PostgreSQL + Prisma ORM
- Vector DB: Qdrant (metadata filtering by project/portion)
- Queue: BullMQ + Redis (also used for cache/sessions)
- Object storage: S3-compatible, behind ONE switch — `STORAGE_BACKEND=local|spaces`
  (`apps/api/src/storageConfig.ts`, `workers/src/storage_config.py`). `spaces` is
  DigitalOcean Spaces (AWS S3 SDK with endpoint override, e.g.
  blr1.digitaloceanspaces.com); `local` is a MinIO container writing to a folder on the
  machine's own disk, which is what lets the whole stack run on one office PC
  (docs/local-server-deployment.md). Both sets of credentials live in the env file at
  once, so it is a restart and not a rewrite — and because MinIO speaks the same API,
  there is no second storage code path to rot untested. Unset means whichever set you
  filled in: any of the four required `SPACES_*` present picks `spaces`, none picks
  `local`; three of the four still fails at startup naming the fourth. The two sides
  resolve it from ONE golden fixture, `packages/shared/fixtures/storage-backend.json`,
  because the API writes the uploaded PDF and the worker reads it back — a drift is not
  a degraded system, it is a worker that cannot find any file it is handed. The switch
  MOVES NOTHING: keys live in Postgres and the two stores hold different bytes, so it is
  for an empty system or after `mc mirror`. `LOCAL_S3_PUBLIC_ENDPOINT` /
  `SPACES_PUBLIC_ENDPOINT` exist because a presigned URL is signed against the host it
  will be REQUESTED on: the API reaches MinIO at `http://minio:9000` and a laptop across
  the office reaches it at `http://<server-ip>:9000`, so `s3.ts` keeps a second client
  for the URLs that leave the process. Server-to-server presigns (the malware scanner)
  use `presignGetObjectInternal` instead.
- OCR: PaddleOCR
- Embeddings: Voyage AI (voyage-3 / voyage-3-large) by default, with Cohere (`embed-v4.0`)
  and Gemini (`gemini-embedding-001`) as ALTERNATIVES behind one switch:
  `EMBEDDING_PROVIDER=voyage|cohere|gemini`, transports in `workers/src/embedllm.py`
  (documents) and `apps/api/src/embedding.ts` (the chat question). Unlike the LLM switches,
  this one is not transport-only in its consequences: two providers embed into different
  SPACES, so the two sides must always agree, and a switch means a re-index into a new
  `QDRANT_COLLECTION` rather than a restart. `EMBEDDING_DIM` must match the collection —
  `ensure_collection` refuses to index otherwise. Cost control lives in `embeddings.py`:
  revision reuse, in-run dedup of identical chunk text, a Redis vector cache keyed on
  (provider, model, width, text), and `EMBED_USE_BATCH` for the provider's async batch API
  at 50% (Gemini only today; a logged no-op on the other two). Batched runs record usage
  under `<model>-batch` so the dashboard prices them at what they cost.
- LLM: Claude via Anthropic API (Sonnet for chat/summaries/reasoning; Haiku for cheap per-page
  classification). Use prompt caching for repeated context and the Batch API for bulk
  summarization. Gemini is a supported ALTERNATIVE at every model call site, switched per
  stage: `SHEET_PROVIDER` / `SUMMARY_PROVIDER` / `CHAT_PROVIDER` / `VLM_PROVIDER` = `claude`
  (default) | `gemini`, with transports in `workers/src/llm.py` and `apps/api/src/llm.ts`. The provider is
  a TRANSPORT detail: both get the same instructions and the same untrusted document text, and
  both replies go through the same strict parser (`parse_sheet_response`, `parse_summary_json`,
  the `[chunk:<id>]` citation parser), so a swap changes WHO answers and never what an answer
  may claim or cite. Bulk summaries batch on both (Anthropic Message Batches / Gemini inline
  batch jobs, half price either way, bounded by `BATCH_TIMEOUT_SECONDS`); explicit cache
  breakpoints become Gemini's implicit caching. Thinking is OFF by default on BOTH
  (`GEMINI_THINKING_BUDGET`, `CLAUDE_THINKING`): thinking tokens are spent from
  `max_output_tokens` before the answer is written, so on a thinking model they truncate the
  JSON every parser here depends on — and they bill as output, so they are recorded as output.
  The Claude half existed only after the failure it prevents: Sonnet 5 runs ADAPTIVE thinking
  when the `thinking` field is OMITTED, where every earlier model ran none, so a transport that
  had never sent the field started reasoning the day the model id changed. Its text is hidden by
  default too, so `_complete_claude` joined zero text blocks into `""` — `VLM_PROVIDER=claude`
  at `VLM_MAX_TOKENS=4000` returned 200 OK and no description, for a whole project, reported as
  "description was 0 chars" which reads like a refusal. `_complete_claude` therefore also logs
  what a textless reply was MADE of (`1x thinking`), because a model that said nothing and a
  model that spent the budget before it could speak are the same empty string to every caller.
  A model that rejects the field (older tiers take `budget_tokens`) is retried once without it
  and latched, mirroring the Gemini path, which learns the opposite lesson the same way.
  And then learned it AGAIN, from the other vendor: from Gemini 3 on, `thinking_budget` is not
  the control — the field is `thinking_level` (`minimal|low|medium|high`, `GEMINI_THINKING_LEVEL`,
  default `minimal`), sending both is an error, and an UNSPECIFIED level is the TOP of the scale.
  So the old fallback — drop the field this model rejected and carry on — did not disable
  thinking on those models, it asked for the most of it, and said "retrying without the thinking
  budget" while doing so. `VLM_PROVIDER=gemini` on `gemini-3.6-flash` therefore spent 3900 of a
  4000-token budget reasoning and wrote 103 tokens of an ARCH E1 drawing, from a transport that
  believed it had turned thinking off — the identical shape to the Sonnet 5 failure above, at the
  identical cost, because a transport that does not send the CURRENT field gets the model's
  default and the default had moved. Omission is now the LAST rung of a ladder rather than the
  first fallback: a refused level steps UP one (`minimal` is not accepted by every model in the
  family), whatever works is latched, and surrendering to omission on a level-taking model is an
  ERROR naming the env var, because there it is a defeat and not a fallback. Which field a model
  takes is a VERSION SNIFF (`gemini-<N>`, N≥3) rather than a list of model names: this file has
  already paid for one of those, and Gemini 4 has to work without a code change. The switch a log
  line recommends had to be corrected with it — `vlm.describe_page` was telling whoever read it
  to set `GEMINI_THINKING_BUDGET=off`, which on the model in front of them was the instruction
  that caused the failure. Automatic Function Calling is
  off too (`_gemini_config`): no call here declares a tool, so AFC can never act, but the SDK
  still routes every `generate_content` through its agentic wrapper and logs two lines — one at
  WARNING — per call. That noise sat directly above the line reporting the vision pass had
  returned 98 characters. Adding a call site means
  adding its switch too. A Gemini model NAME also expires on a schedule this repo does not
  control, and expires per KEY: Google removes a retired model for new users first, so the same
  commit keeps working for whoever set a deployment up and returns `404 ... no longer available
  to new users` for whoever creates a key the month after. Every default here was one generation
  behind at once (`gemini-2.5-flash` for the sheet read and the vision pass, `gemini-2.5-pro`
  for summaries). What makes that expensive rather than obvious is the fallback design: an
  unavailable provider is SUPPOSED to degrade, so a dead model name finishes a 400-page scrape
  with the rules ladder standing in for the sheet reader, or with no description on any page,
  reporting one warning per call among thousands. `llm._note_missing_model` says it once per
  model and stage at ERROR, naming the stage and saying the fix is an env var — a name the
  provider does not know will fail identically on every remaining call, which is configuration,
  not weather. Ordinary failures (a rate limit, a timeout) stay warnings, since the fallback
  really is the right answer for those. Pricing degrades on its own: a model missing from
  `RATES` is quoted at its family rate with a warning. The worker's summary defaults are
  MIRRORED in `apps/api/src/llm.ts`, whose comment already said the two must match — and they
  had drifted, the worker running `gemini-2.5-pro` while the "Generate summary" dialog priced
  `gemini-3.1-pro-preview`. `test_summarize.py` now reads the TypeScript and fails on a drift.
- Monitoring: OpenTelemetry + Grafana
- Deployment target: DigitalOcean App Platform / DOKS, or the two Droplets in
  `deploy/docker-compose.{app,worker}.yml`. API and workers scale independently. A third
  shape is ONE machine running everything
  (`deploy/docker-compose.local.yml`, `./deploy/deploy.sh local`) — an office PC on a
  LAN (docs/local-server-deployment.md) or a single laptop at localhost
  (docs/laptop-deployment.md), the same file sized by the env-tunable `*_MEM_LIMIT`
  and `*_CONCURRENCY` values rather than by a second compose file. Plain HTTP either
  way, since a LAN address has no certificate anyone can issue: fine at localhost, for
  a trusted network otherwise, and never port-forwarded. For a machine that will never
  hold the source — a client's laptop — `deploy/release.sh` builds and publishes the
  three images and writes `deploy/dist/`: `deploy/docker-compose.dist.yml` (every
  `build:` replaced by an `image:`, plus a one-shot `migrate` service the api and
  worker wait on) with a generated `.env`. The web image bakes `Caddyfile.local` as its
  default so that bundle stands alone; every other compose file still mounts its own
  over the top. Images PACKAGE the code, they do not hide it — the worker image ships
  `workers/src/*.py` verbatim — so never present them as a licensing control
  (docs/client-install.md).

## Monorepo layout

```
/apps/web        React + TypeScript frontend (Vite)
/apps/api         Express REST API
/workers          Python processing workers
/packages/shared  Shared types
docker-compose.yml  Local Postgres, Redis, Qdrant, MinIO (local stand-in for Spaces)
```

## Spaces bucket layout (per project)

```
projects/{projectId}/
  pdfs/{documentId}/original.pdf
  pdfs/{documentId}/ocr.pdf
  pdfs/{documentId}/pages/{n}.png       (rendered page images)
  pdfs/{documentId}/thumbs/{n}.jpg      (thumbnails)
  pdfs/{documentId}/text/{n}.txt        (per-page extracted text)
  pdfs/{documentId}/extract.json        (structured extraction)
  pdfs/{documentId}/markdown/{n}.md
```

## Large-file handling (hard rules)

- Never load an entire PDF into memory; never process inside an HTTP request.
- Browser uploads go DIRECTLY to Spaces via presigned multipart upload (resumable); the API
  server never proxies file bytes.
- Workers stream one page at a time with PyMuPDF; memory stays flat.
- Failed jobs retry with exponential backoff; partial results are preserved (a failure at page
  700 must not discard pages 1–699).
- PDFs are merged VIRTUALLY, not physically: a page manifest maps each (document, page) →
  continuous combined page number. This manifest and the chunk→page→bbox citation mapping are
  the correctness-critical paths in this codebase — they need direct test coverage.

## Functional requirements

- FR-1–4: CRUD for projects (name, roles, description, created date, owner); multi-PDF upload per
  project (no fixed count limit); document version management (revisions of the same drawing).
- FR-6: PDFs merged VIRTUALLY, not physically — see page manifest, above.
- FR-7: OCR applied automatically when a page lacks a text layer.
- FR-8: Per page, extract text, images, tables, title-block metadata, page number.
- FR-9: Per-document processing status visible to the user: `uploaded → processing →
  completed / failed`.
- FR-10–12: Whole-project summary, a separate summary per detected portion (discipline), and
  page-level summaries as the building blocks for both — see hierarchical summarization, above.
- FR-13: Every fact/summary statement stores source references (chunk IDs → document, page,
  bounding box).
- FR-14: Project-scoped chat answers generated ONLY from retrieved project content, with
  citation links.
- FR-15: Each portion stores name, start page, end page (combined numbering), own summary.
- FR-16: Clicking a portion jumps the viewer to its start page and switches the left panel to
  that portion's summary.
- FR-17: Split layout: left = summary + chat, right = combined PDF viewer.
- FR-18–19: Clicking any summary item or chat citation opens the corresponding page; the viewer
  highlights the referenced bounding box / cited text.
- FR-20: Viewer lazy-loads pages; cached thumbnails support 1000+ page navigation.
- FR-21: Every chat answer includes clickable sources (e.g. "S201 Page 17").
- FR-22: Chat is optionally filterable to a single portion.
- FR-23: Persist per project: prompt, retrieved chunks, Claude response, sources, timestamp
  (replay, audit, analytics).

## Portion (discipline) detection — region-driven

Classify pages by the SHEET NUMBER, not content, and read that number out of a region the USER
points at. Per project the user drags one box over the title block on a single page
(`sheet_regions`, relative 0–1 coordinates); the `scrape-region` job (`workers/src/scrape.py`)
applies it to every page of every PDF and stores what it reads in `pages.sheetRegionText`. Only
that string reaches the model (`classify_region_batch` → `extract_sheets_from_regions`), which
reports the sheet number; the deterministic `PREFIX_TO_DISCIPLINE` table maps it, so the mapping
never depends on model judgement.

The reader is swappable: `SHEET_PROVIDER=claude|gemini` (`workers/src/sheetllm.py`, transports
in `workers/src/llm.py`). The provider is a TRANSPORT detail only — both are asked for the same
JSON, `parse_sheet_response` validates it, and the same prefix table decides the discipline, so
the two are directly comparable. The Redis cache key includes the provider, so switching
re-reads rather than serving the other model's answers. An unavailable or failing provider falls
through to the rules ladder. Results are Redis-cached by `sha256(region_text)` — hundreds of
sheets share a box layout — and the instructions are prompt-cached.
Most pages never reach a model at all. `confident_sheet_from_region` (the rules-first pre-pass,
`SHEET_RULES_FIRST=true`) resolves a box that says exactly one thing — one strong sheet token, no
license/job/permit context word, no cross-reference word — and abstains on everything else. It is
deliberately NOT `classify_by_rules`: that one is the permissive end of the fallback ladder where
a guess beats nothing, whereas a pre-pass sits in FRONT of the model and must be right, so it
trades recall for precision. The pages it abstains on are then read MANY PER REQUEST
(`SHEET_BATCH_SIZE`, default 25) — round trips, not tokens, are what a 400-page scrape spends its
wall clock and rate limit on. Batching adds exactly one new failure mode, alignment: every entry
carries an index the model must echo, `parse_sheet_batch_response` discards anything out of range
or duplicated, and a page the model did not answer for is retried in a smaller batch (bounded by
`_BATCH_MAX_DEPTH`) rather than silently downgraded — a drifted answer must become "unresolved",
never a confident wrong discipline.

The reader is swappable: `SHEET_PROVIDER=claude|gemini` (`workers/src/sheetllm.py`). The provider
is a TRANSPORT detail only — as is the batching — both are asked for the same JSON, the same
`_sheet_from_payload` validates it, and the same prefix table decides the discipline, so the two
are directly comparable. The Redis cache key includes the provider, so switching re-reads rather
than serving the other model's answers. An unavailable or failing provider falls through to the
rules ladder, and its non-answer is NOT cached — only a real "the model looked and found nothing"
is. Results are Redis-cached by `sha256(region_text)` — hundreds of sheets share a box layout, and
the single-page and batched readers share the key — and the instructions are prompt-cached.

The scraping itself is the correctness-critical part and is unit-tested at 0/90/180/270 rotation
(`workers/tests/test_region.py`): `page.rect` and `page.get_pixmap()` are rotation-aware, but
`page.get_text(clip=…)` is NOT, so the clip must be mapped with `clip * page.derotation_matrix`
first or every rotated CAD sheet comes back empty. Extraction ladder per page: vector text →
word-overlap pass (≥30% of a word inside the box) → OCR the rendered crop.

Fallbacks, in order: pattern match on the scraped string (`classify_by_rules`), the filename sheet
number (`classify_by_filename`), then neighbour inheritance (`fill_unresolved`) — so detection
still works with no `ANTHROPIC_API_KEY` or `SHEET_EXTRACTION=rules`. There is NO content-based
classification. `workers/src/portions.py` is the LEGACY page-text path, kept only for projects with
no region defined; the document pipeline no longer calls it. Editing the region bumps
`sheet_regions.version`, and the pages to re-scrape are exactly those whose `pages.regionVersion`
differs. Stage switches `EMBEDDINGS_ENABLED` / `SUMMARIES_ENABLED` (workers/src/config.py) let the
chat and summary flows be tested independently; `POST /projects/:id/documents/reindex` re-queues
completed documents to fill in vectors afterwards. Prefix →
discipline (two-letter prefixes win over single letters; mirrored in workers/src/classify.py
`PREFIX_TO_DISCIPLINE` and `@cdip/shared` `Discipline`):

G → General | A → Architectural | S → Structural | C → Civil | L → Landscape | I → Interiors |
M → Mechanical | H → HVAC | P → Plumbing | E → Electrical | F/FP → Fire Protection |
FA → Fire Alarm | T → Telecommunications | IT → Information Technology | AV → Audio Visual |
X → Other/Special. Each page's discipline is stored on pages.discipline; there is ONE portion
per discipline (FR-15) covering all its pages even when non-contiguous (startPage/endPage span
them, start is the jump target). Chunks and page summaries group by pages.discipline (not page
range) — assign_chunk_portions joins pages.discipline = portions.discipline; summarize.run
groups covered pages by discipline. Portion and section summaries are therefore per-discipline.

## Chunking strategy (hybrid)

1. Structural split first, in two passes (`workers/src/chunker.py`):
   a. **Tables** (`workers/src/tables.py`, `page.find_tables()`) are lifted out whole — a
      door/panel/beam schedule becomes ONE chunk of markdown with the table's own bbox, and
      the text blocks inside that region are dropped from the block stream so the schedule is
      indexed once, not twice. A region rejected as not-a-schedule (one column, prose-length
      cells) absorbs nothing and leaves its text alone. Detection is a heuristic: a page
      reporting more than `MAX_TABLES_PER_PAGE` tables has had its drawing border read as a
      grid, and all of that page's detection is discarded. It is also BOUNDED
      (`TABLE_DETECTION_BUDGET_SECONDS`): `find_tables()` scans ruled lines for
      intersections and a structural drawing is mostly ruled lines — measured at 32s per page
      on a real IFC set against 0.1–0.8s on ordinary sheets, which ran ingest 4.8x slower.
      The first page to blow the budget disables detection for the rest of that document, so
      a drawing-heavy set pays for one slow page rather than all of them. A budget rather than
      a page-complexity cutoff because complexity did not predict the cost. Detection is also
      SERIALIZED process-wide: `find_tables()` is pure Python and holds the GIL, so concurrent
      scans bought no parallelism, raced the budget (every thread was inside a scan before the
      first returned to set it) and starved the uploads — the same run's upload fell from
      0.3 MB/s to 0.03. A page arriving mid-scan skips its own detection rather than queueing.
   b. **Spatial clustering** (`cluster_blocks`) groups the remaining blocks by proximity
      BEFORE packing. A CAD sheet returns blocks in content-stream order, so packing in that
      order merged a top-left note with a bottom-right callout: the chunk read as two
      unrelated fragments and its bbox union spanned the whole sheet, making the FR-19
      highlight useless. The gap threshold scales with the page's short side (a D-size sheet
      and a letter page are treated proportionally); `merge_small_clusters` then merges tiny
      neighbours back up to MIN_TOKENS, bounded by `MAX_BBOX_AREA_RATIO` so the merge cannot
      re-create the sheet-wide bbox this exists to prevent.
2. Size split second: 400–800 token chunks, 100-token overlap — packed WITHIN a cluster group,
   so overlap is never carried across a spatial break.
3. Every chunk carries metadata:
   `{ chunk_id, document_id, page, portion, discipline, bbox: {x, y, width, height}, text, image_ref, revision, token_count }`

Purity split: everything needing a `fitz` page lives in `tables.py`; every rule about what a
chunk may contain lives in `chunker.py`, unit-tested without a PDF.

## Vision pass — the geometry the text layer cannot hold

`VLM_ENABLED` (default OFF, `workers/src/vlm.py`) describes each page with a vision model and
stores the result as `kind="description"` chunks. It exists because a sheet carries two kinds
of fact and the pipeline only had one. The VOCABULARY — every footing mark, every member size,
every schedule row — is in the text layer, and retrieval finds all of it. The GEOMETRY is not
there at all: `page.get_text()` returns every member size in one run and every footing mark in
another, because what joins them is a diagonal LEADER LINE. Nothing a chunker does recovers a
fact that was never written.

So the prompt asks for pairings, positions and connections, and says outright that the text is
already indexed and a description which merely lists labels is worthless. It demands a FULL GRID
COORDINATE on every pairing, because that one habit is the whole measured difference between a
useful description and a useless one: two descriptions of the same sheet scored 84% and 47% on
grid questions, and the weaker one had written its first row as "At 8/B: ..." and the other two
as ordered lists under a heading ("Middle row, around grid C: F9 ... F8 ... F12"). The coordinated
row scored 6/7; the two listed rows scored 3/12 — a list of marks in reading order is EXACTLY what
the text layer already holds. The failing shapes (no coordinate, an approximate one like
"near grid 9/8", two grid lines merged into one entry, and the coordinate written row-first) are
quoted back at the model as counter-examples.

The grid itself can be over-read, and that is its own failure: one description named 12 column
lines and 8 row lines on a sheet carrying 8 and 3, having counted the letters and numbers printed
around the drawing's FRAME. Those are zone markers for finding things on a printed sheet — evenly
spaced, in the border, no line attached, no circle — and it is the same trap `drawing_truth.py`
documents from the generator's side, where an uncircled zone letter read as a grid line put the
wrong footing at 7/C. The prompt now defines a grid line by what it IS (a line drawn across the
drawing ending in a circled label) rather than by where the label sits. It also inflates
`grid_coverage`'s denominator, so a coverage warning should be read against the grid the sheet
actually has before it is believed.

Naming the GRID comes before any pairing — two lines listing the column lines left to right and
the row lines top to bottom, and every coordinate must then use only those names. That step
exists because the pairing can be RIGHT while the row name is WRONG, which is the one failure
here that leaves nothing to see. The first description good enough to score got the footing mark
AND the member size right at three consecutive intersections and labelled that whole row with
its neighbour's letter; 14 of the eval's 40 questions ask about that row, and they had nothing
to answer from. It reported as 15 abstentions, which reads like a model that could not see
rather than one that mislabelled. A coordinate may also appear only ONCE: the same description
wrote one intersection twice with different footings, flagging it itself as a "second column
line row", which is the merged-grid-line failure in a third costume — one label over two
physical rows, both entries unusable. And the ordering had to change with it. "Then, and only
with the room left over" listed dimensions as permitted, so a third of the budget went to nine
grid-to-grid spacings, every one already indexed, while a whole grid row went undescribed.
Intersections come first; everything else is what is left.

The vision provider was then measured against those rules rather than argued about, once the
Gemini thinking control worked at all. `GEMINI_THINKING_LEVEL` is the whole experiment on that
side: at `minimal` the same sheet scored 20% correct with 17 off-target and answered one member
size to 75% of the column questions it attempted — a size the sheet shows on 14% of them, so not
even the majority guess — while at `low` it scored 60% and beat the baseline on BOTH tags for the
first time, 12 of 24 hits on minority labels. Against Claude's best run on the same sheet (63%,
footings 74%, columns tying their baseline) `low` traded: worse on footings, better on columns.

**That comparison no longer stands, and the way it fell over is the point.** It was n=1 per arm,
and it was written as settled cause — "the floor of the scale is not a cheap version of the next
rung; on this task it is a different model". A later run at `GEMINI_THINKING_LEVEL=low`, on the
far better configuration this section arrives at, reproduced the failure signature this paragraph
attributes to `minimal` almost exactly: the column tag at 19% with 10 off-target, answering
`HSS6X6X3/8` to 67% of the questions it attempted — the same fixation, on a size the sheet shows
on the same 14% of them. One arm's characteristic failure appearing under the other arm is not a
detail; it says the variable named here was probably not the one doing the work.

What it is instead is unknown, and the run cannot say, because TWO settings had moved from the
83% run before it (`GEMINI_THINKING_LEVEL` off its default and `VLM_MAX_TOKENS` 4000 to 20000)
and the run before that predates `_report_settings`, so its values were never recorded at all.
Two variables at once against an unknown baseline is three unknowns and one number. The honest
state is: `minimal` versus `low` is UNMEASURED on this pipeline, both of the runs that pretend to
measure it are confounded, and nothing in this section should be read as knowing which way that
switch cuts until one variable moves on its own.

Those four rules were then measured, on a fresh ingest of the same sheet: 28% correct with 15
abstentions became 63% correct with 4, and the footing tag went from guesswork to 74% against its
32% baseline with 9 of its 14 hits on minority marks. Naming the grid first is what bought most
of it — the row those 14 questions ask about now answers. The column tag did not move off its
baseline, and the report now says why: it answered HSS8X8X3/8 to 15 of the 18 cases it attempted,
where the sheet shows that size on 52% of them. Two of its remaining misses name the right
section with the wrong thickness and two the reverse, which is the signature of a mark being half
read — not of a row nobody located.

Every example value in the prompt is SYNTHETIC on purpose, and that is load-bearing rather than
tidy. The counter-examples carried this sheet's real footing marks for a while — `F9` among
them, which is exactly the label the majority-class baseline guesses and scores 32% with. A
model falling back on the prompt's own examples would produce the frequency prior the benchmark
exists to punish, and the run would be scoring the prompt rather than the drawing.
`test_the_prompt_never_seeds_an_answer_from_the_sheet_under_test` asserts that no mark, member
size or detail number from this sheet appears in it. Two prohibitions come from the same comparison: do not transcribe the schedules,
notes, title block or loose dimensions (the weaker description spent two thirds of its budget on
them, all already indexed, all displacing pairings), and never carry an unreadable value forward
(both descriptions answered nearly every column with one repeated size rather than once saying a
mark was illegible). That second rule had to be widened once it was measured: it forbade
REPEATING the last value read, and the failure that got through was a fresh guess at the
unreadable part — "column HSS8X8X1/8 (marking illegible beyond HSS8X8, exact thickness not
readable)", both halves in one sentence. Only the value survives retrieval: the chat answered
with the size and dropped the caveat, and the scorer recorded an invented member size. An
illegible item now gets NO value at all, partial readings included, because a value written
beside the word "illegible" is still a value. `VLM_MAX_TOKENS` IS a lever for this format, which is the reverse of what the prose prompt
showed. Given 10000 the old prompt wrote 1285 and 2231 tokens and stopped on its own, so room
looked irrelevant; but "At 8/B: footing F12, column HSS8X8X3/8." carries the same fact in far
more TOKENS, since every mark and member size is one word and many tokens. At 1500 both providers
failed on the same sheet from opposite ends — Claude truncated mid-grid and every intersection
past the cut became a question the chat could not answer (8 of 19 footing cases, which read as a
comprehension regression), while Gemini 3.1 Pro spent the whole budget reasoning and returned 98
characters, discarded as too short. A thinking model bills its reasoning from the SAME
`max_output_tokens` as its answer, and 3.1 Pro does not let you turn it off. Neither log line
named the budget, so both looked like model quality; `vlm.describe_page` now reports the
`stop_reason` on a discarded reply and says outright that a truncated description leaves the rest
of the sheet with no description at all. The default is 4000.

Demanding that count moved both tags, in opposite directions, which is the clearest statement
yet of what limits this pass. The footing tag went to 95% — 18 of 19, no wrong answers, nothing
declined, 12 hits on minority marks and its common mark named at exactly the rate the sheet shows
it — while the column tag fell from 57% to 24%, answering one member size at 17 of 21
intersections. A footing mark is large text in a bubble and READS at 61 DPI; a member size with a
fraction on the end does not. Forced to put a line at every intersection, the model filled the
half it could not read with the value from the line above, which is precisely the failure the
illegible rule already forbade — the count rule simply outweighed it. So the prompt now separates
the two in the same breath: a LINE is owed at every intersection, a VALUE is not, and the same
size five times in a row is the signature of filling the count rather than reading the drawing.
The resolution ceiling is the real constraint underneath, and it is why the column tag is where
the remaining work is.

"Why is this description short?" has a THIRD answer, and it is the one the token count cannot
reach at all: the model ended cleanly because it thought it had finished. `gemini-3.6-flash`
wrote 255 tokens of an ARCH E1 foundation plan and stopped of its own accord, so `VLM_MAX_TOKENS`
at 4000 and at 20000 buy the identical description — raising it is the obvious move and it does
nothing. What was missing is COVERAGE, and coverage is checkable without the PDF, because the
prompt has already made the model list the column lines and the row lines: those two lines say
how many intersections the sheet has, and counting the `At <col>/<row>:` entries says how many it
described. `vlm.grid_coverage` measures a description against the grid IT NAMED and warns below
half (`MIN_GRID_COVERAGE`), saying outright that nothing was cut off and the budget is not the
lever. The prompt earned its share of that failure: it said to STOP once every intersection was
covered without ever saying how many that is, and praised "a short description that named every
intersection" — written when truncation was the problem, read as permission to stop when
incompleteness became it. It now asks for the COUNT (six column lines and four row lines is
twenty-four lines owed, empty intersections included, one row line at a time), and short is a
virtue only after that count is met.

`stop_reason == "max_tokens"` has TWO causes and they need opposite responses, so the log reports
how many tokens were actually WRITTEN, not just the budget. A run stored a description of 64
tokens against a 4000-token budget and logged "it is TRUNCATED … Raise VLM_MAX_TOKENS": the model
had written forty-odd words and spent the other 98% reasoning, so more budget buys more reasoning
and not one more pairing. This is the same thinking-model failure that returns 98 characters and
is discarded — it merely cleared `MIN_DESCRIPTION_CHARS` (120) and was therefore kept, indexed,
and read as an account of the whole sheet. Written tokens under half the budget now says so
explicitly and points at the model rather than the number. Watch for the consequence in a
benchmark: that 64-token chunk still occupied one of k=18 slots and still counted as
"description in prompt 40/40".

Every line `describe_page` emits NAMES THE PROVIDER AND MODEL, and a page that worked emits one
too. `VLM_PROVIDER` is read here, at ingest, so nothing downstream can recover it: the benchmark
reads chunks and the chunks do not carry it. "Was that run Claude or Gemini?" therefore cost
three separate investigations, twice on a project whose owner was sure of the answer — and the
two ingests being compared had produced 64 tokens and zero, both from a model that reasons.
Until a `sourceModel` column exists on `chunks`, the log is the only record of which model
wrote a description. What comes back is the
model's account of a drawing, NEVER a quotation from it, and that distinction is carried all the
way through — `chunks.kind`, a `kind="description"` attribute on the prompt's chunk tag, a rule
telling the model to write "the drawing shows…" rather than "the note says…" and to let the
sheet's own text win any disagreement, and a "described" marker on the citation chip. Break that
chain and FR-13 starts lying: a reader clicks a citation and finds none of its words on the page.
Descriptions are deliberately kept OUT of `chunk_identifiers` — that arm is exact-match and
weighted 3x, so a member size the model misread would outrank the chunk carrying the real one.
They are still reachable by dense search and FTS.

A description is PACKED to chunk size like everything else (`chunker.split_description`), because
its length is whatever `VLM_MAX_TOKENS` allowed and it was being stored whole. At 1500 that is
merely large; at 10000 it is one chunk 12-25x the size of every other chunk in the corpus. A long
text embeds toward the centroid of its own content and loses the sharpness a question matches on,
it spends one of k retrieval slots on twenty times the payload of the chunks it displaces from
the prompt, and `embedllm.batch_texts` does not protect it: that splits on TOTAL request tokens,
so a single oversized INPUT goes straight to a provider that may truncate it (Voyage, Cohere) or
reject it (gemini-embedding-001 caps at 2048 tokens per input). The split follows LINE boundaries
and only falls back to word windows for one line that is itself too long — the pairing of a grid
label with a member size is the one fact this pass exists to carry, it lives on a single line,
and a word-count split lands in the middle of one about as often as not. There is no overlap
between line groups, since nothing is severed; the word-window fallback keeps its overlap, since
something is. Every piece keeps the whole-page bbox: splitting the prose gives no piece of it a
narrower claim on the drawing. Watch the reverse failure — thirteen description chunks now
compete for k=18 where one used to, so a query can fill its prompt with them. That is at least an
honest competition through the same RRF as everything else, and `drawing_eval.mjs` records how
many description chunks reached each case so it is measurable rather than guessed at.

Images reach the provider through `llm.complete(..., images=[png])`, which builds base64 blocks
for Anthropic and `inline_data` parts for Gemini. A call that passes no image sends the exact
string it always sent — a cache breakpoint is a prefix match, so reshaping the user turn for
every existing call site would have cost them all their cached prefix on the day this shipped.

Resolution is the constraint, not cost, and `vlm.render` now SAYS so once per process — the
number that decides what can be read at all, which nothing printed until the column tag had been
blamed on three other things. These sheets are ARCH E1 (42x30in): at the 2576px long
edge Claude's high-resolution models accept, that is 61 DPI and 8.2px of text, which reads —
verified against the text layer, including `HSS6.875X0.375`. "Reads" turned out to mean reads a
FOOTING MARK. The measured split is a footing tag at 79-95% beside a column tag at 19% answering
`HSS6X6X3/8` — the size that belongs to ONE ROW of the sheet — at every intersection on it. The
member callouts sit 47pt from their intersections, CLOSER than the footing marks that are read
correctly, so it is not proximity, and the illegible rule has now been widened twice without
moving it: "F9" is two characters and "HSS8X8X3/8" is ten with a fraction on the end. Haiku 4.5
and every pre-4.7 model cap at 1568px, which on that sheet is 5px and cannot be read at all, so
the cheap model is not an option here. `vlm.render` scales UP only for a page that was DRAWN, and that
distinction had to be learned the hard way. "Never scale up: extra pixels carry no extra
information" is true of a RASTER page and false of a vector one — a PDF re-rendered above 1.0
draws its glyphs again at a higher sampling rate. Because 42in at 72pt/in is 3024pt, a blanket
`min(1.0, max_edge/longest)` pinned this whole pass at 72 DPI or below, so `VLM_MAX_EDGE=5000`,
set to test whether resolution was the column tag's limit, rendered 3024px and logged `72 DPI`:
the experiment could not run, and the only thing that revealed it was the log line added for that
same hypothesis. Upscaling now requires a text layer (`_has_vector_text`), because a page without
one is a scan already fixed at its own resolution and there the original rule holds exactly. The
default is unaffected: 2576 is below 3024, so it still downscales.

And then the experiment did not run a SECOND time, for a reason no log line here could have
shown, because the transport was reporting the number it rendered as though it were the number
the model read. `VLM_MAX_EDGE=5000` on a vector sheet now genuinely renders 119 DPI, and the run
scored the column tag at 14% — worse than the 72 DPI run it was meant to beat, with eleven
answers naming `HSS8X5X3/8`, a size written nowhere on the drawing. Read as a resolution result
that is decisive: more pixels made it worse. It was not a resolution result. Gemini scales an
image into 3072x3072 BEFORE tokenizing it, which on a 3024pt sheet IS 73 DPI whatever is sent,
and from Gemini 3 on it then tokenizes to a fixed per-part budget — `media_resolution`, whose
unspecified value is HIGH at 1120 tokens for an image. Extra pixels are resampled into the same
budget. So the two providers' ceilings are 61 and 73 DPI on this sheet, twelve apart, and the
claim written here twice — that Gemini "tiles at 768px with no hard cap, making resolution a cost
knob rather than a wall" — was simply false. It is a wall 19% further out.

`GEMINI_MEDIA_RESOLUTION` (default `ultra_high`, `llm.py`) is the one control that varies what is
read, and it exists only on the image PART: `GenerateContentConfig.media_resolution` stops at
HIGH, which is the default already. It is version-sniffed like the thinking level rather than
listed by model name, and a model that rejects it is retried once without it and latched — with
a matcher deliberately NARROWER than the thinking one, which had to widen to a bare
INVALID_ARGUMENT: there the fallback is another thinking setting, here it is reading the sheet at
the provider's default, so an unrelated 400 must not silently undo it. `_report_resolution` now
prints the read DPI beside the rendered one and warns when they differ, because the gap between
those two numbers is the whole of what went wrong twice.

Measured, at `VLM_MAX_EDGE=3072` and `GEMINI_MEDIA_RESOLUTION=ultra_high`, it is the largest
single move in this file's history and it settles which of the two axes was the constraint. Same
73 DPI as the run before it — the log says so — and the column tag went 14% to 43% while the
footing tag went 74% to 84%: 63% overall against a 43% baseline, 17 of 25 hits on minority
labels, ZERO abstentions, and the description itself grew from 406 to 601 tokens because there
was more the model could resolve to write down. Pixels were never the lever; the IMAGE TOKEN
BUDGET was, and `VLM_MAX_EDGE` had been the knob under the light.

It also changed what the misses ARE, which is the finding that matters more than the score. Eight
of the eleven are drift — 5 of 8 on columns, 3 of 3 on footings, every one naming the truth at an
intersection 130-162pt away with the bay at 130. `invented` fell from 11 to 4. The failure is no
longer "cannot read the glyph" and is now "read it correctly, placed it one bay off", which is
exactly the boundary where more resolution stops helping and starts hurting: the grid bubbles are
at the sheet's edge, the intersections are in the middle.

That boundary broke a report gate, in the same way the pooled baseline and the minority-hit
defence each broke once. ANSWER CONCENTRATION cannot tell a frequency prior from a label read
correctly and misplaced, because the two produce the identical count — a tag that reads row F's
size and smears it up into rows B and C over-names that size exactly as hard as a tag that never
looked. So the report printed "its hits ride on a frequency rather than on the intersection each
question names… right by coincidence, while reading nothing" over the column tag's best run,
three lines below its own drift annotation saying 5 of its 8 misses named the truth one bay away.
`tagConcentration` now asks how many of the over-named label's MISSES are drift and refuses the
prior verdict when most of them are. It is not a defence of the score — a misplaced read is still
wrong at that intersection — but the two want opposite fixes, and naming the wrong one points the
next change at comprehension when what is missing is locality.

The next run then scored 80% — 32 of 40, both tags beating their baseline for the first time
(columns 71% against 52%, footings 89% against 32%), 20 of 32 hits on minority labels, zero
abstentions, one invented. The column tag's concentration INVERTED: it named `HSS8X8X3/8` on 33%
of its answers where the sheet shows it 52% of the time, which is the opposite of the fixation
this section has been chasing since the beginning. Five of the eight remaining misses are drift.

And that run is where the methodology broke, because it is the best number in this file and
nothing on disk can say what produced it. The code that writes a description was BYTE-IDENTICAL
to the 63% run before it — the only commits between them added `crops()`, which nothing calls,
and a scorer wording gate that cannot move a tally — yet the description came back at 307 tokens
where the previous one was 601, on the same sheet at the same 73 DPI. So either a `VLM_*` setting
moved between the two ingests or that is simply the spread of asking one model twice, and the
repository could not tell the difference. Every comparison recorded in this section is n=1, and
n=1 cannot separate a 17-point improvement from a 17-point spread.

Two things now make that answerable. `vlm._report_settings` logs, once per process, every setting
that decides a description — model, thinking level, media resolution, output budget, crop bays —
because the DPI line was added for exactly this reason and stopped one level short: the provider
and model were recoverable afterwards and the settings that have each moved a tag twenty points
were not. And `drawing_eval.runHistory` reads the run files the harness has always written and
prints what this set has scored BEFORE, grouped by the description chunk ids: runs sharing a
corpus are one sample however often they are re-scored, and runs with different ids are separate
INGESTS. Same corpus scoring differently is a separate and louder line — `temperature: 0` means
same chunks in, same answer out, so a disagreement there is the SCORER or the chat path moving
under the set, which nothing else in the report can see.

That feature then shipped two mistakes of exactly the kind it exists to prevent, and both showed
up on its first real run against 25 accumulated run files. It keyed a corpus on the sorted chunk
ids, so every run from BEFORE the vision pass existed — which has no description ids at all —
collapsed into one group under the empty string and was reported as a single corpus that had
scored 35%, 38%, 40% and 48%. That is the alarm claiming `temperature: 0` had been violated, on a
corpus that does not exist. An empty list is not an identity; a run with no descriptions is its
own sample and can never be a re-score of anything. And it quoted the range across ALL ingests as
"the error bar", which on a real history reaches back to runs with no vision pass — a 63-point
number that measures the project's history rather than any configuration's spread, printed in the
one place a reader is deciding how much to believe. The line now shows the last five ingests
oldest first and says outright that the range between them is NOT an error bar: it mixes real
changes with run-to-run spread, and only REPEATING one configuration separates those. A benchmark
may report bad news; it may never invent it, and a number offered as evidence has to be evidence
of the thing it is standing next to.

With that fixed the tail reads 38%, 43%, 63%, 80%, 83% — the last of those 33 of 40 with both
tags over baseline (columns 76% against 52%, footings 89% against 32%), 19 of 33 hits on minority
labels, and every tag's concentration "in step with the sheet" for the first time: the footing tag
named F9 on exactly the 32% the drawing shows it. Five of the six remaining misses are drift, and
the sixth is `HSS16X0X1/2` for `HSS10X10X1/2` — a glyph misread, not a fabrication. That
trajectory is monotone across the last four, which is what a real improvement looks like and what
run-to-run noise does not; it is still not the repeat measurement, and the report says so every
time it prints.

`_report_settings` earned its place on the first run it saw. That run scored 53% — a 30-point
drop from the 83% before it — and the line named the configuration in one glance:
`VLM_MAX_TOKENS=20000 GEMINI_THINKING_LEVEL=low GEMINI_MEDIA_RESOLUTION=ultra_high`. Without it
this would have been another unexplained swing filed next to the others, and the temptation would
have been to explain it with whatever had changed in the repository — which was nothing that
touches a description. It does NOT identify the cause: two settings moved at once and the run they
are being compared against was never logged, so the comparison has three unknowns in it. What the
line buys is knowing that, rather than guessing.

The failure it produced is the old one exactly: the column tag at 19%, `HSS6X6X3/8` on 14 of 21
answers where the sheet shows it on 3, and the footing tag untouched at 89%. That asymmetry has
been stable through every configuration in this section — a footing mark in a bubble reads and a
member size with a fraction on the end is the thing under pressure — and it is why the column tag,
not the score, is the number to read. The `placement` gate behaved correctly under it, and only
just: 5 of the 11 misses naming the over-named size were drift, one short of the majority that
would have called it placement rather than a prior. It is a 50% threshold on eleven cases, so
treat the two verdicts as neighbours near the boundary rather than as opposites.

Then the one-variable run happened, and it answers the question the paragraph above had to leave
open. Holding everything else exactly where the 53% run had it — `VLM_MAX_TOKENS=20000`,
`ultra_high`, 3072 — and moving `GEMINI_THINKING_LEVEL` from `low` back to `minimal`: 65% against
53%, the column tag 33% against 19%, and the footing tag 19 of 19. **`minimal` beats `low` on this
pipeline**, which is the opposite of what this section claimed for a year of its own history. Still
n=1 per arm, but for the first time one variable moved on its own, which is worth more than the
four confounded comparisons that came before it. What is still unexplained is the 83% run, and the
remaining suspect is now `VLM_MAX_TOKENS`: the same thinking level at 20000 gives 318 tokens of
description and a 33% column tag, where the 83% run gave 602 and 76%.

The footing tag reaching 100% is the first perfect tag in this file. It is also the tag that has
never been the problem.

And the column tag's misses finally said what they are. Of its fourteen, THIRTEEN carry the
thickness exactly — 3/8, 5/8 and 1/2 each landing where the drawing puts them — and get only the
section wrong, always `8X8` read as `6X6` and never once the reverse. The report called that
"reaching for one label far more often than the drawing offers it… right by coincidence, while
reading nothing". A model reading nothing does not place three different thicknesses correctly
thirteen times. It is one glyph pair misread, in one direction, at every intersection on the
sheet — which is a RESOLUTION failure on half of a compound label, and exactly the kind a crop at
roughly 990 DPI makes go away.

`componentMisreads` measures it. A footing mark is atomic; a member size is a SECTION and a WALL
THICKNESS printed as one string and read as two facts, and every measure in this report that
counts labels was treating them as atoms. That is the FOURTH time a measure here has read a
specific failure as a guess for want of looking inside the thing it was counting — after the
pooled baseline, the minority-hit defence and the placement gate — and the verdict is now gated on
it the same way.

The condition that makes it worth anything is that the held part must VARY. A tag answering one
constant string holds whichever component the truth happens to share with it: answer `HSS6X6X3/8`
to everything on a sheet whose columns are mostly X3/8 and the thickness "matches" every time,
from a model that never looked. Reading shows up as the held part TRACKING the drawing — three
different thicknesses, each where the sheet puts it — so one distinct held value is an artifact of
the truth distribution and several is the claim. Without that condition the gate excuses the exact
fixation the concentration measure exists to catch, and the old tests proved it: they fired on the
first fixture built to represent a pure frequency prior.

The citation check fired on the same run: 8 of 40 answers named a label that appears in none of
the chunks they cited, including two that scored CORRECT. FR-13's chain does not hold for those,
and nothing but this line would have said so.

The next run falsified the obvious follow-up and found something better. Holding `minimal`,
`ultra_high` and 3072, and moving `VLM_MAX_TOKENS` from 20000 back to 4000 — the last unlogged
difference between here and the 83% run — scored **25%**, the worst result since descriptions
existed. The prediction was that columns would climb back toward 76%; they went to 24%, and the
FOOTING tag, which had been 84-100% through every configuration in this section, fell to 26%. The
description was 604 tokens against the 83% run's 602, which also disposes of length as an
explanation for anything.

So `VLM_MAX_TOKENS` is a large lever in a direction nobody predicted, OR the 83% run was at these
same settings and this is the repeat measurement finally arriving with a 58-point spread. The data
cannot separate those, because the 83% run predates `_report_settings`. One more ingest at
`minimal` + 4000 decides it, and no reading of this section is safe until it happens.

What the run did settle is the SHAPE of the failure, and it needed one more measure to see.
`drift` annotates each miss on its own — "the truth at 3/B, 148pt away" — which reads as N
independent slips. Seven of the footing tag's ten drifted misses were the IDENTICAL offset: one
column line over, same row. Column 4 answered with column 3's footing, 4.6 with 4's, 6 with 4.6's,
7 with 6's, 9 with 8's, every one the same direction and every one exactly one grid step. That is
not ten mistakes, it is ONE mistake made seven times: the model named the grid correctly and then
walked its values along that grid off by one.

`systematicOffset` reports it, in GRID INDEX space rather than points — "one column line over" is
the claim, and the bays here run 130 to 218pt, so no distance can say it. An enumeration error is
a third failure distinct from the two this section already separates: not a misread glyph, which
wants resolution, and not a correctly-read label dropped at an arbitrary neighbour, which wants
locality. It is the one failure a crop removes COMPLETELY, because a crop is handed its coordinate
instead of counting its way to one.

Writing the tests exposed a limit in `drift` worth stating rather than fixing. It measures the bay
as the SHORTEST gap in the set and looks 1.5 bays out, so on a sheet whose bays run 130 to 218pt
the widest pair (1.5 x 130 = 195) falls outside the window. Every drift count in this file is
therefore a LOWER bound, and so is every offset built on one. Widening the threshold would rebase
every drift figure here against runs that never measured it, which is the same reason drift is
reported and never scored.

**And then the error bar arrived, and it eats most of this section.**

Two ingests at a byte-identical, fully logged configuration — `VLM_MAX_TOKENS=4000`,
`GEMINI_THINKING_LEVEL=minimal`, `GEMINI_MEDIA_RESOLUTION=ultra_high`, 3072, same model, same
sheet, same prompt, same commit — scored **25% and 68%**. The footing tag inside those two runs
ran **26% and 95%**. Nothing moved between them but the model being asked twice.

A 43-point spread set-wide, 69 on the tag underneath it, is WIDER THAN EVERY EFFECT THIS SECTION
CLAIMS TO HAVE MEASURED. `minimal` beating `low` was 12 points. `media_resolution` as "the largest
single move in this file's history" was 20. The four-rules change, the count rule, the grid-naming
rule — every one of them is a single run against a single run, and every one of them is smaller
than the noise. They are not refuted; they are UNSUPPORTED, which is a different and more
uncomfortable thing: the experiments were never powered to see what they reported.

What survives is what was never a between-run comparison:

  * The mechanical facts, which are properties of code and APIs rather than measured effects —
    Gemini's 3072 cap and its fixed per-part image token budget, `thinking_level` replacing
    `thinking_budget` with omission at the TOP of the scale, the prompt heading leaking into 25 of
    40 answers, the citations that name a chunk which cannot support them.
  * The failure SHAPES, which are observations INSIDE one description rather than differences
    between two: thirteen of fourteen column misses carrying the right thickness and only the
    section wrong; seven of ten footing misses sharing one column-line offset. Those are
    structural claims about a single run's own misses, and a coin flip does not produce them.

What does not survive is any sentence of the form "configuration A scores better than B" on n=1.

`--label` is the repair, and it is the one thing the harness cannot work out for itself: `VLM_*`
is read by the WORKER at ingest and the chunks carry none of it, so only the person running the
ingest can say what configuration produced it. Copy it off `_report_settings` in the worker log,
pass it to the benchmark, and two ingests sharing a label are the same experiment repeated.
`runHistory` then reports the spread between them as an ERROR BAR — printed FIRST, above every
other history line, because it bounds what all of them may claim — and names the widest TAG
separately, since the set-wide number averages the variance away exactly where it is worst. A
wrong label is worse than no label: it manufactures a measurement rather than merely lacking one.

The size of that spread is itself the next question this pass has to answer. Forty cases against
one description is a small sample of a stochastic generator, and the fix is more ingests per
configuration rather than more configurations — which is the opposite of how every experiment
above was run.

The two survivors then turned out not to be independent of each other, which is the fifth time a
measure here has read one failure as another and the first time two of them did it to the same
cases at once. Re-scoring the 68% corpus (identical project, identical tallies, identical drift
list — `temperature: 0` means a re-score of one corpus is arithmetic, not a sample) printed both
gates on the column tag, two lines apart: "3 of those 6 are the SAME offset — 1 row line down",
and then "10 of its 11 misses carry the right thickness and miss only the section". Those are the
SAME three answers. Row C's columns are HSS8X8 where row F's are HSS6X6 at the same thickness, so
"took the value one row line down" and "read 8X8 as 6X6" predict the IDENTICAL string at 2/C,
4.6/C and 7/C. The report offered each as independent evidence for a different mechanism, and the
two mechanisms want opposite fixes — a crop for the enumeration, resolution for the glyph.

The tie-break is the misses the other hypothesis cannot reach, and here it is one-sided. That same
substitution appears on four more misses (2/B, 8/B, 8/C, 9/C) where NO neighbour holds what was
said, so displacement is not available as an explanation at all; no drifted miss needed an offset
that a substitution could not produce. The misread has evidence of its own, the offset has none,
and the three ambiguous cases belong to neither.

`componentSwap` decides it per miss: the single edit that turns one label into another, or null
for two edits, no edit, a hedge, or a label with no parts. Only misses it CANNOT explain vote on
an offset. The asymmetry is the point — a footing mark is atomic, so `F3` for `F4` has no
substitution to be confused with, and the seven-of-ten footing offset this function was written
for stands exactly as it did. On a compound label over a sheet with this few distinct sizes, an
offset is close to unprovable, and the report now says that in the place it used to assert one.
When neither side has a miss the other cannot account for, it says that too rather than picking.

Read that against the error bar rather than beside it. The error bar bounds what a comparison
BETWEEN runs may claim; this bounds what a shape WITHIN one run may claim, and those shapes were
listed above as the things that survived. One of them still does — nine of eleven misses one edit
from their truth, always toward 6 (8X8 read as 6X6 nine times, 10X10 as 16X10 once, never the
reverse) — and it is a glyph collapse, which is the resolution argument the crop was always for.
The enumeration error is not in evidence on this tag. It is in evidence on the footings, where the
labels cannot confound it.

None of that run is a new measurement, and the reason is worth keeping: it was invoked without
`--label`, so the repeats group was empty and the ERROR BAR line — the one thing that would bound
every number above it — did not print. The flag is the whole of what the previous commit added,
and a run without it is another single point on a scale nobody has calibrated.

`vlm.crops(page)` is that lever's geometry, and nothing more — one display-space rectangle per
grid intersection, labelled `<column>/<row>` off `grid.py`, with no model call and no rendering.
On the sheet measured here it is 187x223pt against a 3024x2160pt page: 0.6% of the area, so the
same 3072px ceiling that buys 73 DPI on the whole sheet buys roughly 990 on a crop. The size is
`VLM_CROP_BAYS` (0.6) times the MEDIAN bay per axis, and median rather than minimum is load-
bearing: the columns here are 130 to 218pt apart, and sizing every crop off the 130 cuts the
furthest footing label (83.7pt out) from the crop that exists to carry it. The minimum bay is the
right unit for `drawing_eval.mjs`'s drift annotation and the wrong one for this.

The second thing a crop buys is not resolution at all, and it is the half the DPI argument keeps
hiding: the model is no longer asked WHERE the grid is. The measured failure is a label read
correctly and placed one bay off — five of seven footing misses in one run — and the bubbles that
would settle it are at the sheet's edge while the intersections are in the middle, so a
higher-resolution whole-sheet image makes that worse rather than better. The crop's label comes
off the PDF's own geometry and is handed to the model as given.

Which is exactly why the grid's NAMES had to be got right first, and Phase B found they were not.
`grid.bubbles` reports DISPLAY coordinates, so on a /Rotate 90 sheet the numbered lines share a
display x and `axes` files them as the ROWS: the labels stay correct and the two axis names swap.
That was survivable while the generator was the only reader — a case asked about "column line B"
and derived its answer at the same point, self-consistently — and it stops being survivable the
moment a label is handed to a model as ground truth, because "B/2" for an intersection the
drawing calls 2/B is then our own error, agreed on by both readers of `grid.py` at once. Geometry
cannot break that tie, so the CONVENTION does (`grid.transposed`): column lines are numbered, row
lines are lettered, applied only when one axis is entirely numeric and the other entirely
alphabetic, and never otherwise. The fix belongs in `intersections` and NOT in `axes`, which was
the first attempt and was wrong in a way worth recording: the two dicts hold different
coordinates — an x per column, a y per row — so swapping them does not rename the axes, it
reflects the whole grid about its diagonal. A transposed sheet assembles its point the other way
round instead.

`drawing_truth.py --crops [--against set.json]` prints the dump and checks it: every crop centred
on the set's own `intersectionPt`, every `labelDistancePt` inside its crop, every intersection the
set asks about present. It exits non-zero on a disagreement. That check is worth stating carefully
— both sides read `grid.py`, so it cannot catch a grid they are wrong about together. That blind
spot is still `--explain` plus one person looking at the sheet.

Its usage kind is `vlm`, and that has to exist in THREE places or the pass fails in a way that
looks like nothing: `usage.KINDS`, the `UsageKind` Prisma enum, and the `@cdip/shared` union the
dashboard labels from. It did not, first run — and the cost was not a missing dashboard row.
`usage.record` raised on an unrecognised kind from INSIDE the model call, after the request had
gone to the API and come back 200 OK twenty-six seconds later, so `complete()` caught it and
returned None: a description bought and discarded to report a typo in a constant. That guard is
now a log line, because this module's own contract is that accounting never breaks the pipeline
and every other failure in it already honoured that. `workers/tests/test_usage.py` asserts both
halves — an unknown kind does not raise, and `KINDS` is checked against the enum text itself so
the vocabulary cannot drift again. (It already had: `rerank` was in the enum and missing from
the shared union, so reranker spend reached the dashboard with no label.)

A failure is never a failed page. An unavailable provider, a refusal, an empty or too-short
reply all return None and the page keeps everything else it produced — a page without a
description is exactly as good as it was before this existed. A reply under
`MIN_DESCRIPTION_CHARS` is discarded rather than stored, because "I cannot see the image"
written into a chunk would be retrieved and cited as though it described a drawing.

## Source verification chain (never break it)

Answer → chunk_id → page → bounding box → original PDF. Summaries and chat answers reference
chunk IDs; the UI maps chunk IDs back to pages/regions for click-to-highlight.

## Project roles — whose questions the summaries answer

A project is created with a name, ONE OR MORE roles (`projects.roles`, the `Discipline`
vocabulary via `PROJECT_ROLES` in `@cdip/shared`), and a description. The roles are read by
`summarize._system_blocks`, which appends `_role_focus(...)` as a SECOND system block —
the first block stays byte-identical so it keeps its cache breakpoint. The instruction is
about ORDER and DETAIL, never omission: a plumbing reader still needs the structural note
that moves their pipe. Roles never filter extraction, classification, chunking or retrieval.

## Hierarchical summarization — on demand, never automatic

Bottom-up only: page → section → portion → project. Never summarize 1000 pages in one call.
Each level cites chunk IDs from the level below. Summaries are stored as structured JSON with
sources. Use the active provider's batch API for bulk page summaries (`SUMMARY_USE_BATCH`).
Pressing the button first shows a cost estimate (`apps/api/src/summaryEstimate.ts`) that mirrors
the worker's tier structure and prices it at the model `SUMMARY_PROVIDER` resolves to; a model
missing from `RATES` is priced by family rather than at the default frontier rate.

Every tier gets exactly one salvage retry (`summarize._parse_or_retry`): a truncated answer is
re-asked with more room AND a shorter target, anything else gets a formatting reminder. The
rollups share it with the page tier — without it one bad rollup response silently dropped a
whole discipline to `_merge_lower`, which concatenates the level below in place of the summary
the user reads.

The section tier exists to BOUND the portion rollup's input (40 pages → 4 section summaries →
one portion call). It is therefore skipped when a discipline has ≤ `SECTION_SIZE` (10) pages —
`summarize.needs_section_tier` — because `group_sections` yields exactly one group there and the
call would only restate the page summaries before the portion rollup restates them again. A
one-page discipline costs one rollup call, not three.

**Nothing is summarized until a user asks.** Each discipline card carries a "Generate summary"
button → `POST /projects/:id/portions/:portionId/summarize` → the `summarize-portion` job runs
`summarize.run_portion`, which summarizes ONLY that discipline's pages (reusing any page summaries
that already exist, so a second discipline over the same pages is nearly free). The project rollup
is its own button (`POST /summaries/project` → `summarize.run_project`) and combines the portion
summaries that exist. `POST /summaries/rebuild` is the admin full re-run (job name `rebuild` →
`summarize.run`). `portions.summaryStatus` tracks the lifecycle
(`none|queued|running|ready|failed|stale`); a re-scrape that changes a discipline's page set marks
its summary `stale` and keeps the text rather than deleting work the user paid for.

## RAG chat flow

question → HYBRID retrieval → Claude prompt (chunks + inline metadata + chat history) → answer
with cited chunk IDs → map to clickable page links. Claude only ever sees retrieved chunks,
never raw PDFs. Cache frequent retrievals in Redis (the key includes the retrieval mode).
Persist the full exchange to PostgreSQL.

Retrieval is THREE searches run CONCURRENTLY and fused by weighted Reciprocal Rank Fusion
(`apps/api/src/retrieval.ts`, `HYBRID_RETRIEVAL=true`): the dense embedding
(`EMBEDDING_PROVIDER` → Qdrant, filtered by project + optional portion), a Postgres
full-text search over `chunks.text`, and an EXACT identifier lookup weighted 3x. Dense alone cannot separate `S102A` from `S201` — both
are "a structural sheet number" — so every question naming a sheet, a detail or a member size
depended on luck. The FTS side uses the `english` configuration on BOTH the index and the
query (`simple` keeps stop words, so "what is S102A" demands a chunk containing "what" and
matches nothing) and OR's the question's lexemes rather than AND-ing them, building the
tsquery from `tsvector_to_array` so it is injection-safe. A failure in any arm degrades to
the others rather than failing the question.

The identifier arm exists because FTS treats `S102A` as one more word (and `english` mangles
`A-301` into the lexeme `-301`, dropping its leading letter). What counts as an identifier is
defined ONCE, in the `cdip_identifiers()` SQL function — the worker calls it writing a chunk
(`db.replace_page_chunks` → `chunk_identifiers`), the API calls it on the question. A regex
duplicated across Python and TypeScript would drift, and a drift means a question's identifiers
silently stop matching the documents'. The extractor is case-SENSITIVE so prose stays out of
the index; the query side uppercases first so someone can type "what is s102a". The 3x weight
is arithmetic: at 2x an identifier hit exactly TIES a chunk both similarity arms rank first,
and the tie breaks alphabetically.

`RERANK_PROVIDER=cohere|voyage` (`apps/api/src/rerank.ts`, off by default) then widens the
fused pool to `RERANK_CANDIDATES` and lets a cross-encoder that reads the question and each
chunk TOGETHER pick the final cut — the one thing an embedding cannot do, since by the time a
chunk is a vector the question is not in the room. A missing key, a failed call or a malformed
reply all leave the fused order: reranking improves a working system, it is never a dependency
of one. Cohere bills per SEARCH, so `usage.ts` prices `rerank-*` per-million-searches and
rerank.ts records one search as one unit.

`benchmarks/retrieval_eval.mjs` is what makes any of this checkable: it runs a tagged question
set (`retrieval_eval_set.json`) through the REAL `retrieveChunkIds` and reports recall@k and
MRR, per tag. Run it before and after a retrieval change — every claim in this section is
otherwise unfalsifiable. A case says what should come back as `expectedText` (a phrase quoted
off the sheet), `expectedPages` or `expectedChunkIds`; prefer TEXT, because a combined page
number is derived from document order and a re-upload silently repoints every case in the file
at a different sheet while the run still prints a number. That is not hypothetical: a set
marked up from the source PDFs' filenames rather than the combined numbering reported 40% and
20% recall for two configurations that were both returning the right chunk at rank 1. So every
expectation is now checked against the corpus BEFORE the run, and one that cannot match is an
error rather than a reported miss — a benchmark may report bad news, never invent it.

Recall is not answer quality, and on a text-heavy set the two come apart in one specific place:
GEOMETRY. A sheet's text layer holds every footing mark and every member size, so retrieval
reports 100% recall honestly — but it holds them in two separate runs, one of sizes and one of
marks, because the pairing between them is drawn as a LEADER LINE and is not text at all. No
chunker recovers a fact that was never written. `benchmarks/drawing_eval.mjs` measures that gap:
it runs the REAL `retrieveChunkIds` + `answerFromChunks` and scores the ANSWER, and it is the
gate the VLM work has to pass through — if the pipeline already answers these, it does not need
vision. Cases are GENERATED, never hand-written and never captured from the app, by
`benchmarks/drawing_truth.py`, which derives each answer from the PDF's own coordinates: grid
bubbles are circles holding exactly one label (a detail callout holds two, and the drawing
frame's zone letters are not circled at all — mistaking those for grid lines is how the footing
at grid 7/C got read as F10 when it is F12), and a candidate case is REFUSED unless its reading
survives jittering the intersection 20pt in eight directions. Finding those bubbles now lives in
`workers/src/grid.py`, which the generator imports, because the vision pass needs the same
coordinates to decide what to CROP and a grid detected twice is a grid that drifts — the lesson
the identifier regex, the combined-numbering rule and the storage switch each paid for once. It
costs something real and the module says so: while the generator was the only reader, the eval
could falsify anything the pipeline believed about the grid, and once both sides read the same
bubbles a misdetection is wrong in both at once — the crop labelled 4/B and the expected answer
for 4/B agree with each other and disagree with the drawing. One definition plus a stated blind
spot beats two definitions and a silent divergence; the blind spot is covered by `--explain`,
read by a person once per sheet. Scoring is SIX-way, not pass/fail:
`correct`, `wrong` (named the nearest neighbouring label), `off-target` (named some other label
of that kind, from elsewhere on the sheet), `invented` (named something SHAPED like a mark of
that kind but written nowhere on the drawing), `hedged` (named the truth and the distractor), and
`abstained` (named no label at all). `invented` needs the label's SHAPE, which no vocabulary
built from the sheet can supply, so `drawing_truth.py` emits a `labelPattern` per case — defined
once, travelling with the cases, never rewritten in JavaScript. It exists because one description
of this sheet answered nearly every column question with `HSS9X9X3/8` — a real AISC square
section, which is what makes it plausible, and one that appears nowhere on THIS sheet, whose
columns are HSS8X8, HSS6X6 and HSS10X10. Matching neither the truth nor the distractor nor
anything else on the drawing, all 21 cases scored ABSTAINED. A size nobody specified, reported as a
refusal to guess. A set generated before `labelPattern` existed still runs, and the report says in
as many words that it cannot tell an invented label from a refusal.
`invented` needs one more thing the set cannot supply: the sheet's WHOLE label vocabulary.
`off-target` means "another mark of this kind, from elsewhere on the drawing", and the scorer
was building that vocabulary out of the cases' own `expected` and `distractor` — i.e. only the
intersections the set asks about. A mark that is really on the sheet, at an intersection no case
covers, therefore scored INVENTED: the one outcome that accuses the model of fabricating. It
happened on the first run to produce any, where two of three were `F6` and `HSS5X5X3/8`, both of
which the description places at 9/C. `drawing_truth.py` already reads every footing mark and
member callout on the page to find the nearest one, so it now emits that inventory as
`sheetLabels` per case and `labelVocabulary` prefers it. A set without the field still runs on
the old vocabulary, and the report says in as many words that `invented` is overstated until it
is regenerated.
`drawing_truth.py --backfill <set.json>` adds the labelPattern field to such a set WITHOUT
re-deriving any answer, which is sound only because the pattern is a function of the TAG alone — it depends on
no geometry, so the result is byte-identical to what a full regeneration would write for that
field. Regenerating from the PDF is still the real fix, since it re-derives the truth too. How
much this matters scales with ABSTENTIONS: at zero or one it is nearly harmless, and the first
run to produce a real description declined 15 of 40, any of which could be an invented mark
filed as a refusal. The first vision output good enough to be worth scoring is exactly the one
that makes the blind spot expensive. A model that declines is not a model that is wrong — on
drawings "the sheet does not show this" sends someone to look, while a confident wrong footing
mark gets poured — and collapsing the two would hide the only failure that is dangerous while
punishing the behaviour FR-14 asks for. But `off-target` has to be its own outcome for that line
to hold. Knowing only the truth and its neighbour, the scorer filed every third-label answer
under `abstained`, i.e. under SAFE: `wrong` requires naming the NEAREST neighbour, so the further
an answer landed from the right intersection the safer it scored. A run really did go from 11
correct / 4 wrong on the column tag to 0 / 0 with all 21 cases "abstained" — both buckets
emptying at once, which no amount of restraint produces. So each tag's whole label vocabulary is
built from the set and any mention of it counts as an answer; the cost is that a refusal listing
candidates scores off-target, which is why every run now writes its answers to
`benchmarks/runs/` (gitignored) instead of computing them and throwing them away unless asked for
`--json`. The scorer has its own tests (`node --test benchmarks/drawing_eval.test.mjs`), because
a matcher that finds "F9" inside "F90" reports a wrong answer as right.

Six outcomes still cannot express the failure that matters most once a description is good: the
RIGHT label at the WRONG intersection. "F10 at 3/B" scores off-target — "named some other label of
that kind" — which reads as a model that could not read the mark. But F10 IS the truth at 4/B,
148pt away, one grid bay across, and five of seven footing misses in one run were exactly that,
alongside five of fourteen column misses. The mark was read correctly and placed one bay off.
`drift` reports it: every miss naming a label whose home intersection is within 1.5 bays is
annotated with where that label really lives and how far, and a tag with two or more says so in a
line of its own. The bay is measured from the cases themselves (the shortest gap between two
intersections), because no constant is right across sheets. It is REPORTED, never scored — a
seventh outcome would silently rebase every tally in this file's history against runs that never
measured it. The distinction decides the next move and the two are opposite: a misread glyph wants
resolution, while a correctly-read label in the wrong place wants LOCALITY, and a
higher-resolution whole-sheet image makes locality WORSE, since the grid bubbles are at the
drawing's edge and the intersections are in the middle, so each tile covers less of the sheet.
That is the lever the crop-per-grid-row idea exists for, and it is why raising DPI on the whole
page cannot be the last word.

The harness runs the API's own `retrieveChunkIds` + `answerFromChunks`, and "the real thing" is
load-bearing to the letter: it must pass `kind` exactly as `routes/chat.ts` does, or the
description is serialized without `kind="description"` and the model reads a vision model's
account as though it were words lifted off the sheet — the one confusion the whole vision pass
exists to prevent, mis-measured in the direction that flatters the result. Each row also records
how many retrieved chunks were descriptions, MEASURED rather than inferred from `VLM_*`: those
are read by the worker at ingest, so the benchmark process's environment says nothing about what
is in the chunks it is scoring.

WHICH CORPUS was asked is recorded too, and had to be. The natural way to test a VLM change is
to ingest the same PDF into a FRESH project per configuration, but a generated set carries the
`projectId` it was generated against, so the harness kept asking a project nobody had touched and
returning a stale answer instead of an error. `--project <uuid>` repoints every case; the run
file and the report name the project and, more usefully, the DESCRIPTION CHUNK IDS that reached
the answers. Those ids are the experiment's identity: `db.replace_page_chunks` deletes a page's
chunks and reinserts them with fresh `uuid4`, so an unchanged id PROVES the descriptions were
never regenerated — three consecutive runs reported byte-identical tallies before anyone noticed,
and `temperature: 0` in `apps/api/src/llm.ts` means that identity is evidence rather than
coincidence: same chunks in, same answer out, always. The preflight refuses two shapes this
workflow produces — a set naming more than one project (it would score two corpora and report one
number), and a sheet living on more than one live page of the target project (a re-upload is a
NEW document; only `replacesDocumentId` makes it a revision, so retrieval draws on both ingests
at once and whichever description wins the fusion decides the answer).

How many description chunks EXIST on the sheet is reported next to how many were reached
(`describeCoverage`), because the per-case count cannot tell the two apart and they have
opposite fixes. A run reported `description in prompt 40/40` — every case saw one, which reads
as full coverage — while naming exactly ONE distinct chunk id. Either the description was stored
whole (so `split_description` never ran on that ingest) or the split worked and retrieval
surfaces the same piece for every question, leaving the rest of the sheet indexed and
unreachable. In the second case the obvious next move is the wrong one: raising
`VLM_MAX_TOKENS` writes more of the drawing into pieces nothing retrieves.

Every tag reports its MAJORITY-CLASS BASELINE, because a bare percentage invites the wrong
reading: a sheet reuses a handful of marks, so "always answer HSS8X8X3/8" scores 52% on the
column tag while reading nothing. Across tags that baseline is each tag's OWN majority summed,
never the pooled mode — pooling proposes answering a member size to "which footing mark is at
7/C", which no guesser would do, and it scored this set's null model at 28% instead of 43%. A run
that scored exactly 43%, tying the real baseline on both tags to the case, was therefore told it
had beaten it: the most encouraging line on the screen, produced by the report's own arithmetic.
Alongside it each tag reports MINORITY-LABEL HITS — correct answers naming something other than
that tag's most common label. It is the only figure here a frequency prior cannot produce, and
the only one that separates two runs with identical scores. The first measured run
(CHAT_PROVIDER=gemini, k=18, no descriptions) scored 23% correct / 10% wrong / 68% abstained and
was BELOW baseline on both tags, with all four wrong answers naming a high-frequency label: zero
comprehension plus a frequency prior, not partial success. With descriptions at
`VLM_MAX_TOKENS=10000` the footing tag reached 53% against its 32% baseline, and at least four of
those ten hits were minority marks — the first result here that a guesser cannot account for.

Minority hits read that instinct from one end only — among CORRECT answers — so a tag can look
clean there while every one of its misses is the same word. Each tag therefore also reports
ANSWER CONCENTRATION: which label it reached for most, on how many of the cases it ANSWERED, and
how often that label is actually the truth. The run that first made this matter beat the baseline
overall (63% against 43%) on the strength of one tag: footings scored 74% against a 32% baseline
with 9 of 14 hits on minority marks and named F9 on 33% of its answers where the sheet shows it
32% of the time — calibrated, i.e. reading. The column tag on the same description scored 52%,
tying its baseline to the case, and said HSS8X8X3/8 to 15 of the 18 questions it answered: 83% of
its answers on a label that is the truth 52% of the time. Every other number on that line looked
like half a result, and the 31-point gap had to be worked out by hand from the miss list. It is
measured PER TAG for the same reason the baseline is — a footing question could never be answered
with a column size, and pooling dilutes the concentration it exists to expose. Over-naming is not
the same claim as being wrong: a tag can over-name and still beat its baseline, and that is
exactly what is worth seeing, because it means the hits may be riding on the sheet's own
frequencies rather than on the intersection each question names. MAY, and the line had to say so
after one run, because "the shape of a guess" is the same claim the set-wide verdict already had
to be gated on: the footing tag reached for one mark 18 points more often than the drawing offers
it while 10 of its 12 hits were minority marks. Both are true — it leaned on a label and read the
rest — so the wording is gated, and the set-wide "watch this tag" line picks the tag that looks
like a PRIOR before the tag with the widest gap. A reading tag can lean harder than a guessing
one, and naming it there would bury the tag that is only guessing.

Gated on WHAT took one more correction, because minority hits cannot carry that defence alone and
the next run proved it within the hour. A tag that fixates on a label which is NOT its majority
scores minority hits BY COINCIDENCE: the column tag answered `HSS6X6X3/8` to 17 of 21 questions,
that size is the truth at three of those intersections, and all three landed in the minority
column — a frequency prior over the WRONG frequency, credited as the one figure a prior cannot
fake, on a tag scoring 24% where guessing scores 52%. The hits that count are therefore the
INDEPENDENT ones, naming neither the tag's majority label nor the label it is over-naming, and
the defence is refused outright to a tag that loses to its own baseline. Both gates come from the
same principle as the pooled-baseline fix: the encouraging sentence must be the one the evidence
supports, not the one the arithmetic happens to produce.

That baseline ANSWERS EVERY CASE, which makes raw accuracy the wrong comparison for a run that
declines: it has forfeited the cases it abstained on, so it cannot win on the total however well
it reads the rest. Each tag therefore also reports COVERAGE (how many it was willing to answer)
and SELECTIVE ACCURACY (how it did on those). Random abstention leaves selective accuracy at the
raw rate; abstaining where it is unsure raises it, and that gap is the claim. The report's own
verdict needed the same correction. "The correct answers are a frequency prior" is a claim about
WHICH labels were named, and minority hits measure exactly that — a prior produces them at a rate
of zero — so printing ZERO COMPREHENSION over a run whose hits were 45% minority labels was the
report contradicting its own evidence, on the most decisive line it prints. It is the pooled
baseline mistake again: the strongest sentence on the screen produced by the report's arithmetic
rather than by the system under test. The verdict is now gated on that fraction and states it.

Two things the report could not see were sitting in the RAW ANSWERS all along, and both were
found by reading one run file rather than by any number the harness prints.

The first is a leak of the prompt into the product. `buildSystemPrompt` sorts questions into
numbered kinds and opened by telling the model to "be explicit about which one you are using" —
but only ONE of those kinds has a line prescribed for it (`Construction reference — not from this
project's drawings.`), so for the other the model did the next most obvious thing and copied the
rule's own heading. 25 of 40 answers in one run began with a bare line reading `QUESTIONS ABOUT
THIS PROJECT`: correct, cited answers with a fragment of their own instructions stapled to the
front, shipped to every reader for as long as the chat has existed. The prompt now says outright
never to name the kind of question, which is the repair; `answer.stripPromptScaffolding` removes a
rule heading standing on its own line, which is the guarantee — the same division `citations.ts`
already makes when it sweeps for a bare `chunk:<uuid>` after rewriting the tags it knows about. A
reader must never see the machinery, whatever shape the model invents on a day the wording does
not cover. The stripper is deliberately narrow: only at the FRONT (a heading mid-answer is the
model having gone strange, and deleting it would hide that), only a whole line, and never the
`Construction reference` line, which IS content.

The second is bigger and is still only measured, not fixed. FR-13 — every statement traceable to
a chunk, a page and a bbox, verifiable in one click — is this project's central promise, and
nothing had ever checked it. The scorer grades WHAT was answered and never what the answer was
hung on, so a correct label cited to an unrelated chunk scores full marks while a reader who
clicks it finds nothing. In the run that showed it, several answers read "Per the Column Footing
Schedule on S-100.0, the footing mark at the intersection of column line 4 and row line B is F10"
and cited only the schedule chunk. A footing schedule maps marks to sizes and reinforcing; the
thing being claimed is a POSITION, which is precisely the fact the text layer does not hold and
the whole reason the vision pass exists. `citationSupport` now records, per case, which chunks the
answer cited and whether the label it gave appears in any of them, and the report says how many
answers cite a chunk that could not account for them. It is REPORTED, never scored, like drift —
a seventh outcome would rebase every tally in this file's history — and the line states its own
weakness: it asks only whether the label appears somewhere in the cited text, so a chunk that
merely lists the mark passes. It cannot prove a citation supports its claim, only catch one that
could not possibly. "Cited nothing at all" is kept separate from "cited the wrong thing", because
a claim with no citation has not broken the chain, it never joined it, and the fix is the prompt
rather than retrieval.

FR-14 is amended in one direction only (`apps/api/src/answer.ts`, `CHAT_SCOPE`): a claim ABOUT
THE PROJECT still comes from retrieved chunks and still carries a `[chunk:<id>]` citation, so
FR-13's chain is intact, and a gap in the drawings is NEVER filled from the model's knowledge —
but a construction-DISCIPLINE question ("what is a column?", "what is a shear wall?") is
answered under an explicit "Construction reference — not from this project's drawings." line.
That is a domain gate, not general knowledge: the prompt carries an explicit in-scope list
(structural/civil/geotechnical/architectural/MEP/fire/telecoms, materials and methods, drawings
and specs and BIM, codes, site safety, surveying, sequencing/estimating/QA) because "related"
stretches under pressure and a list does not, and everything outside it gets one refusal
sentence. `CHAT_SCOPE=documents` removes the allowance entirely. Zero retrieved chunks is
therefore a normal case, not a dead end.

Chat history is a view of the FR-23 records: `GET /projects/:id/chat/sessions?window=` lists
past conversations by LAST activity (1h|24h|7d|30d|3m|all|custom from/to), and the web client
remembers the last session per project in `localStorage` so leaving a project and returning
resumes the thread instead of showing a blank panel.

## Claude prompting pattern for grounded answers

- Send only relevant markdown chunks, never full PDFs.
- Include each chunk's metadata inline (document, page, chunk ID).
- Instruct Claude to cite chunk IDs for every claim (or use the API's native Citations feature
  with custom-content chunks).
- Map cited chunk IDs back to bounding boxes for the UI.

## PostgreSQL schema (Prisma)

```
projects(id, name, description, roles[], createdAt)
sheet_regions(id, projectId UNIQUE, relX/relY/relW/relH, version, scrapeStatus, counters)
documents(id, projectId, filename, spacesKey, pages, revision, status)
pages(id, documentId, pageNumber, combinedPageNumber, imageUrl, text,
      discipline, sheetRegionText, sheetNumber, regionMethod, regionVersion, disciplineSource)
portions(id, projectId, name, discipline, startPage, endPage, pageCount, summary,
         summaryStatus, ...)   // UNIQUE(projectId, discipline) — UPSERT, never delete+reinsert
chunks(id, pageId, portionId, text, bbox, tokenCount, embeddingId, kind)  // embeddingId = Qdrant point ID; kind = text|description
summaries(id, projectId, portionId, level[page|section|portion|project], summary JSON, sources)
chat_sessions(id, projectId, createdAt)
messages(id, sessionId, role, content JSON incl. citations, sources, createdAt)
```

PostgreSQL is the single source of truth for references; Qdrant holds vectors only.

`pages.discipline ↔ portions.discipline` is a LOGICAL join, not an FK — a page's discipline comes
from its own sheet number, the portion is the derived grouping. `assign_chunk_portions`
materializes it onto `chunks.portionId` (FK, SetNull) for the chat portion filter, so re-run it —
and refresh the Qdrant payloads — after every regroup. Page-level summaries keep `portionId = NULL`
so they survive a page changing discipline; only section/portion rollups hang off `portionId`.

## UI layout

A new project opens in a three-step setup stepper (`components/ProjectSetup.tsx`): upload PDFs
→ mark the title-block region → let the sheets categorise, then "Open the workspace". Progress
is read from server state (documents / region / scrapeStatus), so it resumes after a reload;
the decision to show it is LATCHED per mount so finishing step 3 does not yank the stepper
away mid-click. "Skip setup" (and the finished hand-off) writes `cdip-setup-skipped:{id}`; the
project header offers "Finish setup" while the project is still incomplete. First arrival in
the workspace runs a spotlight tour (`components/Tour.tsx`, targets marked with `data-tour`),
shown once per browser and replayable from the ? button in the project header.

Signed-in chrome is shadcn's inset sidebar shell (`apps/web/src/components/AppShell.tsx`):
collapsible sidebar (brand, grouped nav, user menu) + a sticky header carrying the sidebar
trigger, the page name, the light/dark toggle and the account menu. Navigation is store state
(`view` + `selectedProjectId`), not a router.

Inside a project, three panes: Sidebar (project summary + portion list) | Middle (chat with
clickable sources) | Right (combined PDF viewer with jump + highlight). Clicking a portion (e.g.
"Structural") switches the summary panel, jumps the viewer to the portion's start page, and
optionally filters chat retrieval to that portion.

### Design system

Every colour is a semantic token defined for both themes in `apps/web/src/index.css`
(`background`, `card`, `muted`, `primary`, `border`, `sidebar`, `destructive`, `success`,
`warning`, plus `chart-1..5` and `status-*` for data viz). Components never carry a raw hex, and
dark mode is a variable swap — the `.dark` class on `<html>`, driven by
`components/theme-provider.tsx` (light / dark / system, persisted). Build UI out of the
primitives in `components/ui` (shadcn, unmodified API) and the app furniture in
`components/shared.tsx` (`PageHeader`, `TextField`, `Notice`, `Modal`, `ConfirmDialog`,
`PageLoading`); charts read their colours from `charts/palette.ts`, which resolves to those same
tokens. The shadcn sources target React 19 — on this app's React 18, any component used as a
Radix `asChild` trigger (`Button`, `SidebarMenuButton`, `Input`, `Textarea`) must keep its
`forwardRef`.

## Parallelism

Runtime reference (queues, polling, capacity, cost): docs/runtime-architecture.md.

Jobs run concurrently per queue (`config.PROCESS_CONCURRENCY` etc., all defaulting to
`WORKER_CONCURRENCY`=4), and horizontally across replicas — throughput is
`replicas × concurrency`. Within one document, pages are extracted by a thread pool
(`PAGE_CONCURRENCY`, `processing._extract_pages`) — queue concurrency does nothing for a single
large PDF. Each page thread opens its OWN `fitz.Document` (a shared one is not thread-safe), and
`PROCESS_CONCURRENCY × PAGE_CONCURRENCY` is what sizes both memory and `DB_POOL_SIZE`. The handlers hand their synchronous body to `asyncio.to_thread`, so
the threads genuinely overlap (PyMuPDF releases the GIL while rendering; everything else is
network I/O).

`process_document` holds a `db.document_lock` (a `pg_try_advisory_lock`) for the whole run and
DISCARDS a delivery it cannot acquire: BullMQ re-delivers a job whose lock lapses, and with
`lockDuration` at the library's 30s default against documents that run for minutes, one 148-page
set really was processed by two executions at once. `WORKER_LOCK_DURATION_MS` (default 10 min)
makes the stall rare; the document lock makes it harmless. Each pool thread's `fitz.Document` is
closed when extraction drains — leaked handles are invisible on Linux but stop Windows deleting
the temp directory, turning a completed job into a `WinError 32`.

Three steps are project-wide rather than per-document — `recompute_combined_numbering`, the
portion rebuild (`upsert_portions`), and `assign_chunk_portions` — so they run inside
`db.project_lock(project_id)`, a Postgres advisory lock keyed on `sha256(projectId)[:8]`.
Without it, two documents of the same project finishing together interleave and corrupt the
manifest. It is a Postgres lock, not an in-process one, because the contenders are separate
replicas; Postgres frees it if a worker dies. Different projects never contend.

`db.connect()` borrows from a `psycopg_pool` (`DB_POOL_SIZE`, default `2 × WORKER_CONCURRENCY`) —
keep `replicas × DB_POOL_SIZE` under the server's `max_connections`. The Spaces client is sized from the worst case
across BOTH queues that touch storage at once (`SPACES_POOL_SIZE` → botocore
`max_pool_connections`): `PROCESS_CONCURRENCY × max(PAGE_CONCURRENCY, SPACES_DOWNLOAD_CONCURRENCY)
+ SCRAPE_CONCURRENCY × SPACES_DOWNLOAD_CONCURRENCY`. A download is NOT one stream — boto3's
transfer manager pulls 8 MB parts in parallel, so one large PDF can occupy the whole pool by
itself, which is what sizing it off the page threads alone missed. An undersized pool fails
quietly, as urllib3 discarding each returning connection and the next request re-handshaking TLS.

Extraction is network-bound, not CPU-bound: each page ships a full-resolution PNG, a thumbnail
and a text file, and a measured production run managed 6.7 pages/minute against Spaces where the
CPU-only benchmark reported 125. `benchmarks/extract_throughput.py --upload` measures the real
thing; without the flag it reports CPU only and says so. Raising concurrency
multiplies the Voyage/Anthropic request rate directly, so raise provider tiers first.

## Non-functional rules

- Async processing via BullMQ workers only; separate API from workers, scale workers on queue
  length; partition Qdrant by project/tenant.
- Thumbnails generated once, served via Spaces CDN.
- Batch embedding calls; reuse embeddings for unchanged revisions.
- Presigned URLs for all PDF/image access; TLS; encryption at rest; project-level RBAC;
  malware-scan uploads; sanitize input.
- Treat extracted document text as UNTRUSTED input (prompt-injection defense).

## Engineering conventions

- TypeScript strict mode across `/apps/web` and `/apps/api`. Zod validation on all API inputs.
- Every phase must end with the app runnable via `docker compose up`, with commands documented
  in README.md.
- All secrets from `.env` (keep `.env.example` current): `ANTHROPIC_API_KEY`,
  `VOYAGE_API_KEY`/`COHERE_API_KEY`/`GEMINI_API_KEY` (whichever `EMBEDDING_PROVIDER` selects),
  the object-storage credentials `STORAGE_BACKEND` selects
  (`SPACES_KEY`/`SPACES_SECRET`/`SPACES_ENDPOINT`/`SPACES_BUCKET`, or the `LOCAL_S3_*` set),
  `DATABASE_URL`, `REDIS_URL`, `QDRANT_URL`. The filled-in `deploy/.env.*` files are
  gitignored; only their `.example` templates are checked in.
