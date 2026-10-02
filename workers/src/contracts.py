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
    RFI_PACKAGE_QUEUE,
    RFI_FULL_SCAN_QUEUE,
    RFI_REVIEW_QUEUE,
    RFI_SCAN_QUEUE,
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
    detail: str | None = None

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
    detail: str | None = None

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


@dataclass(frozen=True)
class RfiScanJob:
    """Scan a project for RFI-worthy gaps, reporting into an rfi_scans row the
    API created — and the UI is already polling — before the job was queued."""

    project_id: str
    scan_id: str

    @classmethod
    def from_payload(cls, data: dict) -> "RfiScanJob":
        return _build(cls, "rfiScan", data)


@dataclass(frozen=True)
class RfiReviewJob:
    """Run one targeted RFI review. Only the run id travels: target, checks,
    thinking and the approved scope are read from the rfi_review_runs row, so
    a retry runs exactly the plan the person approved."""

    run_id: str

    @classmethod
    def from_payload(cls, data: dict) -> "RfiReviewJob":
        return _build(cls, "rfiReview", data)


@dataclass
class RfiPackageJob:
    """Render one marked-up RFI package; its items live on the rfi_packages row."""

    package_id: str

    @classmethod
    def from_payload(cls, data: dict) -> "RfiPackageJob":
        return _build(cls, "rfiPackage", data)


@dataclass
class RfiFullScanJob:
    """One step of a full AI scan: `plan` (catalogue, pairs, tiles, estimate)
    or `run` (the approved tiles to the model). The plan lives on the
    rfi_full_scans row, so a retry or a resume reads what was approved."""

    scan_id: str
    mode: str

    @classmethod
    def from_payload(cls, data: dict) -> "RfiFullScanJob":
        return _build(cls, "rfiFullScan", data)


for _cls, _job in (
    (ProcessDocumentJob, "processDocument"),
    (RfiFullScanJob, "rfiFullScan"),
    (RfiPackageJob, "rfiPackage"),
    (RfiScanJob, "rfiScan"),
    (RfiReviewJob, "rfiReview"),
    (ScrapeRegionJob, "scrapeRegion"),
    (SummarizePortionJob, "summarizePortion"),
    (SummarizeProjectJob, "summarizeProject"),
):
    _assert_matches(_cls, _job)
