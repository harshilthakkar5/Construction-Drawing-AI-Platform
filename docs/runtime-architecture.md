# Runtime architecture: queues, polling, parallelism, cost

How work actually executes at runtime — what runs where, how the browser learns
that it finished, how much of it runs at once, and what it costs. Written as the
reference for operating and scaling the system.

Companion documents: [CLAUDE.md](../CLAUDE.md) for the architecture as a whole,
[region-based-classification.md](./region-based-classification.md) for the
discipline-detection design.

---

## 1. The core pattern: queue and poll, no sockets

There are **no WebSockets, no Socket.IO, and no server-sent events** anywhere in
the codebase. Every long-running operation follows one shape:

```
Browser  ──POST──►  API  ──enqueue──►  Redis (BullMQ)  ──►  Python worker
   │                 │                                          │
   │   202 {jobId}   │                                    writes Postgres
   ◄─────────────────┘                                          │
   │                                                            │
   └──GET every 2–3s ──► API ──► Postgres ◄─────────────────────┘
```

The API never waits for the work. It records an intent, returns `202`, and the
browser discovers completion by re-reading state.

**Progress lives in database columns, not in a connection:**

| Column | Reports |
| --- | --- |
| `documents.status` | `uploaded → processing → completed / failed` (FR-9) |
| `sheet_regions.scrapeStatus` + `scrapedPages` / `totalPages` / `notFoundPages` | region scrape progress |
| `portions.summaryStatus` | `none / queued / running / ready / failed / stale` |

### Why polling rather than sockets

The work outlives the request by design. A 1000-page scrape runs for many
minutes, survives worker restarts via BullMQ retries, and must resume where it
died. A socket would add a second source of truth for state Postgres already
holds, plus reconnection handling, plus a sticky-session requirement once the API
runs more than one instance. Polling an indexed table every 3 seconds costs
almost nothing and is stateless.

Sockets would only pay off for per-page live progress rather than a counter.

### Every poll stops on its own

Each `refetchInterval` is a **predicate**, re-evaluated after every response, and
returns `false` when there is nothing left to watch. An idle project generates
zero background requests.

| Component | Interval | Stops when |
| --- | --- | --- |
| `DocumentsPanel` | 2s | no document is `uploaded`/`processing` |
| `RegionBanner` | 2s | `scrapeStatus` leaves `pending`/`running` |
| `RegionSelector` (preview) | 1.5s | the preview result appears in Redis |
| `CombinedViewer` (manifest) | 3s | every manifest entry has an image |
| `PortionsPanel` | 3s | no portion is `queued`/`running` |
| `SummaryPanel` | 3s | the summary for the current level exists |
| `DashboardPage` | 30s | never (ambient page) |

`SummaryPanel` returning `false` when idle is deliberate and recent: under the
old auto-summary behaviour it fell back to a 30s poll forever. Now nothing can
appear unless a user presses a button, so waiting for it is wasted work.

### The one exception

`POST /projects/:id/chat` is **synchronous**. `apps/api/src/routes/chat.ts`
returns only after the Voyage embedding, the Qdrant search, and the complete
Sonnet answer — typically 5–20 seconds with the connection held open, and the
answer arrives all at once.

This works, and the Redis retrieval cache covers repeat questions, but it is the
only request that can hit a proxy or load-balancer idle timeout, and a streamed
answer would feel far faster than a 15-second blank wait. Streaming it means
`client.messages.stream()` plus SSE from Express, and it complicates citations:
`citations.ts` parses `[chunk:<uuid>]` markers and renumbers them *after* the
full text arrives, so streaming requires either rendering raw markers and
swapping them, or buffering until each marker completes. Deferred deliberately.

---

## 2. The four queues

| Queue | Produced by | Runs | Concurrency default |
| --- | --- | --- | --- |
| `process-document` | upload completion, reprocess, reindex | `processing.process_document` | 4 |
| `scrape-region` | saving/editing the region, a new upload into a project that has one | `scrape.run` (also `scrape.preview` under job name `preview`) | 4 |
| `summarize-portion` | the per-discipline button | `summarize.run_portion` | 4 |
| `summarize-project` | the project button (`summarize`), the admin rebuild (`rebuild`) | `summarize.run_project` / `summarize.run` | 1 |

