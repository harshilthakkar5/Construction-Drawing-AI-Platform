# ArcAligned AI

**ArcAligned AI** (repository name: Construction Drawing AI Platform) manages construction projects, ingests very large sets of construction drawing PDFs
(100 MB – 1 GB+, 1000+ pages), and provides AI-powered hierarchical summaries plus a
project-scoped RAG chat assistant with click-to-verify citations — every AI statement
traces to the exact PDF document, page, and bounding box. See
[CLAUDE.md](./CLAUDE.md) for the full architecture.

## Repository layout

```
apps/web            React + TypeScript frontend (Vite, Tailwind v4, shadcn/ui, TanStack Query, Zustand)
apps/api            Express + TypeScript REST API (Prisma, Zod, BullMQ producer)
workers             Python processing workers (PyMuPDF, pdfplumber, PaddleOCR, OpenCV; BullMQ consumer)
packages/shared     Shared TypeScript types (queue names, job payloads, domain types)
monitoring          Prometheus config + provisioned Grafana dashboard
docker-compose.yml  Local Postgres, Redis, Qdrant, MinIO (Spaces stand-in), Prometheus, Grafana
```

## Prerequisites

- Node.js ≥ 22, npm
- Docker + Docker Compose
- Python 3.11+ (workers only)

## Local development

```bash
# 1. Environment
cp .env.example .env
cp .env.example apps/api/.env   # Prisma reads DATABASE_URL from apps/api/.env

# 2. Infrastructure (Postgres, Redis, Qdrant, MinIO + bucket init, Prometheus, Grafana)
docker compose up -d

# 3. Node dependencies (also builds packages/shared)
npm install

# 4. Database migration + Prisma client
npm run prisma:migrate

# 5. Run the apps (separate terminals)
npm run dev:api    # http://localhost:4000 (health: /health, metrics: :9464/metrics)
npm run dev:web    # http://localhost:3000
```

### Python workers

```bash
cd workers
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python src/worker.py       # consumes process-document, scrape-region,
                           # summarize-portion and summarize-project
```

Or containerized, which is also how you scale it:

```bash
docker compose --profile worker up -d --scale worker=3
```

## Parallel processing

Full runtime reference — queues, polling, capacity limits, and where the token
spend goes: [docs/runtime-architecture.md](./docs/runtime-architecture.md).

Jobs run in parallel, per queue, so ten people uploading at once are processed
together rather than one after another:

| Queue | Env var | Default | What caps it |
| --- | --- | --- | --- |
| `process-document` | `PROCESS_CONCURRENCY` | 4 | Memory — see the page note below |
| ↳ pages within one document | `PAGE_CONCURRENCY` | 4 | Memory — `PROCESS_CONCURRENCY x PAGE_CONCURRENCY` pages are rendered at once, ~18 MP each |
| `scrape-region` | `SCRAPE_CONCURRENCY` | 4 | Provider rate limit — pages are read in batches of `SHEET_BATCH_SIZE` (25), and unambiguous title blocks skip the model entirely |
| `summarize-portion` | `SUMMARIZE_PORTION_CONCURRENCY` | 4 | Sonnet rate limit, and spend |
| `summarize-project` | `SUMMARIZE_PROJECT_CONCURRENCY` | 1 | Rollups are cheap and one per project |

`WORKER_CONCURRENCY` sets all four at once; the per-queue variables override it.
Throughput is `replicas x concurrency`, so three replicas at the defaults process
twelve documents simultaneously.

**Pages inside one document run in parallel too.** Document-level concurrency
does nothing for a single 400-page upload — that was one page at a time, leaving
the CPU idle during every upload and the network idle during every render. Each
page thread opens its own PyMuPDF handle on the downloaded file (a shared
`Document` is not thread-safe). Pages still commit individually, so a crash
leaves the finished ones saved and a retry resumes.

**What keeps this correct.** Most of the pipeline is per-document and parallelises
freely — each job writes its own pages. Three steps are project-wide, though:
combined page numbering, the portion rebuild, and chunk→portion assignment. Two
documents of the same project finishing at the same instant would interleave
those and corrupt the manifest, so they run inside a **Postgres advisory lock
keyed on the project** (`db.project_lock`). Different projects never contend;
same-project jobs serialize for the few seconds those steps take. The lock lives
in Postgres rather than the process because the contenders are separate replicas,
and Postgres releases it automatically if a worker dies — a crash cannot wedge a
project.

