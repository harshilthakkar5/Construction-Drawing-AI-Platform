# Environment variables

Every variable the code reads, with its default and what it does. Built by scanning
`apps/api/src`, `apps/web/src`, `workers/src` and the `deploy/` compose files — if a
variable is not here, nothing reads it. `.env.example` carries the long explanations
for most of these; this file is the index.

**Used by:** API = Express server (`apps/api`), Worker = Python worker (`workers`),
Web = browser bundle (`apps/web`), Deploy = docker compose files only.
"—" as a default means unset / empty.

## 1. Core infrastructure

| Variable | Default | Used by | What it does |
|---|---|---|---|
| `DATABASE_URL` | `postgresql://postgres:postgres@localhost:5432/cdip` | API, Worker | Postgres connection. Required by the API. |
| `REDIS_URL` | `redis://localhost:6379` | API, Worker | Queues, sessions, caches, rate-limit counters. |
| `QDRANT_URL` | `http://localhost:6333` | API, Worker | Vector database. |
| `QDRANT_COLLECTION` | `chunks` | API, Worker | Collection name. Change it (and re-index) when you switch embedding provider or width. |
| `PORT` | `4000` | API | HTTP port. |
| `NODE_ENV` | `development` | API | `development` / `test` / `production`. |
| `APP_URL` | `http://localhost:3000` | API | Public web URL; password-reset links are built from it. |
| `VITE_API_URL` | `http://localhost:4000` | Web | Where the browser calls the API. Baked in at build time. |
| `LOG_LEVEL` | `INFO` | Worker | Worker log verbosity (`DEBUG`, `INFO`, `WARNING`…). |
| `API_CLUSTER_WORKERS` | `1` | API | HTTP processes on one machine; `auto` = one per core. |
| `TRUST_PROXY_HOPS` | `1` | API | Proxy hops trusted for the client IP (rate limiting by IP). |
| `OTEL_METRICS_PORT` | `9464` | API | Prometheus metrics port. |
| `WORKER_METRICS_PORT` | `9465` | Worker | Prometheus metrics port. |
| `USAGE_TRACKING` | `on` | API | `off` stops recording token usage (the benchmarks use this). |

## 2. Object storage

| Variable | Default | Used by | What it does |
|---|---|---|---|
| `STORAGE_BACKEND` | auto | API, Worker | `local` (MinIO on this machine) or `spaces` (DigitalOcean). Unset: `spaces` if any `SPACES_*` credential is set, else `local`. Switching moves no files. |
| `LOCAL_S3_ENDPOINT` | `http://localhost:9000` | API, Worker | MinIO address the servers use. |
| `LOCAL_S3_PUBLIC_ENDPOINT` | — | API | MinIO address the BROWSER uses (e.g. `http://<server-ip>:9000`). Needed when users are on other machines, or uploads fail with SignatureDoesNotMatch. |
| `LOCAL_S3_BUCKET` | `cdip-local` | API, Worker | MinIO bucket. |
| `LOCAL_S3_REGION` | `us-east-1` | API, Worker | MinIO region. |
| `LOCAL_S3_KEY` / `LOCAL_S3_SECRET` | `minioadmin` | API, Worker | MinIO credentials. |
| `SPACES_KEY` / `SPACES_SECRET` | — | API, Worker | Spaces credentials. |
| `SPACES_ENDPOINT` | — | API, Worker | Region endpoint WITHOUT the bucket, e.g. `https://blr1.digitaloceanspaces.com`. |
| `SPACES_BUCKET` | — | API, Worker | Bucket name. |
| `SPACES_REGION` | — | API, Worker | Region slug, e.g. `blr1`. |
| `SPACES_PUBLIC_ENDPOINT` | — | API | Browser-facing endpoint, only if a CDN sits in front. |
| `SPACES_ACL` | private | API, Worker | `public-read` makes every uploaded file world-readable and bypasses the app's permissions. Leave unset. |
| `SPACES_DOWNLOAD_CONCURRENCY` | `10` | Worker | Parallel 8 MB parts when downloading one PDF. |
| `SPACES_POOL_SIZE` | computed (`80` with defaults) | Worker | HTTPS connections kept open to storage. |

## 3. AI provider keys and shared model behaviour

