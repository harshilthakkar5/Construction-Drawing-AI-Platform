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
| `npm run prisma:migrate` | Create/apply migrations (dev) |
| `npm run prisma:generate` | Regenerate Prisma client |
| `docker compose up -d` | Start local infra |
| `docker compose down` | Stop local infra (add `-v` to wipe data) |

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

## Status

Scaffold and wiring only — no product features yet. The API exposes `/health`
(checks Postgres, Redis, Qdrant), the web app renders the three-pane layout shell,
the `process-document` BullMQ queue is wired between the API (producer) and the
Python worker (consumer), and the initial Prisma migration creates the full schema
from CLAUDE.md.
