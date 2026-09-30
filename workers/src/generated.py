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

def review_evidence_key(project_id: str, run_id: str, evidence_id: str) -> str:
    return f"projects/{project_id}/rfi-reviews/{run_id}/evidence/{evidence_id}.png"


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
        "sourceSection": "General",
        "sourceQuestionNumber": 1,
        "originalQuestion": "what are the Grid to Grid Dimensions, compare the grids with architecture if there is any mismatch isolate and highlight",
        "label": "Grid names and grid-to-grid dimensions",
        "requiredObservations": [
            "grid label",
            "grid pair",
            "dimension between the pair",
            "units",
            "level/view",
            "which end of the line",
        ],
        "query": "grid line grid dimension spacing",
        "applicability": {
            "always": True,
            "keywords": [],
        },
        "comparisonRules": "Compare architectural and structural grids for the SAME area and level. Resolve renamed or jogged grids, orientation and explicit offsets before calling a mismatch.",
        "candidateRules": "A candidate needs the same grid pair dimensioned differently, or one grid line named differently, on two localized sources.",
        "deterministic": "grid_mismatch",
        "geometryAids": [],
    },
    {
        "id": "G02",
        "sourceSection": "General",
        "sourceQuestionNumber": 2,
        "originalQuestion": "Find all the Level and there elevation from the drawings set, compare the Levels with architecture if there is any mismatch isolate and highlight",
        "label": "Levels and their elevations",
        "requiredObservations": [
            "level name",
            "elevation",
            "units",
            "vertical datum",
            "source view",
        ],
        "query": "level elevation datum T.O.S. top of slab finish floor",
        "applicability": {
            "always": True,
            "keywords": [],
        },
        "comparisonRules": "Normalize level aliases (LEVEL 5 / L5 / 5TH FLOOR) and datums before comparing. Structural top of slab and architectural finish floor differ by design; compare like with like.",
        "candidateRules": "A candidate needs one level given two different elevations on the same datum by two localized sources.",
        "deterministic": None,
        "geometryAids": [],
    },
    {
        "id": "F01",
        "sourceSection": "Foundations",
        "sourceQuestionNumber": 1,
        "originalQuestion": "What are all the isolated footing types in the floor plans and foundation schedules, location sizes, and elevation.",
        "label": "Isolated footings",
        "requiredObservations": [
            "footing mark/type",
            "instance location from grids",
            "size",
            "elevation",
            "schedule row",
        ],
        "query": "isolated footing spread footing schedule F1 size elevation",
        "applicability": {
            "always": False,
            "keywords": [
                "FOOTING",
                "FOUNDATION",
                "FTG",
                "SPREAD",
            ],
        },
        "comparisonRules": "Match plan instances to schedule rows by mark. A size given by type in the schedule is not missing from the instance.",
        "candidateRules": "A candidate needs a mark with no schedule row, a schedule size contradicted elsewhere, or an instance with no locating information after plans, schedules and details were searched.",
        "deterministic": None,
        "geometryAids": [],
    },
    {
        "id": "F02",
        "sourceSection": "Foundations",
        "sourceQuestionNumber": 2,
        "originalQuestion": "What are all the Wall footing in the floor plans and foundation schedule, also find there size location and elevation from grids.",
        "label": "Wall footings",
        "requiredObservations": [
            "wall footing mark/type",
            "segment extent",
            "size",
            "location from grids",
            "elevation",
            "reference face or centerline",
        ],
        "query": "wall footing continuous footing strip footing schedule size elevation",
        "applicability": {
            "always": False,
            "keywords": [
                "WALL FOOTING",
                "CONTINUOUS FOOTING",
                "STRIP FOOTING",
                "WF",
                "FOUNDATION",
            ],
        },
        "comparisonRules": "Match segments and extents; compare only the same reference (face vs centerline).",
        "candidateRules": "A candidate needs a segment whose size, location or elevation conflicts between sources, or cannot be located after the relevant sections were searched.",
        "deterministic": None,
        "geometryAids": [],
    },
    {
        "id": "F03",
        "sourceSection": "Foundations",
        "sourceQuestionNumber": 3,
        "originalQuestion": "what are all the Mat foundation in floor plans and foundation schedules.",
        "label": "Mat foundations",
        "requiredObservations": [
            "mat mark",
            "presence on plan",
            "schedule row",
            "stated extents, thickness and elevation where given",
        ],
        "query": "mat foundation mat slab raft schedule thickness",
        "applicability": {
            "always": False,
            "keywords": [
                "MAT",
                "RAFT",
                "FOUNDATION",
            ],
        },
        "comparisonRules": "Reconcile presence and identity between plans and schedules. Extents and thickness are supporting attributes, not mandatory fields.",
        "candidateRules": "A candidate needs a mat on one source that the other contradicts or omits.",
        "deterministic": None,
        "geometryAids": [],
    },
    {
        "id": "F04",
        "sourceSection": "Foundations",
        "sourceQuestionNumber": 4,
        "originalQuestion": "Are there any piles in the floor plans, if so what are the size and location.",
        "label": "Piles",
        "requiredObservations": [
            "pile presence",
            "pile mark/type",
            "size",
            "location from grids",
            "pile cap it belongs to",
        ],
        "query": "pile pile cap pile schedule diameter capacity location",
        "applicability": {
            "always": False,
            "keywords": [
                "PILE",
                "PC",
                "CAISSON",
                "PIER",
                "FOUNDATION",
            ],
        },
        "comparisonRules": "Distinguish individual piles from pile caps. Match pile types to their legend or schedule.",
        "candidateRules": "A candidate needs a pile or pile cap whose size or location conflicts between sources, or cannot be located.",
        "deterministic": None,
        "geometryAids": [],
    },
    {
        "id": "FL01",
        "sourceSection": "Floor",
        "sourceQuestionNumber": 1,
        "originalQuestion": "What are the floor edges location from Grids",
        "label": "Floor edge locations",
        "requiredObservations": [
            "edge segment",
            "reference grid",
            "offset",
            "face/edge convention",
        ],
        "query": "slab edge floor edge dimension from grid edge of slab",
        "applicability": {
            "always": False,
            "keywords": [
                "SLAB",
                "FLOOR",
                "EDGE",
                "PLAN",
                "LEVEL",
            ],
        },
        "comparisonRules": "Compare the same edge on corresponding architectural and structural plans; account for intentional setbacks.",
        "candidateRules": "A candidate needs one edge located differently by two sources, or an edge with no locating dimension after enlarged plans and details were searched.",
        "deterministic": None,
        "geometryAids": [],
    },
    {
        "id": "FL02",
        "sourceSection": "Floor",
        "sourceQuestionNumber": 2,
        "originalQuestion": "What are the Floor top Elevation and Thickness",
        "label": "Floor top elevation and thickness",
        "requiredObservations": [
            "slab zone",
            "top elevation",
            "thickness",
            "units",
            "datum",
        ],
        "query": "slab thickness top of slab elevation T.O.S. floor elevation",
        "applicability": {
            "always": False,
            "keywords": [
                "SLAB",
                "FLOOR",
                "T.O.S",
                "TOS",
                "LEVEL",
                "PLAN",
            ],
        },
        "comparisonRules": "Distinguish structural top of slab from finish floor, and local thickening from the typical thickness.",
        "candidateRules": "A candidate needs one zone given two different elevations or thicknesses by two sources on the same datum.",
        "deterministic": None,
        "geometryAids": [],
    },
    {
        "id": "FL03",
        "sourceSection": "Floor",
        "sourceQuestionNumber": 3,
        "originalQuestion": "are there any stepping in floor, if yes what are the location and elevation different at stepping",
        "label": "Floor steps",
        "requiredObservations": [
            "step location/extent",
            "elevation each side",
            "difference (derived, with its operands)",
        ],
        "query": "slab step depression drop elevation change recess",
        "applicability": {
            "always": False,
            "keywords": [
                "STEP",
                "DEPRESS",
                "RECESS",
                "DROP",
                "SLAB",
                "FLOOR",
            ],
        },
        "comparisonRules": "A documented step is an inventory item, not an RFI. Compare the step on plan with its section.",
        "candidateRules": "A candidate needs a step whose elevations disagree between plan and section, or a step with no elevations anywhere searched.",
        "deterministic": None,
        "geometryAids": [],
    },
    {
        "id": "FL04",
        "sourceSection": "Floor",
        "sourceQuestionNumber": 4,
        "originalQuestion": "are there any slop on the floor, if yes what is the sloping direction span and sloping percentage",
        "label": "Floor slopes",
        "requiredObservations": [
            "sloped zone",
            "direction",
            "horizontal run",
            "rise or slope as printed",
            "percentage (derived only when rise and run are both supported)",
        ],
        "query": "slope slope to drain per foot ramp spot elevation",
        "applicability": {
            "always": False,
            "keywords": [
                "SLOPE",
                "RAMP",
                "DRAIN",
                "PER FT",
                "%",
            ],
        },
        "comparisonRules": "Convert ratio to percent only with a known convention: 100 x rise / horizontal run, marked derived.",
        "candidateRules": "A candidate needs a slope whose direction, run or rate conflicts between sources, or a sloped zone with no rate anywhere searched.",
        "deterministic": None,
        "geometryAids": [],
    },
    {
        "id": "C01",
        "sourceSection": "Columns & Shear walls",
        "sourceQuestionNumber": 1,
        "originalQuestion": "Find all the columns in the floor plans and columns schedule, find sizes, location from Grids and base and top elevation of all the columns.",
        "label": "Columns: size, location, base and top",
        "requiredObservations": [
            "column mark",
            "instance location from grids",
            "size",
            "base elevation",
            "top elevation",
            "schedule row",
        ],
        "query": "column schedule column size mark location grid base top elevation",
        "applicability": {
            "always": False,
            "keywords": [
                "COLUMN",
                "COL",
                "C-",
                "PLAN",
                "FRAMING",
            ],
        },
        "comparisonRules": "Match instances by mark AND level. Size changes up the building and transfers are not conflicts.",
        "candidateRules": "A candidate needs a column shown at a different place or size on two sources for the same level, or with no base/top elevation after the schedule and sections were searched.",
        "deterministic": "column_mismatch",
        "geometryAids": [],
    },
    {
        "id": "C02",
        "sourceSection": "Columns & Shear walls",
        "sourceQuestionNumber": 2,
        "originalQuestion": "Find all the shear Wall or Core wall in the floor plan, find sizes, location from Grids and base and top elevation of all the Core Walls.",
        "label": "Shear and core walls",
        "requiredObservations": [
            "wall mark",
            "thickness",
            "location from grids (face or centerline)",
            "base elevation",
            "top elevation",
        ],
        "query": "core wall shear wall location grid thickness wall schedule",
        "applicability": {
            "always": False,
            "keywords": [
                "CORE",
                "SHEAR WALL",
                "SHEARWALL",
                "SW-",
                "WALL",
            ],
        },
        "comparisonRules": "Localize wall faces or centerlines consistently and compare corresponding views of the same level.",
        "candidateRules": "A candidate needs a wall at a different place or thickness on two sources, or with no locating dimension after sections were searched.",
        "deterministic": None,
        "geometryAids": [],
    },
    {
        "id": "C03",
        "sourceSection": "Columns & Shear walls",
        "sourceQuestionNumber": 3,
        "originalQuestion": "if any column is centered to grid, most probably it is not dimensioned from grid, but if any column is off from the grids and not dimensioned from grid isolate and highlight.",
        "label": "Off-grid columns without a locating dimension",
        "requiredObservations": [
            "column mark",
            "centred on the grid crossing or not",
            "governing locating dimension if off grid",
            "where that dimension is",
        ],
        "query": "column offset from grid dimension column location enlarged plan",
        "applicability": {
            "always": False,
            "keywords": [
                "COLUMN",
                "COL",
                "C-",
                "PLAN",
                "FRAMING",
            ],
        },
        "comparisonRules": "A centred column needs no offset dimension. An off-grid column is located only by a printed dimension, note, enlarged detail or schedule — never by a measurement of the drawing.",
        "candidateRules": "A candidate needs an off-grid column with no locating dimension after dimension chains, notes, enlarged details and schedules were searched. Unreadable is insufficient evidence, not a candidate.",
        "deterministic": None,
        "geometryAids": [
            "column_grid_offsets",
        ],
    },
    {
        "id": "B01",
        "sourceSection": "Beams",
        "sourceQuestionNumber": 1,
        "originalQuestion": "what is the start and end of the beams with reference to grids",
        "label": "Beam start and end",
        "requiredObservations": [
            "beam mark",
            "start location from grids",
            "end location from grids",
            "endpoint convention (support, face or centerline)",
        ],
        "query": "beam framing plan beam span support grid",
        "applicability": {
            "always": False,
            "keywords": [
                "BEAM",
                "FRAMING",
                "GIRDER",
                "JOIST",
                "B-",
            ],
        },
        "comparisonRules": "Compare only equivalent endpoint references (support vs face vs centerline).",
        "candidateRules": "A candidate needs a beam whose ends are located differently by two sources, or cannot be located after framing details were searched.",
        "deterministic": None,
        "geometryAids": [],
    },
    {
        "id": "B02",
        "sourceSection": "Beams",
        "sourceQuestionNumber": 2,
        "originalQuestion": "what is the size of the beam.",
        "label": "Beam size",
        "requiredObservations": [
            "beam mark/type",
            "size or section",
            "units",
        ],
        "query": "beam schedule beam size section W shape depth",
        "applicability": {
            "always": False,
            "keywords": [
                "BEAM",
                "FRAMING",
                "GIRDER",
                "JOIST",
                "B-",
            ],
        },
        "comparisonRules": "Reconcile framing tags, schedules and sections, including legitimate variation along one member.",
        "candidateRules": "A candidate needs one beam given two sizes by two sources, or a tagged beam with no size anywhere searched.",
        "deterministic": None,
        "geometryAids": [],
    },
    {
        "id": "B03",
        "sourceSection": "Beams",
        "sourceQuestionNumber": 3,
        "originalQuestion": "what is the elevation of the beams.",
        "label": "Beam elevation",
        "requiredObservations": [
            "beam mark",
            "elevation",
            "datum",
            "reference surface (top, bottom or centerline)",
        ],
        "query": "beam elevation top of beam T.O.B. bottom of beam soffit",
        "applicability": {
            "always": False,
            "keywords": [
                "BEAM",
                "FRAMING",
                "GIRDER",
                "T.O.B",
                "TOB",
            ],
        },
        "comparisonRules": "Distinguish top, bottom and centerline; account for documented slopes and steps.",
        "candidateRules": "A candidate needs one beam at two elevations on the same reference by two sources, or no elevation anywhere searched.",
        "deterministic": None,
        "geometryAids": [],
    },
]