Queue names and payload shapes are duplicated in `packages/shared/src/index.ts`
and `workers/src/contracts.py` — change both.

**Job names carry meaning.** `scrape-region` distinguishes a real scrape from a
`preview` dry run; `summarize-project` distinguishes `summarize` (roll up what
exists) from `rebuild` (re-run every level). The worker branches on `job.name`.

### What each does

**`process-document`** streams the PDF one page at a time: extract text, render
PNG + thumbnail, OCR when there is no text layer, chunk with bboxes. Commits per
page, so a crash at page 700 keeps 1–699 and a retry resumes there. Then
supersedes any previous revision, recomputes combined numbering, and embeds new
chunks. On success it queues a `scrape-region` job for the new document — and
nothing else. **It never queues a summary.**

**`scrape-region`** applies the project's one user-drawn box to every page whose
`regionVersion` is stale, reads the sheet number out of each scraped string via
Haiku, rebuilds the portions, and flags summaries whose page set changed as
`stale`.

**`summarize-portion`** summarizes one discipline bottom-up. **`summarize-project`**
rolls up the discipline summaries that already exist.

### Retry policy

Set by the producer in `apps/api/src/queues.ts`:

- `process-document`: 5 attempts, exponential backoff from 5s. Safe because jobs
  are idempotent and resume.
- `scrape-region`: 3 attempts, same backoff. Also resumes — pages already at the
  current `regionVersion` are skipped.
- `summarize-portion` / `summarize-project`: **1 attempt.** Summaries cost real
  money; a failed one is surfaced to the user as `failed` with the error, and
  the button becomes "Try again". Never retried behind their back.

---

## 3. Parallelism

### Concurrency per queue

BullMQ defaults to `concurrency: 1`, which meant one document processed at a time
*globally* — ten simultaneous uploads queued behind each other. Each queue is now
tunable, because each is capped by a different resource:

| Queue | Env var | Default | Capped by |
| --- | --- | --- | --- |
| `process-document` | `PROCESS_CONCURRENCY` | 4 | **Memory** — ~250–400 MB per concurrent job |
| `scrape-region` | `SCRAPE_CONCURRENCY` | 4 | **Haiku rate limit** — one call per page |
| `summarize-portion` | `SUMMARIZE_PORTION_CONCURRENCY` | 4 | **Sonnet rate limit** and spend |
| `summarize-project` | `SUMMARIZE_PROJECT_CONCURRENCY` | 1 | Rollups are cheap and one per project |

`WORKER_CONCURRENCY` sets all four; per-queue variables override it. Invalid or
zero values clamp to 1 — a typo must never leave a queue with no consumers.

The handlers hand their synchronous body to `asyncio.to_thread`, so these
genuinely overlap: PyMuPDF releases the GIL while rendering, and everything else
is network I/O.

### Horizontal scaling

```bash
docker compose --profile worker up -d --scale worker=3
```

The worker service sits behind a compose profile, so `docker compose up -d` still
starts infrastructure only and does not fight a worker running from a venv.

**Throughput is `replicas × concurrency`** — three replicas at the defaults
process twelve documents at once.

### The project lock — what makes concurrency safe

Most of the pipeline is per-document and parallelises freely; each job writes its
own pages. **Three steps are project-wide:**

1. `recompute_combined_numbering` — renumbers every page in the project
2. the portion rebuild (`upsert_portions`)
3. `assign_chunk_portions`

Two documents of the same project finishing in the same second would interleave
those and corrupt the combined-page manifest — a correctness-critical path, and a
corrupted one silently breaks every citation.

They run inside `db.project_lock(project_id)`: a **Postgres advisory lock** keyed
on `sha256(projectId)[:8]` as a signed 64-bit int.

- **In Postgres, not in-process**, because the contenders are separate replicas.
- **Postgres frees it if the worker dies**, so a crash cannot wedge a project.
- **The key is derived in Python, not via `hashtext()`**, so every process
  computes the same number and it is testable without a database.
- Different projects never contend. Same-project jobs serialize only for the few
  seconds those steps take.

### Connection pooling

