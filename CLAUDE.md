# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

Phases 1–5 done: project CRUD, direct-to-storage resumable multipart upload, worker pipeline
(per-page text/PNG/thumb + OCR fallback + status flow + resume), virtual page manifest, lazy
combined react-pdf viewer, portion detection (rule classifier + Haiku fallback), hybrid
chunking with bbox metadata, Voyage embeddings → Qdrant (payload-partitioned by project,
payloads refreshed after portion rebuilds), RAG chat with chunk-ID citations mapped to
document/page/bbox, portion filter, Redis retrieval cache, FR-23 persistence, and hierarchical
summaries (page → section → portion → project; page level is incremental and can use the
Anthropic Message Batches API via SUMMARY_USE_BATCH=true; every item cites chunk IDs with the
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
caching (cache_control on chat system prompt + retrieved-chunk block in apps/api/src/claude.ts
and on the summarizer system prompt in workers/src/summarize.py); Redis summaries cache
(cache:summaries:{projectId}, API reads / worker invalidates — key format duplicated in
apps/api/src/routes/summaries.ts ↔ workers/src/cache.py); OpenTelemetry metrics (API
Prometheus endpoint :9464 — HTTP/Qdrant/chat latency, queue depths; worker :9465 — job
durations; monitoring/ has Prometheus + provisioned Grafana dashboard, both in docker
compose); README covers DO App Platform/DOKS deployment. Worker logs every pipeline stage
(1/6 download … 6/6 finalize, LOG_LEVEL env, workers/src/logutil.py); DELETE /projects/:id
purges all stores — Postgres cascade + Spaces prefix + Qdrant points + Redis caches
(apps/api/src/cleanup.ts, each stage logged, failures non-blocking).

Chat and embedding need `ANTHROPIC_API_KEY`/`VOYAGE_API_KEY`; without them the worker skips
embedding (chunks wait with NULL embeddingId) and the chat endpoint returns 503. For offline
E2E, both APIs can be pointed at a stub via `ANTHROPIC_BASE_URL`/`VOYAGE_BASE_URL`.

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
1..N within each) is implemented twice — `apps/api/src/manifest.ts` (canonical, unit-tested) and
the recompute SQL in `workers/src/db.py`. If you change one, change both. The same duplication
exists for object keys (`packages/shared` `objectKeys` ↔ `workers/src/storage.py`) and queue
contracts (`packages/shared` ↔ `workers/src/contracts.py`). The citation format is another
cross-cutting contract: Claude is told to emit `[chunk:<uuid>]` (apps/api/src/claude.ts) and
`apps/api/src/citations.ts` (unit-tested) parses/renumbers it.

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

- Frontend: React + TypeScript, TanStack Query, Zustand, Tailwind CSS
- PDF rendering: PDF.js / react-pdf (programmatic page jump + region highlight)
- Backend API: Node.js (Express), REST
- Processing workers: Python (PyMuPDF, pdfplumber, PaddleOCR, OpenCV)
- Database: PostgreSQL + Prisma ORM
- Vector DB: Qdrant (metadata filtering by project/portion)
- Queue: BullMQ + Redis (also used for cache/sessions)
- Object storage: DigitalOcean Spaces (S3-compatible; use AWS S3 SDK with endpoint override,
  e.g. blr1.digitaloceanspaces.com)
- OCR: PaddleOCR
- Embeddings: Voyage AI (voyage-3 / voyage-3-large)
- LLM: Claude via Anthropic API (Sonnet for chat/summaries/reasoning; Haiku for cheap per-page
  classification). Use prompt caching for repeated context and the Batch API for bulk
  summarization.
- Monitoring: OpenTelemetry + Grafana
- Deployment target: DigitalOcean App Platform / DOKS. API and workers scale independently.

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

- FR-1–4: CRUD for projects (name, description, created date, owner); multi-PDF upload per
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
that string reaches Claude Haiku (`classify_region_text` → `extract_sheet_from_region`), which
reports the sheet number; the deterministic `PREFIX_TO_DISCIPLINE` table maps it, so the mapping
never depends on model judgement. Results are Redis-cached by `sha256(region_text)` — hundreds of
sheets share a box layout — and the instructions are prompt-cached.

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

1. Structural split first: by drawing, section, title-block boundaries.
2. Size split second: 400–800 token chunks, 100-token overlap.
3. Every chunk carries metadata:
   `{ chunk_id, document_id, page, portion, discipline, bbox: {x, y, width, height}, text, image_ref, revision, token_count }`