| Variable | Default | Used by | What it does |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | — | API, Worker | Claude key. Needed for any stage set to `claude`. |
| `ANTHROPIC_MAX_RETRIES` | `6` | Worker | Retries on rate limits / server errors. |
| `ANTHROPIC_BASE_URL` | — | API, Worker | Point Claude calls at a stub (offline testing). |
| `GEMINI_API_KEY` | — | API, Worker | Gemini key (chat, summaries, sheets, vision, RFIs, embeddings when set to gemini). |
| `VOYAGE_API_KEY` | — | API, Worker | Voyage key (embeddings, reranking). |
| `COHERE_API_KEY` | — | API, Worker | Cohere key (embeddings, reranking). |
| `VOYAGE_BASE_URL` / `COHERE_BASE_URL` | provider URL | API | Point reranking at a stub. |
| `CLAUDE_THINKING` | `off` | Worker | Claude reasoning for every worker call. `off` sends "disabled"; anything else lets the model decide. |
| `GEMINI_THINKING_BUDGET` | `0` | Worker | Gemini 2.x reasoning budget. Ignored by Gemini 3+. |
| `GEMINI_THINKING_LEVEL` | `minimal` | Worker | Gemini 3+ reasoning level: `minimal`/`low`/`medium`/`high`. |
| `GEMINI_MEDIA_RESOLUTION` | `ultra_high` | Worker | How many tokens Gemini spends reading each image. |
| `BATCH_TIMEOUT_SECONDS` | `3600` | Worker | Longest wait for a provider batch job (summaries, embeddings) before the job fails. |
| `BATCH_POLL_SECONDS` | `5` | Worker | First polling interval for a batch. |
| `BATCH_POLL_MAX_SECONDS` | `60` | Worker | Polling interval ceiling. |

## 4. Embeddings (search index)

| Variable | Default | Used by | What it does |
|---|---|---|---|
| `EMBEDDING_PROVIDER` | `voyage` | API, Worker | `voyage` / `cohere` / `gemini`. API and worker MUST match. Changing it needs a re-index into a new collection. |
| `EMBEDDING_MODEL` | provider default | API, Worker | `voyage-3`, `embed-v4.0`, `gemini-embedding-001`… |
| `EMBEDDING_DIM` | `1024` | API, Worker | Vector width; must equal the collection's. |
| `EMBED_SEND_DIMENSION` | per provider | API, Worker | Ask the provider for that width explicitly. |
| `EMBED_BATCH_SIZE` | provider max | Worker | Texts per request (voyage 128, cohere 96, gemini 100). Old name `VOYAGE_BATCH_SIZE` still works. |
| `EMBED_BATCH_DELAY` | `0` | Worker | Seconds between requests (for free tiers). Old name `VOYAGE_BATCH_DELAY`. |
| `EMBED_MAX_RETRIES` | `6` | Worker | Retries on 429/5xx. Old name `VOYAGE_MAX_RETRIES`. |
| `EMBED_MAX_BATCH_TOKENS` | voyage 100k, cohere 90k, gemini 18k | Worker | Token ceiling per request. |
| `EMBED_TIMEOUT_SECONDS` | `120` | Worker | HTTP timeout per embedding request. |
| `EMBED_USE_BATCH` | `false` | Worker | Half-price async batch (Gemini only; no-op elsewhere). |
| `EMBED_BATCH_MIN` | `200` | Worker | Below this many chunks, skip the batch API. |
| `EMBED_CACHE_ENABLED` | `true` | Worker | Redis cache of vectors for repeated text. |
| `EMBED_CACHE_TTL_SECONDS` | `1209600` (14 days) | Worker | How long cached vectors live. |
| `EMBEDDINGS_ENABLED` | `true` | Worker | `false` skips embedding entirely (chat then has nothing to search). |

## 5. Retrieval and chat

| Variable | Default | Used by | What it does |
|---|---|---|---|
| `HYBRID_RETRIEVAL` | `true` | API | Vector + keyword search fused. `false` = vector only. |
| `RERANK_PROVIDER` | `none` | API | `cohere` / `voyage` reranks results; costs a call per question. |
| `RERANK_MODEL` | provider default | API | Reranker model. |
| `RERANK_CANDIDATES` | `60` | API | Chunks the reranker judges per question. |
| `CHAT_PROVIDER` | `claude` | API | `claude` / `gemini`. |
| `CHAT_MODEL` | `claude-sonnet-5` | API | Claude chat model. |
| `CHAT_GEMINI_MODEL` | `models/gemini-3.1-pro-preview` | API | Gemini chat model. |
| `CHAT_SCOPE` | `construction` | API | `construction` also answers general construction questions (labelled); `documents` answers only from the drawings. |

