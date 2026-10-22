"""rfi_review.library_code_findings: a review keeps only the code findings
that stand on its own sheets — except a finding about the SET, which no
choice of one to four sheets can contain."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import rfi_checks  # noqa: E402
import rfi_review  # noqa: E402
import rfi_scan  # noqa: E402
from rfi_checks import Finding  # noqa: E402


def finding(check_type: str, page: int) -> Finding:
    ev = [{"documentId": "d", "pageNumber": page, "role": "finding", "bbox": None}]
    return Finding(check_type, f"{check_type}:{page}", "high", "s", "q", ev)


def test_a_missing_level_is_kept_whatever_sheets_the_review_read(monkeypatch):
    # The review read page 5; the index and the plans either side of the gap are elsewhere.
    monkeypatch.setattr(rfi_scan, "load", lambda project_id: ([], []))
    monkeypatch.setattr(rfi_scan, "pinpoint_evidence", lambda *a: None)
    monkeypatch.setattr(rfi_checks, "run_all", lambda pages, chunks, _, only: (
        [finding(t, 9) for t in sorted(only)] + [finding(t, 5) for t in sorted(only)], []))
    scope = {"pages": [{"documentId": "d", "pageNumber": 5}]}
    found, _ = rfi_review.library_code_findings("p", scope, {"missing_plan_level", "dangling_reference"}, None)
    assert sorted(f.fingerprint for f in found) == [
        "dangling_reference:5", "missing_plan_level:5", "missing_plan_level:9",
    ]
