# Construction Drawing AI Platform

Manages construction projects, ingests very large sets of construction drawing PDFs
(100 MB – 1 GB+, 1000+ pages), and provides AI-powered hierarchical summaries plus a
project-scoped RAG chat assistant with click-to-verify citations. See
[CLAUDE.md](./CLAUDE.md) for the full architecture.

## Repository layout

```
apps/web          React + TypeScript frontend (Vite, Tailwind, TanStack Query, Zustand)
apps/api          Express + TypeScript REST API (Prisma, Zod, BullMQ producer)
workers           Python processing workers (PyMuPDF, pdfplumber, PaddleOCR, OpenCV; BullMQ consumer)
packages/shared   Shared TypeScript types (queue names, job payloads, domain types)
docker-compose.yml  Local Postgres, Redis, Qdrant, MinIO (stand-in for DigitalOcean Spaces)
```

## Prerequisites

- Node.js ≥ 22, npm
- Docker + Docker Compose
- Python 3.11+ (workers only)

## Getting started

```bash
# 1. Environment
cp .env.example .env
cp .env.example apps/api/.env   # Prisma reads DATABASE_URL from apps/api/.env

# 2. Infrastructure (Postgres, Redis, Qdrant, MinIO + bucket init)
docker compose up -d

# 3. Node dependencies (also builds packages/shared)
npm install

# 4. Database migration + Prisma client
npm run prisma:migrate

# 5. Run the apps (separate terminals)
npm run dev:api    # http://localhost:4000 (health check: /health)
npm run dev:web    # http://localhost:3000
```

### Python workers

```bash
cd workers
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python src/worker.py       # consumes the process-document queue from Redis
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

To run a single test file: `npx vitest run src/manifest.test.ts` from `apps/api`.

## Local service endpoints

| Service | URL | Credentials |
| --- | --- | --- |
| API | http://localhost:4000 | — |
| Web | http://localhost:3000 | — |
| Postgres | localhost:5432 (`cdip`) | postgres / postgres |
| Redis | localhost:6379 | — |
| Qdrant | http://localhost:6333 | — |
| MinIO API | http://localhost:9000 (bucket `cdip-local`) | minioadmin / minioadmin |
| MinIO console | http://localhost:9001 | minioadmin / minioadmin |

## Demo flow (Phase 1)

With infra, API, web, and the Python worker all running:

1. Open http://localhost:3000 and create a project.
2. Open the project and click **Upload PDFs** — files go directly to MinIO/Spaces
   via presigned multipart upload (resumable; the API never touches file bytes).
3. Watch the document status move `uploaded → processing → completed` in the left
   panel while the worker streams pages (text extraction, page PNG, thumbnail,
   OCR only for pages without a text layer).
4. Browse the combined set on the right: continuous page numbering across all
   PDFs via the virtual page manifest, lazy-loaded react-pdf pages, thumbnail
   rail, and "Go to page" jump.
5. Once processing finishes, detected portions (Architectural, Structural, …)
   appear in the sidebar — classified from title-block sheet numbers, with a
   Claude Haiku fallback for ambiguous pages when `ANTHROPIC_API_KEY` is set.
   Clicking a portion jumps the viewer to its first page.

## Status

Phases 1–2 complete: project CRUD (FR-1..3), direct-to-storage resumable
multipart upload (FR-5), virtual combined page manifest (FR-6), per-page
extraction with OCR fallback (FR-7/8), visible processing status with retry +
partial-result preservation (FR-9), the lazy combined viewer (FR-17 right pane,
FR-20), and portion detection with sidebar click-to-jump (FR-15/16) — rule-based
sheet-prefix classification plus Claude Haiku strict-JSON fallback (Redis-cached)
for ambiguous pages. Manifest numbering is unit-tested
(`apps/api/src/manifest.test.ts`), the classifier in `workers/tests/`.
Summaries, chunking/embeddings, and chat are not built yet.