Every `db` helper borrows a connection for a single statement, and the scrape
loop commits per page — so this previously opened and TLS-negotiated a **fresh
connection per page**. Bad at concurrency 1; fatal at concurrency 4 across
replicas, since Postgres defaults to `max_connections = 100`.

`db.connect()` now borrows from a `psycopg_pool` sized by `DB_POOL_SIZE`
(default `2 × WORKER_CONCURRENCY`). Call sites are unchanged. If `psycopg_pool`
is missing the worker still runs, unpooled, and logs why.

**Keep `replicas × DB_POOL_SIZE` under the server's `max_connections`.**

### Before raising any of these

Raise provider tiers first. Concurrency multiplies the Voyage and Anthropic
request rate directly. On a free Voyage tier (~3 req/min) more workers simply
convert queueing into 429 retries.

---

## 4. Capacity

**Not load-tested.** These are reasoned from the architecture; treat them as
starting points for measurement, not measurements.

| Layer | Rough limit | Reasoning |
| --- | --- | --- |
| Browsing / viewing | ~200–500 per API instance | Idle projects poll nothing; page images are 302s to presigned Spaces URLs, so the CDN serves the bytes |
| Chat | ~10–30 concurrent, then the Anthropic org rate limit | Synchronous but I/O-bound `await`; the provider limit bites before the event loop |
| Document processing | `replicas × PROCESS_CONCURRENCY` | 12 at the suggested production shape |
| Postgres | thousands of these queries | all indexed lookups |
| Qdrant | single node is fine here | payload-partitioned by project |

**10–20 people using it normally** — browsing, chatting, occasionally starting a
job — is comfortable on one API and one worker at the defaults.

### What breaks first, in order

1. **Provider rate limits**, not infrastructure. This is now the real ceiling.
2. **Cost**, well before capacity. At ~30K embedding tokens per dense sheet, ten
   users each loading a 500-sheet set is ~150M Voyage tokens. The system survives
   it; the bill is the constraint.
3. **No API rate limiting.** Nothing stops one account from queuing fifty
   scrapes. Fine for a trusted team, not for open signup.
4. **Chat's synchronous request** at high concurrency.

### Scaling ladder

- **1–20 users** — current defaults, one API, one worker.
- **20–100** — 2–3 API instances behind a load balancer (already safe: sessions
  live in Redis, so no sticky sessions), 2–4 worker replicas, paid provider
  tiers, per-account rate limiting.
- **100+** — separate worker pools per queue so a 1000-page scrape cannot starve
  a summary job, a Postgres read replica for the dashboard, and a real load test.

---

## 5. Where the money goes

Four things write to `usage_events`: `chat`, `summary`, `classification`,
`embedding`. The project card on the Projects page shows the **sum of all four**,
which is why a project can show 29K tokens after a single one-page upload.

### Observed, from real runs

| Stage | Model | Tokens | Notes |
| --- | --- | --- | --- |
| Region classification | Haiku | **286 in / 32 out per page** | Only the scraped box text plus a cached instruction block |
| Embedding | voyage-3 | **~30K per dense sheet** | The whole page's text, chunked. Dominates everything |
| Page summary | Sonnet | ~3.9K in / ~1.35K out | The page's chunks |
| Section rollup | Sonnet | ~1.7K in / ~1.35K out | Reads the level below |
| Portion rollup | Sonnet | ~1.7K in / ~1.35K out | Reads the level below |

Each tier's input is roughly the previous tier's *output* — visible directly in
the numbers.

**Embedding is the dominant cost by a wide margin**, and it runs at upload time,
before any region or summary work. A 1000-sheet set is roughly 30M Voyage tokens.

### Summaries are user-approved

Nothing is summarized automatically. Every enqueue site sits behind a user
action, and `run_portion` additionally **refuses** unless the portion is already
`queued`/`running` — a state only `POST /portions/:id/summarize` sets. A job left
in Redis by an older build, a replay, or a hand-rolled enqueue is discarded
rather than quietly spending money.

### The tier structure, and when the section tier is skipped

Bottom-up only: page → section → portion → project. Sections exist to **bound the
portion rollup's input** — 40 pages become 4 section summaries so the portion
reads 4 compact summaries instead of 40.

