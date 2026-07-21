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

# Rendering: 2x zoom ≈ 144 dpi page PNGs; thumbnails resized to this width.
PAGE_RENDER_ZOOM = float(os.environ.get("PAGE_RENDER_ZOOM", "2"))
THUMB_WIDTH = int(os.environ.get("THUMB_WIDTH", "200"))

# FR-7: OCR pages with no text layer. Disable to run without PaddleOCR installed.
OCR_ENABLED = os.environ.get("OCR_ENABLED", "true").lower() != "false"
