"""Worker configuration. Loads the repo-root .env, then reads process env."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")
load_dotenv()  # also allow a workers-local .env to override

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/cdip")
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")
SPACES_KEY = os.environ.get("SPACES_KEY", "minioadmin")
SPACES_SECRET = os.environ.get("SPACES_SECRET", "minioadmin")
# Region endpoint WITHOUT the bucket name (https://blr1.digitaloceanspaces.com
# for DO Spaces, http://localhost:9000 for MinIO).
SPACES_ENDPOINT = os.environ.get("SPACES_ENDPOINT", "http://localhost:9000")
SPACES_BUCKET = os.environ.get("SPACES_BUCKET", "cdip-local")
SPACES_REGION = os.environ.get("SPACES_REGION", "us-east-1")
# Optional canned ACL for objects the worker writes (page images/thumbs/text).
# Leave unset to keep them private (served via presigned URLs); set to
# "public-read" only to deliberately expose them (matches the API's SPACES_ACL).
SPACES_ACL = os.environ.get("SPACES_ACL") or None


def _flag(name: str, default: str = "true") -> bool:
    return os.environ.get(name, default).strip().lower() not in ("false", "0", "no", "off")


# Pipeline stage switches — test the chat (embedding→retrieval) and summary
# flows independently, e.g. turn embeddings off while the free Voyage tier is
# rate-limited and still exercise summaries.
EMBEDDINGS_ENABLED = _flag("EMBEDDINGS_ENABLED")
SUMMARIES_ENABLED = _flag("SUMMARIES_ENABLED")

# Rendering: 2x zoom ≈ 144 dpi page PNGs; thumbnails resized to this width.
PAGE_RENDER_ZOOM = float(os.environ.get("PAGE_RENDER_ZOOM", "2"))
THUMB_WIDTH = int(os.environ.get("THUMB_WIDTH", "200"))

# FR-7: OCR pages with no text layer. Disable to run without PaddleOCR installed.
OCR_ENABLED = os.environ.get("OCR_ENABLED", "true").lower() != "false"

# FR-8: lift schedules out of the page as whole tables instead of letting the
# block extractor shred them. Detection is a heuristic (workers/src/tables.py);
# turn it off if a particular sheet set trips it more than it helps.
TABLE_EXTRACTION_ENABLED = (
    os.environ.get("TABLE_EXTRACTION_ENABLED", "true").lower() != "false"
)
# Above this, the page's "tables" are its drawing border and grid lines being
# read as a grid, so the whole page's detection is discarded.
MAX_TABLES_PER_PAGE = int(os.environ.get("MAX_TABLES_PER_PAGE", "12"))


# --- Parallelism ---------------------------------------------------------
#
# How many jobs one worker process runs at once, per queue. Ten people
# uploading at the same time should not queue behind each other, so these
# default above 1 — but they are not free, and each queue has a different
# reason to be capped:
#
#   process-document  memory. Each job holds one PDF plus the page it is
#                     rendering; PyMuPDF releases the GIL during rendering and
#                     the rest is network I/O, so threads really do overlap.
#                     Budget ~250-400 MB per concurrent job.
#   scrape-region     provider rate limits. A project's unresolved pages are
#                     batched into a few calls (SHEET_BATCH_SIZE), so this
#                     multiplies the request rate per PROJECT, not per page.
#   summarize-portion provider rate limits + spend. Sonnet, and every job is
#                     something a user is waiting on.
#   summarize-project rollups only, cheap, and one per project — 1 is plenty.
#
# WORKER_CONCURRENCY sets them all; the per-queue variables override it.
def _int(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, "") or default))
    except ValueError:
        return default


WORKER_CONCURRENCY = _int("WORKER_CONCURRENCY", 4)
PROCESS_CONCURRENCY = _int("PROCESS_CONCURRENCY", WORKER_CONCURRENCY)

# Pages rendered at once WITHIN one document. A page is independent work, and
# a 400-page set processed serially leaves the CPU idle during every upload and
# the network idle during every render — concurrency across documents does
# nothing for a single large PDF.
#
# The number that actually matters for memory is the PRODUCT:
#
#     PROCESS_CONCURRENCY x PAGE_CONCURRENCY = pages in flight at once
#
# Each in-flight page holds a full-resolution pixmap — an ISO A1 sheet at
# PAGE_RENDER_ZOOM=2 is ~18 megapixels, a few hundred MB with its encode
# buffer. At the defaults that is 16 pages, so budget accordingly (or lower
# one of the two on a small worker).
PAGE_CONCURRENCY = _int("PAGE_CONCURRENCY", 4)
SCRAPE_CONCURRENCY = _int("SCRAPE_CONCURRENCY", WORKER_CONCURRENCY)
SUMMARIZE_PORTION_CONCURRENCY = _int("SUMMARIZE_PORTION_CONCURRENCY", WORKER_CONCURRENCY)
SUMMARIZE_PROJECT_CONCURRENCY = _int("SUMMARIZE_PROJECT_CONCURRENCY", 1)

# Postgres connections held by this process. Every db helper borrows one for
# the length of a single statement, so the pool has to cover the threads that
# might hold one at the same moment — which, with per-page parallelism, is the
# document workers TIMES their page threads, not just the job count. Sized too
# small, parallelism turns into pool-timeout errors rather than speed.
# Postgres defaults to max_connections 100 across ALL clients — keep
# replicas x DB_POOL_SIZE well under it.
DB_POOL_SIZE = _int(
    "DB_POOL_SIZE", max(4, PROCESS_CONCURRENCY * PAGE_CONCURRENCY, WORKER_CONCURRENCY * 2)
)

# HTTPS connections botocore keeps open to Spaces. Sized off the same product
# as DB_POOL_SIZE, for the same reason: every in-flight page uploads its PNG,
# thumbnail and text, so the threads contending for a connection are the
# document workers TIMES their page threads.
#
# botocore's own default is 10, which is under the 16 pages the defaults put in
# flight. Overflow is not an error — urllib3 DISCARDS the returning connection
# and logs "Connection pool is full" — so it shows up as a slow upload rather
# than a failure: the next request pays a fresh TCP and TLS handshake to a
# region that may be a continent away. Over a 1000-page set that is thousands
# of avoidable round trips.
SPACES_POOL_SIZE = _int(
    "SPACES_POOL_SIZE", max(10, PROCESS_CONCURRENCY * PAGE_CONCURRENCY, WORKER_CONCURRENCY * 2)
)
