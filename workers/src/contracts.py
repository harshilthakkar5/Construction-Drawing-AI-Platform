"""Queue names and job payload shapes shared with the Node API.

Mirrors packages/shared/src/index.ts — keep the two in sync.
"""

from dataclasses import dataclass

PROCESS_DOCUMENT_QUEUE = "process-document"
SCRAPE_REGION_QUEUE = "scrape-region"
SUMMARIZE_PORTION_QUEUE = "summarize-portion"
SUMMARIZE_PROJECT_QUEUE = "summarize-project"


@dataclass(frozen=True)
class SummarizeProjectJob:
    project_id: str

    @classmethod
    def from_payload(cls, data: dict) -> "SummarizeProjectJob":
        return cls(project_id=data["projectId"])


@dataclass(frozen=True)
class ScrapeRegionJob:
    """Apply the project's title-block region to its pages, then classify."""

    project_id: str
    region_version: int
    document_id: str | None = None

    @classmethod
    def from_payload(cls, data: dict) -> "ScrapeRegionJob":
        return cls(
            project_id=data["projectId"],
            region_version=int(data["regionVersion"]),
            document_id=data.get("documentId"),
        )


@dataclass(frozen=True)
class SummarizePortionJob:
    """Summarize ONE discipline, because a user pressed its button."""

    project_id: str
    portion_id: str
    requested_by_id: str | None = None

    @classmethod
    def from_payload(cls, data: dict) -> "SummarizePortionJob":
        return cls(
            project_id=data["projectId"],
            portion_id=data["portionId"],
            requested_by_id=data.get("requestedById"),
        )


@dataclass(frozen=True)
class ProcessDocumentJob:
    project_id: str
    document_id: str
    spaces_key: str

    @classmethod
    def from_payload(cls, data: dict) -> "ProcessDocumentJob":
        return cls(
            project_id=data["projectId"],
            document_id=data["documentId"],
            spaces_key=data["spacesKey"],
        )
