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
