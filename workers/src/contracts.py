"""Queue names and job payload shapes shared with the Node API.

The names, fields and casts all come from `generated.py`, which
packages/shared/codegen.mjs emits from packages/shared/src/index.ts — so this
module is the ergonomic wrapper, never a second copy of the contract. Adding a
field on the TypeScript side is a compile error there until it is declared, and
a `npm test` failure here until the generated file is refreshed.

The dataclasses stay hand-written because their job is to be pleasant to use in
the worker; only the wire shape is generated.
"""

from dataclasses import dataclass, fields as dataclass_fields

from generated import (  # noqa: F401 — re-exported for the worker's imports
    JOB_FIELDS,
    PROCESS_DOCUMENT_QUEUE,
    SCRAPE_REGION_QUEUE,
    SUMMARIZE_PORTION_QUEUE,
    SUMMARIZE_PROJECT_QUEUE,
)

_CASTS = {"str": str, "int": int}


def _build(cls, job: str, data: dict):
    """Read a BullMQ payload using the generated field spec.

    A required field missing from the payload raises KeyError, which fails the
    job loudly — the alternative is a dataclass full of Nones that fails later,
    somewhere less informative.
    """
    kwargs = {}
    for payload_key, attribute, optional, cast in JOB_FIELDS[job]:
        if optional:
            value = data.get(payload_key)
            kwargs[attribute] = None if value is None else _CASTS[cast](value)
        else:
            kwargs[attribute] = _CASTS[cast](data[payload_key])
    return cls(**kwargs)


def _assert_matches(cls, job: str) -> None:
    """The dataclass and the generated spec must describe the same payload.

    Import-time rather than a test, because a mismatch here means the worker
    would mis-read every job of that type — better to refuse to start than to
    process a queue wrongly.
    """
    declared = {f.name for f in dataclass_fields(cls)}
    generated = {attribute for _key, attribute, _opt, _cast in JOB_FIELDS[job]}
    if declared != generated:
        raise RuntimeError(
            f"{cls.__name__} does not match the generated {job} contract: "
            f"only in dataclass {sorted(declared - generated)}, "
            f"only in generated.py {sorted(generated - declared)}. "
            "Regenerate with: npm run codegen -w @cdip/shared"
        )


@dataclass(frozen=True)
class SummarizeProjectJob:
    project_id: str

    @classmethod
    def from_payload(cls, data: dict) -> "SummarizeProjectJob":
        return _build(cls, "summarizeProject", data)


@dataclass(frozen=True)
class ScrapeRegionJob:
    """Apply the project's title-block region to its pages, then classify."""

    project_id: str
    region_version: int
    document_id: str | None = None

    @classmethod
    def from_payload(cls, data: dict) -> "ScrapeRegionJob":
        return _build(cls, "scrapeRegion", data)


@dataclass(frozen=True)
class SummarizePortionJob:
    """Summarize ONE discipline, because a user pressed its button."""

    project_id: str
    portion_id: str
    requested_by_id: str | None = None

    @classmethod
    def from_payload(cls, data: dict) -> "SummarizePortionJob":
        return _build(cls, "summarizePortion", data)


@dataclass(frozen=True)
class ProcessDocumentJob:
    project_id: str
    document_id: str
    spaces_key: str

    @classmethod
    def from_payload(cls, data: dict) -> "ProcessDocumentJob":
        return _build(cls, "processDocument", data)


for _cls, _job in (
    (ProcessDocumentJob, "processDocument"),
    (ScrapeRegionJob, "scrapeRegion"),
    (SummarizePortionJob, "summarizePortion"),
    (SummarizeProjectJob, "summarizeProject"),
):
    _assert_matches(_cls, _job)