At or below `SECTION_SIZE` (10) pages there is exactly one group, so the section
call only restates the page summaries before the portion rollup restates them
again. `summarize.needs_section_tier` skips it there. A one-page discipline costs
**one rollup call, not three** — measured at ~3.4K in / 2.7K out saved.

### Cost confirmation

Both summary buttons open a dialog first, backed by
`GET /portions/:portionId/summarize/estimate` and
`GET /summaries/project/estimate`.

- **Call counts are exact** — `summaryEstimate.ts` mirrors `run_portion`
  including the section skip, and tests pin the boundary.
- **Page-tier input is real** — summed from the stored `chunks.tokenCount`.
- **Reused page summaries are shown as free**, matching what the worker does.
- **Only the answer length is estimated** (`TYPICAL_OUTPUT_TOKENS = 1350`,
  calibrated from observed runs). The dialog says so.

Two known imprecisions: a page whose cited chunks were replaced by reprocessing
will be re-summarized without the estimate seeing it, and prompt-cache savings
are not modelled (so the quote is slightly pessimistic).

### A truncation bug worth remembering

`max_tokens` was 1000. A dense sheet filled it, the JSON was cut off mid-object,
the parse failed, and the retry re-sent the same prompt into the same cap — 27K
tokens for no summary at all. The tell is `outputTokens` exactly equal to the
cap. Now `SUMMARY_MAX_TOKENS` (default 2000), and a truncated response retries
with double the room and a shorter target rather than repeating itself.

---

## 6. Operations

### Diagnosing

| Question | Where to look |
| --- | --- |
| Is a job stuck? | `GET /queues` (counts per queue), `GET /queues/:name/failed?limit=50` (**with the error**) |
| Why did this sheet get that discipline? | `GET /projects/:id/pages/:combined/region-text` — the scraped string, the method (`vector`/`words`/`ocr`), and what the classifier made of it |
| Why is there no summary? | `GET /projects/:id/summaries/status` — counts per level plus a `hint` |
| What did this project spend? | Dashboard "spend by stage", or the SQL below |
| Is a scrape progressing? | `sheet_regions.scrapedPages` / `totalPages`, surfaced in the sidebar |

```sql
-- Spend by stage for one project
SELECT kind, SUM("inputTokens" + "outputTokens") AS tokens, COUNT(*) AS calls
FROM usage_events WHERE "projectId" = '…' GROUP BY kind;
```

### Recovery

| Situation | Fix |
| --- | --- |
| Job exhausted retries / worker died mid-run | `POST /documents/:id/reprocess` — resumes, already-processed pages are skipped |
| Chunks exist with no vectors (ran with `EMBEDDINGS_ENABLED=false`) | `POST /documents/reindex` |
| Sheets came back empty | Redraw the box larger, save — bumps the version and re-scans only stale pages |
| Jobs looping on foreign-key violations | `POST /queues/purge-orphaned` |
| Summary failed | The portion shows `failed` with the error; the button becomes "Try again" |

### Deployment shape

- **API**: stateless. Sessions live in Redis, so instances scale horizontally
  with no sticky sessions.
- **Workers**: scale independently on queue depth. Jobs are idempotent and
  commit incrementally, so a replica dying mid-document is resumed, not
  restarted.
- **Never** put the API and workers in one process — the whole design assumes
  file bytes and model calls stay out of the request path.

### Monitoring

Prometheus + Grafana, both in compose. API metrics on `:9464` (HTTP, Qdrant and
chat latency, queue depths), worker on `:9465` (job durations). Queue depth is
the signal to scale workers; job duration is the signal that provider limits are
throttling you.

---

## 7. Deferred, with reasons

| Item | Why it was deferred |
| --- | --- |
| Streaming chat responses | Works today; complicates citation renumbering, which parses complete markers after the full answer |
| Per-account API rate limiting | Needed before open signup, not for a trusted team |
| Batch API for sheet-number reads | One Haiku call per page is sequential; worth doing if a 1000-sheet scrape drags. Measure first |
| Separate worker pools per queue | Only matters past ~100 users, when a long scrape could starve summaries |
| Load testing | Nothing here is measured. Cheapest useful test: three 500-page uploads at once, watching `GET /queues` depth and the Grafana job-duration panel |