## Source verification chain (never break it)

Answer → chunk_id → page → bounding box → original PDF. Summaries and chat answers reference
chunk IDs; the UI maps chunk IDs back to pages/regions for click-to-highlight.

## Hierarchical summarization — on demand, never automatic

Bottom-up only: page → section → portion → project. Never summarize 1000 pages in one call.
Each level cites chunk IDs from the level below. Summaries are stored as structured JSON with
sources. Use the Anthropic Batch API for bulk summary jobs.

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

question → Voyage embedding → Qdrant search (filter: project, optional portion; top ~15–20
chunks) → Claude prompt (chunks + inline metadata + chat history) → answer with cited chunk IDs
→ map to clickable page links. Claude only ever sees retrieved chunks, never raw PDFs. Cache
frequent retrievals in Redis. Persist the full exchange to PostgreSQL.

## Claude prompting pattern for grounded answers

- Send only relevant markdown chunks, never full PDFs.
- Include each chunk's metadata inline (document, page, chunk ID).
- Instruct Claude to cite chunk IDs for every claim (or use the API's native Citations feature
  with custom-content chunks).
- Map cited chunk IDs back to bounding boxes for the UI.

## PostgreSQL schema (Prisma)

```
projects(id, name, description, createdAt)
sheet_regions(id, projectId UNIQUE, relX/relY/relW/relH, version, scrapeStatus, counters)
documents(id, projectId, filename, spacesKey, pages, revision, status)
pages(id, documentId, pageNumber, combinedPageNumber, imageUrl, text,
      discipline, sheetRegionText, sheetNumber, regionMethod, regionVersion, disciplineSource)
portions(id, projectId, name, discipline, startPage, endPage, pageCount, summary,
         summaryStatus, ...)   // UNIQUE(projectId, discipline) — UPSERT, never delete+reinsert
chunks(id, pageId, portionId, text, bbox, tokenCount, embeddingId)  // embeddingId = Qdrant point ID
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

Three panes: Sidebar (project summary + portion list) | Middle (chat with clickable sources) |
Right (combined PDF viewer with jump + highlight). Clicking a portion (e.g. "Structural")
switches the summary panel, jumps the viewer to the portion's start page, and optionally filters
chat retrieval to that portion.

## Parallelism

Jobs run concurrently per queue (`config.PROCESS_CONCURRENCY` etc., all defaulting to
`WORKER_CONCURRENCY`=4), and horizontally across replicas — throughput is
`replicas × concurrency`. The handlers hand their synchronous body to `asyncio.to_thread`, so
the threads genuinely overlap (PyMuPDF releases the GIL while rendering; everything else is
network I/O).

Three steps are project-wide rather than per-document — `recompute_combined_numbering`, the
portion rebuild (`upsert_portions`), and `assign_chunk_portions` — so they run inside
`db.project_lock(project_id)`, a Postgres advisory lock keyed on `sha256(projectId)[:8]`.
Without it, two documents of the same project finishing together interleave and corrupt the
manifest. It is a Postgres lock, not an in-process one, because the contenders are separate
replicas; Postgres frees it if a worker dies. Different projects never contend.

`db.connect()` borrows from a `psycopg_pool` (`DB_POOL_SIZE`, default `2 × WORKER_CONCURRENCY`) —
keep `replicas × DB_POOL_SIZE` under the server's `max_connections`. Raising concurrency
multiplies the Voyage/Anthropic request rate directly, so raise provider tiers first.

## Non-functional rules

- Async processing via BullMQ workers only; separate API from workers, scale workers on queue
  length; partition Qdrant by project/tenant.
- Thumbnails generated once, served via Spaces CDN.
- Batch Voyage embedding calls; reuse embeddings for unchanged revisions.
- Presigned URLs for all PDF/image access; TLS; encryption at rest; project-level RBAC;
  malware-scan uploads; sanitize input.
- Treat extracted document text as UNTRUSTED input (prompt-injection defense).

## Engineering conventions

- TypeScript strict mode across `/apps/web` and `/apps/api`. Zod validation on all API inputs.
- Every phase must end with the app runnable via `docker compose up`, with commands documented
  in README.md.
- All secrets from `.env` (keep `.env.example` current): `ANTHROPIC_API_KEY`, `VOYAGE_API_KEY`,
  `SPACES_KEY`/`SPACES_SECRET`/`SPACES_ENDPOINT`/`SPACES_BUCKET`, `DATABASE_URL`, `REDIS_URL`,
  `QDRANT_URL`.
