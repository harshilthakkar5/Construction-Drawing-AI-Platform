# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

Phases 1–4 done: project CRUD, direct-to-storage resumable multipart upload, worker pipeline
(per-page text/PNG/thumb + OCR fallback + status flow + resume), virtual page manifest, lazy
combined react-pdf viewer, portion detection (rule classifier + Haiku fallback), hybrid
chunking with bbox metadata, Voyage embeddings → Qdrant (payload-partitioned by project,
payloads refreshed after portion rebuilds), RAG chat with chunk-ID citations mapped to
document/page/bbox, portion filter, Redis retrieval cache, FR-23 persistence, and hierarchical
summaries (page → section → portion → project as a summarize-project BullMQ job; page level is
incremental and can use the Anthropic Message Batches API via SUMMARY_USE_BATCH=true; every
item cites chunk IDs with the jump page derived server-side from the first cited chunk;
section/portion rows cascade-delete on portion rebuild). Not built yet: viewer bbox
highlighting (FR-19).

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
- Workers: `cd workers && python src/worker.py` (BullMQ consumer; deps in `requirements.txt`;
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

## Portion (discipline) detection

Classify pages from sheet-number prefixes plus Claude classification of title blocks:
A → Architectural | S → Structural | P → Plumbing | E → Electrical | M/H → HVAC |
FP → Fire Protection | C → Civil | SP/L → Site/Landscape | D/detail sheets → Details, Legends,
Schedules.

## Chunking strategy (hybrid)

1. Structural split first: by drawing, section, title-block boundaries.
2. Size split second: 400–800 token chunks, 100-token overlap.
3. Every chunk carries metadata:
   `{ chunk_id, document_id, page, portion, discipline, bbox: {x, y, width, height}, text, image_ref, revision, token_count }`

## Source verification chain (never break it)

Answer → chunk_id → page → bounding box → original PDF. Summaries and chat answers reference
chunk IDs; the UI maps chunk IDs back to pages/regions for click-to-highlight.

## Hierarchical summarization

Bottom-up only: page → section → portion → project. Never summarize 1000 pages in one call.
Each level cites chunk IDs from the level below. Summaries are stored as structured JSON with
sources. Incremental: a new upload recomputes only its own page/portion summaries and affected
higher levels. Use the Anthropic Batch API for bulk summary jobs.

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
documents(id, projectId, filename, spacesKey, pages, revision, status)
pages(id, documentId, pageNumber, combinedPageNumber, imageUrl, text)
portions(id, projectId, name, startPage, endPage, summary)
chunks(id, pageId, portionId, text, bbox, tokenCount, embeddingId)  // embeddingId = Qdrant point ID
summaries(id, projectId, portionId, level[page|section|portion|project], summary JSON, sources)
chat_sessions(id, projectId, createdAt)
messages(id, sessionId, role, content JSON incl. citations, sources, createdAt)
```

PostgreSQL is the single source of truth for references; Qdrant holds vectors only.

## UI layout

Three panes: Sidebar (project summary + portion list) | Middle (chat with clickable sources) |
Right (combined PDF viewer with jump + highlight). Clicking a portion (e.g. "Structural")
switches the summary panel, jumps the viewer to the portion's start page, and optionally filters
chat retrieval to that portion.

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
