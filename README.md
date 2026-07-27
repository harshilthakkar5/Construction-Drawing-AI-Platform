# Construction Drawing AI Platform

Manages construction projects, ingests very large sets of construction drawing PDFs
(100 MB – 1 GB+, 1000+ pages), and provides AI-powered hierarchical summaries plus a
project-scoped RAG chat assistant with click-to-verify citations — every AI statement
traces to the exact PDF document, page, and bounding box. See
[CLAUDE.md](./CLAUDE.md) for the full architecture.

## Repository layout

```
apps/web            React + TypeScript frontend (Vite, Tailwind, TanStack Query, Zustand)
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
python src/worker.py       # consumes process-document + summarize-project queues
```

Or containerized: `docker build -t cdip-worker workers/`.

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

- `POST /auth/register {email, name, password}` → `{token, user}` (passwords scrypt-hashed;
  sessions live in Redis with a sliding 7-day TTL)
- `POST /auth/login` / `POST /auth/logout` / `GET /auth/me`
- Creating a project makes you its **owner**; owners manage the project and members,
  **members** can view, upload, and chat.
- Member management (owner-only writes):
  `GET/POST /projects/:id/members` (`{email, role}`), `DELETE /projects/:id/members/:userId`.
- Projects created before Phase 5 have no owner and stay accessible to any
  authenticated user.

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
10:14:55 INFO [pipeline] [doc 13c35c8d] stage 4/6 portions done: 5 portions
10:15:20 INFO [pipeline] [doc 13c35c8d] stage 5/6 embed done: 512/512 chunks in Qdrant
10:15:20 INFO [pipeline] [doc 13c35c8d] stage 6/6 finalize: completed in 199.2s — {...}
```

Failures log the exact stage (and page number during extraction) with a full
traceback before BullMQ retries, e.g.
`stage 2/6 extract FAILED at page 137/240 (pages 1..136 are saved; a retry resumes here)`.

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

`:name` is `process-document` or `summarize-project`.

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
the previous revision's embeddings** (no Voyage call).

## Demo flow

With infra, API, web, and the Python worker all running:

1. Open http://localhost:3000, register an account, and create a project.
2. Click **Upload PDFs** — files go directly to MinIO/Spaces via presigned multipart
   upload (resumable; the API never touches file bytes).
3. Watch the document status move `uploaded → processing → completed` while the worker
   streams pages (text/PNG/thumb extraction, OCR fallback).
4. Browse the combined set: continuous numbering across PDFs via the virtual page
   manifest, lazy-loaded pages, thumbnail rail, "Go to page".
5. Detected portions (Architectural, Structural, …) appear in the sidebar; clicking one
   jumps the viewer and filters the summary panel.
6. With `ANTHROPIC_API_KEY` + `VOYAGE_API_KEY` set (API + worker), chat in the middle
   pane. Clicking a citation, source chip, or summary item jumps the viewer to the page
   **and highlights the cited bounding box** (FR-19).
7. Bottom-up summaries (page → section → portion → project) build after processing;
   `SUMMARY_USE_BATCH=true` routes bulk page summaries through the Anthropic Message
   Batches API.
8. Grafana (http://localhost:3001) shows API latency, queue depth, worker job
   durations, Qdrant search latency, and the retrieval-cache hit ratio.

## Caching

- **Prompt caching**: the chat system prompt + retrieved-chunk block and the
  summarizer's shared system prompt carry `cache_control` breakpoints.
- **Redis**: chat retrievals (`retrieval:*`, 1h TTL) and per-project summary lists
  (`cache:summaries:*`, invalidated by the worker after each summarize run).
- **Embeddings**: unchanged chunks across document revisions reuse stored Qdrant
  vectors.

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

⚠️ `SPACES_ENDPOINT` must be the region endpoint, **not** the bucket URL
(`https://<bucket>.blr1.digitaloceanspaces.com` breaks path-style addressing —
requests would target `<bucket>.…/<bucket>/…` and 404).

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
vars above, `ANTHROPIC_API_KEY`, `VOYAGE_API_KEY`, and optionally
`MALWARE_SCAN_URL`, `SUMMARY_USE_BATCH`, `CHAT_MODEL`, `SUMMARY_MODEL`. Use App
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