## 6. Sheet-number and discipline detection

| Variable | Default | Used by | What it does |
|---|---|---|---|
| `SHEET_EXTRACTION` | `ai` | Worker | `ai` = model reads the title block; `rules` = pattern match only, no API calls. |
| `SHEET_PROVIDER` | `claude` | Worker | Which model reads it. |
| `CLASSIFIER_MODEL` | `claude-haiku-4-5-20251001` | Worker | Claude model for sheet reading. |
| `GEMINI_MODEL` | `gemini-3.6-flash` | Worker | Gemini model for sheet reading. |
| `SHEET_RULES_FIRST` | `true` | Worker | Resolve obvious sheet numbers by pattern, send only unclear ones to the model. |
| `SHEET_BATCH_SIZE` | `25` | Worker | Title blocks per model request. |
| `SHEET_SNIPPET_CHARS` | `2500` | Worker | Max characters of page text in the legacy (no-region) path. |
| `REGION_SNIPPET_CHARS` | `600` | Worker | Max characters of title-block text sent per page. |
| `REGION_OCR_DPI` | `300` | Worker | OCR resolution for title blocks with no text layer. |

## 7. Page extraction

| Variable | Default | Used by | What it does |
|---|---|---|---|
| `OCR_ENABLED` | `true` | Worker | OCR pages that have no text layer. |
| `PAGE_RENDER_ZOOM` | `2` | Worker | Page image resolution (2 = 144 DPI). Bigger = sharper viewer, more memory and storage. |
| `THUMB_WIDTH` | `200` | Worker | Thumbnail width in pixels. |
| `TABLE_EXTRACTION_ENABLED` | `true` | Worker | Lift schedules out as whole tables. |
| `MAX_TABLES_PER_PAGE` | `12` | Worker | More than this on a page = the border was misread as a table; discard. |
| `TABLE_DETECTION_BUDGET_SECONDS` | `5` | Worker | Slowest allowed table scan; after that the rest of the document skips it. |

## 8. Vision pass (AI describes the drawing)

| Variable | Default | Used by | What it does |
|---|---|---|---|
| `VLM_ENABLED` | `false` | Worker | Turn the vision pass on (one model call per page at upload). |
| `VLM_PROVIDER` | `claude` | Worker | `claude` / `gemini`. |
| `VLM_CLAUDE_MODEL` | `claude-sonnet-5` | Worker | Must be a high-resolution model (Haiku cannot read these sheets). |
| `VLM_GEMINI_MODEL` | `gemini-3.6-flash` | Worker | Gemini vision model. |
| `VLM_MAX_EDGE` | `2576` | Worker | Rendered image size. Above 3072 buys nothing — the providers shrink it. |
| `VLM_MAX_TOKENS` | `4000` | Worker | Output cap per page description (thinking counts against it). |
| `VLM_CROP` | `off` | Worker | `intersections` = one crop per grid intersection instead of the whole sheet. |
| `VLM_CROP_BAYS` | `0.6` | Worker | Crop size, in grid bays. |
| `VLM_CROP_BATCH` | `6` | Worker | Crops per model call. |
| `VLM_CROP_MAX` | `60` | Worker | Pages with more intersections fall back to the whole-sheet pass. |
| `VLM_CROP_MAX_TOKENS` | `1500` | Worker | Output cap per crop batch. |

## 9. Summaries

| Variable | Default | Used by | What it does |
|---|---|---|---|
| `SUMMARIES_ENABLED` | `true` | Worker | `false` refuses every summary job. |
| `SUMMARY_PROVIDER` | `claude` | API, Worker | `claude` / `gemini`. The API reads it only to show the cost estimate. |
| `SUMMARY_MODEL` | `claude-sonnet-5` | API, Worker | Claude summary model. |
| `SUMMARY_GEMINI_MODEL` | `models/gemini-3.1-pro-preview` | API, Worker | Gemini summary model. |
| `SUMMARY_USE_BATCH` | `false` in code, `true` in `.env.example` | API, Worker | Page summaries through the half-price batch API. |
| `SUMMARY_BATCH_MIN_PAGES` | `4` | Worker | Fewer pages than this skip the batch API. |
| `SUMMARY_MAX_TOKENS` | `2000` | Worker | Output cap per summary call. A cut-off answer is retried once at double. |

## 10. Generated RFIs

