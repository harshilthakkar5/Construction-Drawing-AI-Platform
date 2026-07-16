"""Queue names and job payload shapes shared with the Node API.

Mirrors packages/shared/src/index.ts — keep the two in sync.
"""

from dataclasses import dataclass

PROCESS_DOCUMENT_QUEUE = "process-document"


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
