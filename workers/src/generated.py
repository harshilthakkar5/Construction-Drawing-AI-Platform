"""Contracts shared with the Node API — GENERATED, do not edit.

Written by packages/shared/codegen.mjs from packages/shared/src/index.ts,
which is the single source for queue names, job payload fields and object
keys. Editing this file by hand is pointless: `npm test` regenerates it and
fails if the checked-in copy differs.

    npm run codegen -w @cdip/shared
"""

from __future__ import annotations

# --- Queue names ---

PROCESS_DOCUMENT_QUEUE = "process-document"
SCRAPE_REGION_QUEUE = "scrape-region"
SUMMARIZE_PORTION_QUEUE = "summarize-portion"
SUMMARIZE_PROJECT_QUEUE = "summarize-project"

# --- Object keys (Spaces/MinIO bucket layout) ---

def original_pdf_key(project_id: str, document_id: str) -> str:
    return f"projects/{project_id}/pdfs/{document_id}/original.pdf"

def page_image_key(project_id: str, document_id: str, page: int) -> str:
    return f"projects/{project_id}/pdfs/{document_id}/pages/{page}.png"

def page_thumb_key(project_id: str, document_id: str, page: int) -> str:
    return f"projects/{project_id}/pdfs/{document_id}/thumbs/{page}.jpg"

def page_text_key(project_id: str, document_id: str, page: int) -> str:
    return f"projects/{project_id}/pdfs/{document_id}/text/{page}.txt"


# --- Job payload fields ---

# name -> (payload key, python attribute, is_optional, cast)
JOB_FIELDS: dict[str, tuple[tuple[str, str, bool, str], ...]] = {
    "processDocument": (
        ("projectId", "project_id", False, "str"),
        ("documentId", "document_id", False, "str"),
        ("spacesKey", "spaces_key", False, "str"),
    ),
    "scrapeRegion": (
        ("projectId", "project_id", False, "str"),
        ("regionVersion", "region_version", False, "int"),
        ("documentId", "document_id", True, "str"),
    ),
    "summarizePortion": (
        ("projectId", "project_id", False, "str"),
        ("portionId", "portion_id", False, "str"),
        ("requestedById", "requested_by_id", True, "str"),
    ),
    "summarizeProject": (
        ("projectId", "project_id", False, "str"),
    ),
}