| Variable | Default | Used by | What it does |
|---|---|---|---|
| `RFI_PROVIDER` | `claude` | Worker | Model that words findings as questions. |
| `RFI_MODEL` | `claude-haiku-4-5-20251001` | Worker | Claude wording model. |
| `RFI_GEMINI_MODEL` | `gemini-3.6-flash` | Worker | Gemini wording model. |
| `RFI_THINKING` | — (global setting) | Worker | `off`/`minimal`/`low`/`medium`/`high` for the wording model only. |
| `RFI_AI_WORDING` | `true` | Worker | `false` = template wording, no model calls. |
| `RFI_GRID_CHECK` | `true` | Worker | The grid-mismatch check (reads the PDFs). |
| `RFI_WORDING_BATCH` | `15` | Worker | Findings worded per model call. |

## 11. Worker concurrency and job handling

| Variable | Default | Used by | What it does |
|---|---|---|---|
| `WORKER_CONCURRENCY` | `4` | Worker | Default jobs at once per queue. |
| `PROCESS_CONCURRENCY` | `WORKER_CONCURRENCY` | Worker | Documents processed at once (~250–400 MB each). |
| `PAGE_CONCURRENCY` | `4` | Worker | Pages processed at once inside one document. |
| `SCRAPE_CONCURRENCY` | `WORKER_CONCURRENCY` | Worker | Title-block scrape jobs at once. |
| `SUMMARIZE_PORTION_CONCURRENCY` | `WORKER_CONCURRENCY` | Worker | Discipline summaries at once. |
| `SUMMARIZE_PROJECT_CONCURRENCY` | `1` | Worker | Project rollups at once. |
| `RFI_SCAN_CONCURRENCY` | `1` | Worker | RFI scans at once (one per project is always enforced). |
| `WORKER_LOCK_DURATION_MS` | `600000` (10 min) | Worker | Job lock, renewed while running. A dead worker's job waits this long before another picks it up. |
| `DB_POOL_SIZE` | computed (`16` with defaults) | Worker | Postgres connections per worker process. |

## 12. Rate limits (per user unless noted; `0` disables a tier)

| Variable | Default | Used by | What it does |
|---|---|---|---|
| `RATE_LIMIT_FLOOD_PER_MINUTE` | `1200` | API | Per IP, before login is checked. |
| `RATE_LIMIT_GENERAL_PER_MINUTE` | `600` | API | Every API request. |
| `RATE_LIMIT_AUTH_PER_15MIN` | `20` | API | Failed logins/registrations, per IP. |
| `RATE_LIMIT_CHAT_PER_MINUTE` | `20` | API | Chat questions. |
| `RATE_LIMIT_SUMMARY_PER_HOUR` | `30` | API | Shared by discipline summaries, project rollups, full rebuilds AND RFI scans. |

## 13. Email and upload security

| Variable | Default | Used by | What it does |
|---|---|---|---|
| `SMTP_HOST` | — | API | Mail server. Unset = emails (including reset links) are printed to the API log. |
| `SMTP_PORT` | `587` | API | Mail port. |
| `SMTP_SECURE` | `false` | API | `true` for TLS on 465. |
| `SMTP_USER` / `SMTP_PASSWORD` | — | API | Mail credentials. |
| `MAIL_FROM` | `ArcAligned AI <no-reply@example.com>` | API | Sender address. |
| `SUPPORT_EMAIL` | — | API | Where support tickets go; unset = stored in the database only. |
| `MALWARE_SCAN_URL` | — | API | Upload scanner hook; unset = no scan. When set, it fails closed. |

## 14. Deployment only (docker compose)

| Variable | Used in | What it does |
|---|---|---|
| `APP_DOMAIN` | `docker-compose.app.yml` | Public domain for HTTPS. |
| `PRIVATE_IP` | `docker-compose.app.yml` | VPC address Postgres/Redis/Qdrant bind to (never `0.0.0.0`). |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | local / dist | Database container credentials. |
| `STORAGE_DATA_DIR` | local / dist | Folder MinIO stores files in. |
| `WEB_PORT` / `MINIO_PORT` | local / dist | Host ports for the web app and MinIO. |
| `API_MEM_LIMIT`, `WORKER_MEM_LIMIT`, `POSTGRES_MEM_LIMIT`, `REDIS_MEM_LIMIT`, `QDRANT_MEM_LIMIT`, `MINIO_MEM_LIMIT` | local / dist | Memory cap per container. |
| `CDIP_IMAGE_PREFIX` / `CDIP_TAG` | `docker-compose.dist.yml` | Which published images and version to run. |