Connections are pooled (`DB_POOL_SIZE`, default `2 x WORKER_CONCURRENCY`). Keep
`replicas x DB_POOL_SIZE` under the server's `max_connections` (Postgres defaults
to 100).

**Before raising these numbers**, raise your provider tiers. Concurrency
multiplies the request rate at the embedding provider and Anthropic directly;
on a free tier (Voyage's is ~3 req/min) more workers just convert queueing into
429 retries.

## Common commands

| Command | What it does |
| --- | --- |
| `npm run dev:api` | API dev server with reload (tsx watch) |
| `npm run dev:web` | Web dev server (Vite) |
| `npm run typecheck` | Typecheck all TS workspaces |
| `npm run build` | Build shared, api, and web |
| `npm test` | Run API unit tests (vitest) |
| `npm run prisma:migrate` | Create/apply migrations (dev) |
| `npm run prisma:generate` | Regenerate Prisma client |
| `docker compose up -d` | Start local infra |
| `docker compose down` | Stop local infra (add `-v` to wipe data) |
| `cd workers && python -m pytest tests/ -q` | Python worker unit tests |

Single TS test file: `npx vitest run src/manifest.test.ts` from `apps/api`.

## Local service endpoints

| Service | URL | Credentials |
| --- | --- | --- |
| API | http://localhost:4000 | bearer token from `/auth` |
| API metrics | http://localhost:9464/metrics | — |
| Worker metrics | http://localhost:9465/metrics | — |
| Web | http://localhost:3000 | — |
| Postgres | localhost:5432 (`cdip`) | postgres / postgres |
| Redis | localhost:6379 | — |
| Qdrant | http://localhost:6333 | — |
| MinIO API | http://localhost:9000 (bucket `cdip-local`) | minioadmin / minioadmin |
| MinIO console | http://localhost:9001 | minioadmin / minioadmin |
| Prometheus | http://localhost:9090 | — |
| Grafana | http://localhost:3001 (dashboard "Construction Drawing AI Platform") | admin / admin |

## Authentication & RBAC

All `/projects` routes require a bearer token; media GETs (`.../file`, `.../thumb`,
`.../image`) also accept `?token=` because `<img>`/PDF loads can't send headers —
either way, the API only ever redirects to short-lived presigned storage URLs.

- `POST /auth/register {email, password, firstName, lastName?, company?}` → `{token, user}`
  (passwords scrypt-hashed; sessions live in Redis with a sliding 7-day TTL)
- `POST /auth/login` / `POST /auth/logout` / `GET /auth/me`
- `PATCH /auth/me` (profile) and `POST /auth/password` (change password) back the Account page
- Creating a project makes you its **owner**; owners manage the project and members,
  **members** can view, upload, and chat.
- Member management (owner-only writes):
  `GET/POST /projects/:id/members` (`{email, role}`), `DELETE /projects/:id/members/:userId`.
- Projects created before Phase 5 have no owner and stay accessible to any
  authenticated user.

### Password reset and email

`POST /auth/forgot-password {email}` sends a one-hour, single-use link to
`APP_URL/?reset=<token>`; the reset screen redeems it via
`POST /auth/reset-password {token, password}` and signs the user straight in.
`POST /auth/reset-password/check {token}` reports whether a link is still good,
so an expired one says so before the user types anything.

What the implementation is careful about:

- **No account enumeration.** `forgot-password` always answers `202` with the
  same body, whether or not the address is registered and whether or not the
  mail actually went out.
- **Tokens are stored hashed** (SHA-256), never in the clear — a leaked Redis
  dump yields nothing usable. Redemption is an atomic `GETDEL`, so a link works
  exactly once even under concurrent requests.
- **A reset revokes every existing session** for that account. Resetting exists
  precisely because someone else may be holding one.
- **Per-address throttle**: 3 requests per 15 minutes, so the endpoint can't be
  used to flood someone's inbox.
- The token is **stripped from the address bar** once the reset screen reads it.

Email is optional. With `SMTP_HOST` unset the API logs each message — reset
link included — to the server console, so local development and CI work with no
mail server. Support tickets are persisted first and mailed second: the ticket
row is the record of truth and a mail failure never fails the request. Set
`SUPPORT_EMAIL` to receive them (`Reply-To` is the submitter) — the submitter
also gets an acknowledgement.

Uploads are validated after completion (must start with the `%PDF-` magic bytes,
`.pdf` filename, 2 GiB cap, sanitized name) and pass through a malware-scan hook:
set `MALWARE_SCAN_URL` to a scanner endpoint (`POST {key, url}` →
`{"status": "clean"|"infected"}`, e.g. a small ClamAV REST sidecar); scanner failures
block the file. Unset, scanning is skipped.

## Pipeline logs

The worker logs every processing stage with timestamps and levels
(`LOG_LEVEL=DEBUG` adds per-page detail):

```
10:12:01 INFO [pipeline] [doc 13c35c8d] stage 1/6 download: projects/…/original.pdf
10:12:03 INFO [pipeline] [doc 13c35c8d] stage 2/6 extract: 240 pages (0 already done — resuming)
10:12:41 INFO [pipeline] [doc 13c35c8d] stage 2/6 extract: 25/240 pages (3 OCR)
10:14:55 INFO [pipeline] [doc 13c35c8d] stage 4/6 numbering done
10:15:20 INFO [pipeline] [doc 13c35c8d] stage 5/6 embed done: 512/512 chunks in Qdrant
10:15:20 INFO [pipeline] [doc 13c35c8d] stage 6/6 finalize: completed in 199.2s — {...}
```

Failures log the exact stage (and page number during extraction) with a full
traceback before BullMQ retries, e.g.
`stage 2/6 extract FAILED at page 137/240 (pages 1..136 are saved; a retry resumes here)`.

## Testing the chat and summary flows separately

The two AI flows can be switched off independently on the worker — useful when
a free embedding tier keeps hitting its rate limit:

| Variable | Effect |
| --- | --- |
| `EMBEDDINGS_ENABLED=false` | Skips the embedding provider + Qdrant entirely. Pages, chunks and portions still build; chunks keep a NULL `embeddingId`. Chat retrieval finds nothing until you re-enable. |
| `SUMMARIES_ENABLED=false` | Refuses summary jobs. Processing, region scraping, embedding and **chat still run**. (Summaries never run unasked anyway — see below.) |
| `SUMMARY_USE_BATCH=true` | Bulk page summaries via the active provider's batch API (Anthropic Message Batches / Gemini inline batch jobs) — 50% cheaper on both, recommended for large sets. The wait is bounded by `BATCH_TIMEOUT_SECONDS` (default 1h); a timeout fails the job rather than silently dropping pages. |
| `SHEET_EXTRACTION=rules` | Skips the model sheet-number read (pattern matching on the scraped region only, no API calls). |
| `SHEET_PROVIDER=gemini` | Reads the sheet number with Gemini instead of Claude Haiku (`GEMINI_API_KEY`, `GEMINI_MODEL`). Only the reading changes: both return the same JSON and the same deterministic table maps the prefix to a discipline, so results are directly comparable. Cached answers are keyed per provider, so switching re-reads rather than reusing the other model's. |
| `SHEET_RULES_FIRST=false` | Sends every page to the model. On by default, the rules-first pre-pass resolves a title block that says exactly one thing ("S-003.0", "A17-11 EQUIPMENT PLANS") by pattern and skips the call; it abstains as soon as there is a second candidate, a license/job/permit number, or a reference to another sheet. |
| `SHEET_BATCH_SIZE=25` | Pages per provider request. A 400-page scrape's cost is round trips, not tokens — this is what turns hundreds of calls into a handful. Every entry carries an index the model must echo; anything unanswered is retried in a smaller batch, so a bad response costs accuracy on nothing. |
| `REGION_OCR_DPI=300` | Render DPI for the OCR fallback when the title-block box holds no vector text. |
| `GEMINI_THINKING_BUDGET=0` | Thinking tokens are spent from `max_output_tokens` before the answer is written, so on a thinking model they truncate the summary JSON. Off by default; raise it (or `-1` for automatic) if you want the model to reason first. |
| `TABLE_EXTRACTION_ENABLED=false` | Stops lifting schedules out as whole markdown tables; their cells go back to being chunked as ordinary text blocks. |

## Choosing a model provider per stage

Every stage that calls a model runs on **Claude or Gemini**, switched
independently — they have different economics, so there is no reason they share
a vendor:

| Stage | Switch | Claude model | Gemini model |
| --- | --- | --- | --- |
| Sheet-number read (discipline detection) | `SHEET_PROVIDER` | `CLASSIFIER_MODEL` | `GEMINI_MODEL` |
| Summaries (worker) | `SUMMARY_PROVIDER` | `SUMMARY_MODEL` | `SUMMARY_GEMINI_MODEL` |
| Chat (API) | `CHAT_PROVIDER` | `CHAT_MODEL` | `CHAT_GEMINI_MODEL` |

Embeddings switch separately and differently — see
[Choosing an embedding provider](#choosing-an-embedding-provider).

Each takes `claude` (default) or `gemini`, and needs the matching key —
`ANTHROPIC_API_KEY` or `GEMINI_API_KEY`. An unrecognised value falls back to
`claude` rather than taking the stage offline.

**The provider is a transport detail.** Both are sent the same instructions and
the same untrusted document text, and both replies go through the same strict
parser: `parse_sheet_response` for sheet numbers, `parse_summary_json` for
summaries, the `[chunk:<id>]` citation parser for chat. So switching changes
*who* answers and nothing about what an answer is allowed to claim or cite —
the prefix→discipline table, the FR-13 requirement that every statement carries
a resolvable chunk ID, and the source-verification chain all hold either way.
That is also what makes the two directly comparable on the same project.

The **Generate summary** dialog quotes the model these settings resolve to —
including the batch discount when `SUMMARY_USE_BATCH` is on — so the estimate
follows the provider rather than always quoting Claude.

Two caveats worth knowing before you switch:

- **Sheet-read caches are keyed per provider**, so switching re-reads rather
  than serving the other model's answers.
- **Prompt caching differs.** Anthropic uses explicit cache breakpoints;
  Gemini caches implicitly. The prompt *content* is identical, but the
  `/dashboard` cost figure for Gemini rows applies Anthropic's cache
  multipliers, so its cache portion is an approximation.
- **A model not in the rate table is estimated by family** (`RATES` /
  `FAMILY_RATES` in `apps/api/src/usage.ts`), and the API logs a warning naming
  it. That keeps a new model from being quoted at a frontier model's price, but
  it is a guardrail — add the real published rates for anything you run in
  earnest.

Costs land in the same `usage_events` table under the model that actually
answered, so a switch shows up as a change in the dashboard's spend breakdown
rather than being merged into the previous model's total.

Typical loop: upload with `EMBEDDINGS_ENABLED=false` to test summaries without
burning the embedding quota, then set it back to `true` and run
`POST /projects/:projectId/documents/reindex` — every completed document is
re-queued, already-processed pages are skipped, and only the missing vectors
are created.

## Choosing an embedding provider

The chunks → vectors step is its own switch, `EMBEDDING_PROVIDER`, read by both
the worker (documents) and the API (the chat question):

| Provider | Default model | Dimensions | Key |
| --- | --- | --- | --- |
| `voyage` (default) | `voyage-3` | 1024 fixed (`voyage-3.5`/`-3-large` are configurable) | `VOYAGE_API_KEY` |
| `cohere` | `embed-v4.0` | 256–1536, 1024 here | `COHERE_API_KEY` |
| `gemini` | `gemini-embedding-001` | 768/1536/3072, 1024 here | `GEMINI_API_KEY` |

Override the model with `EMBEDDING_MODEL` and the width with `EMBEDDING_DIM`.
Unlike the chat/summary/sheet switches, **this one is not a restart — it is a
re-index**:

> Two providers embed into different spaces, and so do two models of one
> provider. A cosine distance between spaces is noise, not a worse score, and
> nothing errors: retrieval just quietly returns the wrong chunks. So point
> `QDRANT_COLLECTION` at a new name, restart, and run
> `POST /projects/:projectId/documents/reindex` **with `{"reembed": true}`**.
> The old collection stays where it is until you delete it, which makes the
> switch reversible.

`reembed` is what makes that re-index actually re-embed. The worker only
embeds chunks whose `embeddingId` is NULL, so without the flag the run
re-queues every document, finds nothing to do, and leaves the new collection
empty — with chat answering from nowhere and no error to read. It is opt-in
because it knowingly spends the project's full index cost.

The worker refuses to index when `EMBEDDING_DIM` disagrees with the
collection's width, naming both sides — Qdrant's own rejection names neither.
A width *match* proves nothing about the space (`voyage-3` and `embed-v4.0` are
both 1024), which is why the collection name is the thing to change.

### What the embedding step costs, and what it doesn't pay twice

Four layers, each of which removes work before it becomes a bill:

| Layer | Switch | What it saves |
| --- | --- | --- |
| Revision reuse | always on | A chunk whose text is unchanged from the revision it replaces copies its vector out of Qdrant. |
| In-run dedup | always on | A sheet set repeats its general notes on hundreds of pages; each distinct string is embedded once per call. |
| Redis vector cache | `EMBED_CACHE_ENABLED` (on) | The same dedup across runs — what makes a re-index after a portion rebuild or a chunker change nearly free. Keyed on (provider, model, width, text), ~4 KB per distinct chunk, expiring after `EMBED_CACHE_TTL_SECONDS` (14 days). |
| Async Batch API | `EMBED_USE_BATCH` (off) | 50% of the remaining bill. Gemini is the only provider offering one for embeddings today; for Voyage and Cohere the switch logs once and stays on the synchronous path. |

Request batching is separate and always on: `EMBED_BATCH_SIZE` inputs per call,
capped at each provider's ceiling (voyage 128, cohere 96, gemini 100) and split
again when a batch would exceed the provider's token limit. Round trips, not
tokens, are what a 1000-page project spends its wall clock and its rate limit
on — `EMBED_BATCH_DELAY` spaces them out on a rate-limited free tier.

`EMBED_USE_BATCH` trades latency for money: stage 5/6 waits for the batch
(bounded by `BATCH_TIMEOUT_SECONDS`), so it is worth it for a large upload and
not for a small one — hence `EMBED_BATCH_MIN` (200), below which a run embeds
synchronously regardless. A batch that comes back short or failed is discarded
rather than trusted: its results are matched by position, so a gap would shift
every later vector onto the wrong chunk. Batched runs are recorded under
`<model>-batch` in `usage_events` so the dashboard prices them at the half rate
they were actually billed at.

## After pulling schema changes

`/dashboard` or `/support` returning **503 "prisma client out of date"** (or, on an older
build, `TypeError: Cannot read properties of undefined (reading 'findMany')`) means the
generated Prisma client predates a model the code uses — the delegate is `undefined`.
Regenerate it:

```bash
npm run prisma:migrate     # applies pending migrations AND regenerates the client
# or, if the tables already exist:
npm run prisma:generate
```

Restart the API afterwards. It logs the same warning at startup, naming the missing models,
so you see it before the first request.

## Title-block region: how sheets get categorized

A drawing set prints its sheet number in the same physical place on every sheet, and you
know where that is — so you point at it once instead of making the AI hunt for it.

1. **Draw the box.** Sidebar → *Define region* → pick any page → drag a rectangle over the
   sheet number. It is stored as ratios of the page (0–1), not pixels, so it re-applies to
   every page regardless of size or rotation.
2. **Check it.** *Preview on 5 sheets* scrapes the box on pages spread across the project
   and shows exactly what came back, before a full run.
3. **Scrape.** Saving queues `scrape-region`, which applies the box to every page:
   vector text → word-overlap pass → OCR of the rendered crop for scanned sheets.
4. **Classify.** Only that scraped string goes to Claude Haiku, which reports the sheet
   number; the deterministic prefix table maps it to a discipline (`S-003.0` → Structural).
   Unreadable sheets inherit their neighbour's discipline.
5. **Group.** One category per discipline, with its combined page range and sheet count.

Editing the box bumps its version and re-scans every page. Portions are keyed on
`(projectId, discipline)` and upserted, so their IDs — and any summaries you already
generated — survive the re-scan; a discipline whose page set changed is marked *Out of
date* rather than wiped.

| Endpoint | What it does |
| --- | --- |
| `GET/PUT/DELETE /projects/:projectId/region` | Read, save (bumps version + queues a full scrape), or clear the box |
| `POST /projects/:projectId/region/preview` | Dry run on sample pages → `{previewId}` to poll |
| `POST /projects/:projectId/region/rescrape` | Re-run with the same box (after new uploads or a failure) |
| `GET /projects/:projectId/pages/:combined/region-text` | What the scrape read on one page, and what the classifier made of it |

**A sheet came out in the wrong category?** Ask that last endpoint first — the answer is
almost always a box that clips the number, and the fix is redrawing it slightly larger.

## No summary? Diagnose it

| Endpoint | What it tells you |
| --- | --- |
| `GET /projects/:projectId/summaries/status` | Counts per level (page/section/portion/project), portions, pages with chunks, document statuses, plus a `hint` naming the likely cause |
| `GET /projects/:projectId/portions/:portionId/summarize/estimate` | Model calls, tokens and USD the run will cost — backs the confirmation dialog |
| `POST /projects/:projectId/portions/:portionId/summarize` | Generate ONE discipline's summary (what the button in the sidebar calls) |
| `GET /projects/:projectId/summaries/project/estimate` | Same, for the project rollup |
| `POST /projects/:projectId/summaries/project` | Roll the existing discipline summaries up into the project summary |
| `POST /projects/:projectId/summaries/rebuild` | Admin full re-run: every discipline plus the rollup — no re-upload needed |

Reading the status output:

- `pagesWithChunks: 0` — processing produced no text; check document status and worker logs.
- `pagesWithChunks > 0` but `summaries.page: 0` — usually nobody has pressed a
  discipline's **Generate summary** button yet; summaries are never produced
  automatically. If one was requested and the portion shows `failed`, the worker logs the
  reason: `SUMMARIES_ENABLED=false` or `ANTHROPIC_API_KEY not set on the worker`.
- `summaries.page > 0` but `summaries.project: 0` — the rollup tier failed; rebuild and
  watch the worker log for `rolling up N page summaries`.

The summary panel shows this `hint` in place of "no summary yet" once a run has produced
nothing, with the same **Generate summary** button — so the usual case needs neither curl
nor the worker log. Each category also carries its own status chip
(`No summary / Queued / Summarizing… / Summary ready / Out of date / Failed`), and a
failed run shows its error inline.

The rollup tiers degrade instead of disappearing: if Claude returns unusable JSON for a
section, portion, or project rollup (or the level below cites nothing), the worker merges
the level below deterministically and logs `merging the level below instead`. A page whose
summary fails to parse is retried once. So a project with page summaries always ends up
with a project summary — previously a single bad response on a small project left the DB
with page rows and nothing above them.

## Queue operations (stuck / failed jobs)

Authenticated instance-wide endpoints for inspecting and unsticking BullMQ
without touching Redis by hand:

| Endpoint | What it does |
| --- | --- |
| `GET /queues` | Job counts per queue (waiting/active/failed/delayed/completed) |
| `GET /queues/:name/failed?limit=50` | Recent failed jobs **with their error** — see why, without scraping logs |
| `POST /queues/:name/retry-failed` | Re-queue every failed job (after fixing the cause) |
| `POST /queues/purge-orphaned` | Remove jobs whose document/project no longer exists |
| `DELETE /queues/:name/failed` | Give up on all failed jobs |

`:name` is `process-document`, `scrape-region`, `summarize-portion` or `summarize-project`.

**Jobs looping on `ForeignKeyViolation ... is not present in table "documents"`**
mean the document/project was deleted while its job was still queued. Deleting
a project or document now removes its pending jobs automatically, and the
worker discards jobs for deleted rows instead of retrying — for a backlog from
before that fix, run `POST /queues/purge-orphaned`.

## Deleting a project

`DELETE /projects/:id` (owner-only) removes the project **everywhere**:

1. PostgreSQL — the project row cascades to documents, pages, chunks,
   portions, summaries, and chat history.
2. Object storage — every object under `projects/{id}/` (original PDFs, page
   images, thumbnails, extracted text).
3. Qdrant — all embedding points for the project.
4. Redis — retrieval caches and the summaries cache.

Storage/Qdrant/Redis cleanup stages run after the DB delete and are logged
individually (`[cleanup <id>] …`); a failed stage is logged as an error
without blocking the others.

## Document revisions (FR-4)

Click **New revision** on a completed document (or POST `/documents` with
`replacesDocumentId`) to upload a replacement drawing set. When the new revision
finishes processing, the old one is marked superseded: it leaves the combined viewer,
the page numbering, retrieval (its Qdrant points are deleted), and the summary
rollups, but its rows are kept for history. Chunks whose text is unchanged **reuse
the previous revision's embeddings** (no provider call).

## Demo flow

With infra, API, web, and the Python worker all running:

1. Open http://localhost:3000, register an account, and create a project.
2. Click **Upload PDFs** — files go directly to MinIO/Spaces via presigned multipart
   upload (resumable; the API never touches file bytes).
3. Watch the document status move `uploaded → processing → completed` while the worker
   streams pages (text/PNG/thumb extraction, OCR fallback).
4. Browse the combined set: continuous numbering across PDFs via the virtual page
   manifest, lazy-loaded pages, thumbnail rail, "Go to page".
5. Define the **title-block region**: click *Define region* in the sidebar, pick any page,
   and drag a box over its sheet number. *Preview on 5 sheets* shows what the box scrapes
   before you commit. Saving applies it to every page of every PDF in the project.
6. The categories (Architectural, Structural, …) appear in the sidebar with their page
   range and sheet count; clicking one jumps the viewer and switches the summary panel to
   it. Editing the region re-scans and rebuilds the categories.
7. With `ANTHROPIC_API_KEY` + the embedding provider's key set (API + worker), chat in the middle
   pane. Clicking a citation, source chip, or summary item jumps the viewer to the page
   **and highlights the cited bounding box** (FR-19).
8. Press **Generate summary** on a category. A dialog shows what the run will cost
   first — model calls, tokens, and USD, computed from the stored chunk token counts —
   so the spend is a deliberate second click. Nothing is summarized until you ask, so
   you only pay for the disciplines you care about. (Page → section → portion,
   bottom-up; the section tier is skipped for disciplines of 10 sheets or fewer, where
   it would only restate the page summaries.) Once at least one is ready, **Generate project summary**
   rolls them up. `SUMMARY_USE_BATCH=true` routes bulk page summaries through the
   Anthropic Message Batches API.
9. Grafana (http://localhost:3001) shows API latency, queue depth, worker job
   durations, Qdrant search latency, and the retrieval-cache hit ratio.

### Account pages

| Page | What it shows |
| --- | --- |
| **Dashboard** | Headline totals (projects, drawings, tokens, chat messages), a 14-day token-usage trend, document-status and discipline distributions, spend by stage, and a per-project usage table. All from one `GET /dashboard` call. |
| **Projects** | Card grid with first-page thumbnails, search, status filter, sort, and per-card open/rename/delete. |
| **Support** | Ticket form (`POST /support`); submitted tickets are stored in `support_tickets` and listed back. |
| **Account** | Profile (first/last/company/email) via `PATCH /auth/me` and password change via `POST /auth/password`. Reached from the user card at the bottom of the sidebar. |

Token accounting powers the dashboard's spend figures: every model call — chat, summaries
(including the Batch path), Haiku sheet-number reads, and embeddings — writes a row to
`usage_events` with its input/output/cache token counts. The dollar figure is an estimate from
the published per-million rates in `apps/api/src/usage.ts`; deleting a project keeps its usage
history (the FK is `SET NULL`).

### Viewer & layout controls

| Control | What it does |
| --- | --- |
| `−` / `%` / `+` in the viewer toolbar | Zoom out / reset to 100% / zoom in (40 %–400 %) |
| **Fit** | Scales the page to the current viewer width |
| Ctrl/⌘ + scroll wheel | Zooms over the page area |
| Drag lines between panes | Resize the sidebar and the chat pane; the viewer takes the rest. Double-click a line to collapse that pane to its minimum |
| First open | The page is fitted to the viewer width automatically; after that your own zoom is remembered |
| **Hide chat** / **Show chat** (project header) | Removes the chat pane so the viewer spans the window |

Zoom, both pane widths, and the chat-hidden state persist in `localStorage` per browser.

## Caching

- **Prompt caching**: the chat system prompt + retrieved-chunk block and the
  summarizer's shared system prompt carry `cache_control` breakpoints.
- **Redis**: chat retrievals (`retrieval:*`, 1h TTL) and per-project summary lists
  (`cache:summaries:*`, invalidated by the worker after each summarize run).
- **Embeddings**: unchanged chunks across document revisions reuse stored Qdrant
  vectors; identical chunk text is embedded once per run and cached in Redis
  (`cache:embedding:*`, keyed per provider/model/width) across runs.

## Monitoring

The API exposes OpenTelemetry metrics via a Prometheus endpoint on `:9464`
(HTTP latency by route, Qdrant search latency, chat latency, retrieval-cache
hits, BullMQ queue depths); the worker exposes job durations/counts on `:9465`.
`docker compose up` starts Prometheus (scraping both through
`host.docker.internal`) and Grafana with a provisioned dashboard
(`monitoring/grafana/dashboards/cdip.json`). In production, point
`monitoring/prometheus.yml` targets at the deployed api/worker services.

## Deploying to DigitalOcean

The stack is designed for **App Platform** (simplest) or **DOKS** (Kubernetes, for
independent worker autoscaling). Either way you need these managed services:

| Dependency | DigitalOcean product |
| --- | --- |
| PostgreSQL | Managed Database (Postgres 16) |
| Redis | Managed Database (Redis/Valkey) |
| Qdrant | DOKS pod / Droplet running `qdrant/qdrant` (or Qdrant Cloud) |
| Object storage | Spaces bucket (e.g. region `blr1`) with CDN enabled for thumbnails |

### Spaces configuration

Create a bucket and an access key, then set (on both api and worker):

```
SPACES_ENDPOINT=https://blr1.digitaloceanspaces.com   # REGION endpoint — no bucket name in it
SPACES_BUCKET=<bucket name>
SPACES_REGION=blr1
SPACES_KEY=<access key>
SPACES_SECRET=<secret>
```

⚠️ `SPACES_ENDPOINT` must be the region endpoint, **not** the bucket URL the DO
console shows. With path-style addressing the SDK appends the bucket itself, so
`https://<bucket>.blr1.digitaloceanspaces.com` addresses `<bucket>.…/<bucket>/<key>`:
Spaces takes the bucket from the host and the rest as the key. Symptoms are a
doubled `<bucket>/` prefix on every uploaded object and project deletion failing
with `storage cleanup FAILED NoSuchKey` (the *list* call arrives as a GetObject).

The API detects this at startup, strips the bucket from the host and logs a
warning naming the correct value — but fix `.env` anyway, and note that objects
uploaded under the doubled prefix are orphaned. The quickest cleanup is to
delete those projects and re-upload; otherwise move the keys in the bucket.

The code uses the AWS S3 SDK/boto3 with an endpoint override, so no code changes are
needed between MinIO and Spaces. Add a CORS rule on the bucket allowing `PUT` from
your web origin (the browser uploads parts directly with presigned URLs) and `GET`
for the viewer.

### App Platform

Create one app with three components from this repo:

1. **api** (service) — build: `npm install && npm run build`; run:
   `npm run prisma:generate --workspace @cdip/api && node apps/api/dist/index.js`;
   HTTP port 4000. Run migrations as a pre-deploy job:
   `npx prisma migrate deploy --schema apps/api/prisma/schema.prisma`.
2. **worker** (worker) — Dockerfile `workers/Dockerfile`. Scale instance count on
   queue depth (the `bullmq_queue_jobs` metric); API and workers scale independently.
3. **web** (static site) — build: `npm install && npm run build`; output
   `apps/web/dist`; set `VITE_API_URL` to the api component's public URL.

Environment (api + worker): `DATABASE_URL`, `REDIS_URL`, `QDRANT_URL`, the `SPACES_*`
vars above, `ANTHROPIC_API_KEY`, `EMBEDDING_PROVIDER` + its key
(`VOYAGE_API_KEY` / `COHERE_API_KEY` / `GEMINI_API_KEY`), and optionally
`MALWARE_SCAN_URL`, `SUMMARY_USE_BATCH`, `EMBED_USE_BATCH`, `CHAT_MODEL`,
`SUMMARY_MODEL`. Use App
Platform encrypted env vars for all secrets; TLS is terminated by the platform and
managed databases encrypt at rest.

### DOKS

Deploy the api and worker as Deployments (images built from `apps/api` and
`workers/`), Qdrant as a StatefulSet with a volume, and the web build behind the
static-site CDN of your choice. Give the worker a HorizontalPodAutoscaler driven by
the queue-depth metric (Prometheus adapter on `bullmq_queue_jobs{state="waiting"}`).
Run `prisma migrate deploy` as a Job per release. Scrape `api:9464` and
`worker:9465` with your Prometheus and import
`monitoring/grafana/dashboards/cdip.json`.

## Status

Phases 1–5 complete. Phase 5 adds: FR-19 click-to-highlight (bbox overlay in the
combined viewer, wired from chat citations and summary items), auth + project RBAC
(owner/member), presigned-URL-only media with upload validation and a malware-scan
hook, input sanitization, document revisions with supersede semantics and embedding
reuse, Anthropic prompt caching, Redis caching of summaries and repeat retrievals,
OpenTelemetry metrics with a Prometheus/Grafana stack, and this deployment guide.
Unit tests: manifest, citations, sanitization, RBAC/passwords (`apps/api/src/*.test.ts`),
classifier, chunker, summarizer, embedding reuse (`workers/tests/`).
