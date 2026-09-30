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
RFI_SCAN_QUEUE = "rfi-scan"
RFI_REVIEW_QUEUE = "rfi-review"

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
        ("detail", "detail", True, "str"),
    ),
    "summarizeProject": (
        ("projectId", "project_id", False, "str"),
        ("detail", "detail", True, "str"),
    ),
    "rfiScan": (
        ("projectId", "project_id", False, "str"),
        ("scanId", "scan_id", False, "str"),
    ),
    "rfiReview": (
        ("runId", "run_id", False, "str"),
    ),
}


# --- Targeted RFI review: check catalogue and depth caps ---

RFI_REVIEW_CHECKS = [
    {
        "id": "G01",
        "family": "General",
        "label": "Grid names and grid-to-grid dimensions",
        "objective": "Compare how the drawings name and dimension the grid. Localize any grid line that one drawing names or dimensions differently from another.",
        "query": "grid line grid dimension spacing",
        "autoKeywords": [],
        "deterministic": "grid_mismatch",
    },
    {
        "id": "C01",
        "family": "Columns & walls",
        "label": "Column location, size and mark",
        "objective": "Compare each column's mark, size and position relative to the grid across plans and schedules. Localize any column shown at a different place, with a different size or mark, or without a dimension locating it off the grid.",
        "query": "column schedule column size mark location grid offset",
        "autoKeywords": [],
        "deterministic": "column_mismatch",
    },
    {
        "id": "C02",
        "family": "Columns & walls",
        "label": "Core and shear wall location",
        "objective": "Compare the position, extent and thickness of core and shear walls relative to the grid across drawings. Localize any wall shown at a different place or with a different thickness.",
        "query": "core wall shear wall location grid thickness",
        "autoKeywords": [
            "CORE",
            "SHEAR WALL",
            "SHEARWALL",
            "SW-",
        ],
        "deterministic": None,
    },
]

RFI_REVIEW_DEPTHS = {
    "standard": {
        "label": "Standard",
        "chunks": 48,
        "visualPages": 8,
        "cropsPerPage": 3,
        "pairWindows": 4,
    },
}