RFI_REVIEW_DEPTHS = {
    "quick": {
        "label": "Quick",
        "chunks": 24,
        "visualPages": 4,
        "cropsPerPage": 2,
        "pairWindows": 2,
        "hitsPerQuery": 12,
        "referencePages": 2,
        "maxBatches": 1,
        "maxTotalTokens": 200000,
    },
    "standard": {
        "label": "Standard",
        "chunks": 48,
        "visualPages": 8,
        "cropsPerPage": 3,
        "pairWindows": 4,
        "hitsPerQuery": 24,
        "referencePages": 4,
        "maxBatches": 2,
        "maxTotalTokens": 500000,
    },
    "deep": {
        "label": "Deep",
        "chunks": 96,
        "visualPages": 14,
        "cropsPerPage": 4,
        "pairWindows": 6,
        "hitsPerQuery": 36,
        "referencePages": 8,
        "maxBatches": 4,
        "maxTotalTokens": 1200000,
    },
}

RFI_REVIEW_CATALOGUE_VERSION = "2026-09-30.1"
RFI_REVIEW_INPUT_LIMITS = {
    "min": 20000,
    "max": 400000,
    "default": 120000,
}
RFI_REVIEW_THINKING_LIMITS = {
    "min": 1024,
    "max": 32000,
}
