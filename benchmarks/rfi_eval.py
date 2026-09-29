"""Score a targeted RFI review run against RFIs a person really issued.

    python benchmarks/rfi_eval.py --run <review run id> [--case rfi-002]

Reads the run's candidates from Postgres (DATABASE_URL) and, for each expected
finding in the case, reports whether ONE candidate matches all of it: its check,
every sheet named in its evidence, and every `a = b` pair in its wording.
Candidates that match nothing are LISTED, never counted as wrong — a sheet can
carry a real problem nobody wrote an RFI for, and the benchmark may report bad
news but never invent it.

The issued RFI is the expected OUTPUT only. Nothing here is passed to the
review; feeding a reference RFI to the model would measure recall of the
answer key.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

CASES = Path(__file__).with_name("rfi_eval_cases.json")


def _norm(sheet: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", sheet.upper())


def _pair(a: str, b: str, text: str) -> bool:
    def one(x: str, y: str) -> bool:
        return re.search(rf"(?<![\w.]){re.escape(x)}\s*=\s*{re.escape(y)}(?![\w.])", text) is not None

    return one(a, b) or one(b, a)


def matches(expected: dict, candidate: dict) -> list[str]:
    """What this candidate is missing for this expectation; [] is a match."""
    missing = []
    if candidate["checkType"] not in expected["checkTypes"]:
        missing.append(f"check {candidate['checkType']} is not {'/'.join(expected['checkTypes'])}")
    named = {_norm(e.get("sheetNumber") or "") for e in candidate.get("evidence") or []}
    for sheet in expected.get("sheets", []):
        if _norm(sheet) not in named:
            missing.append(f"no evidence on {sheet}")
    text = f"{candidate['subject']}\n{candidate['question']}"
    for a, b in expected.get("pairs", []):
        if not _pair(a, b, text):
            missing.append(f"does not say {a} = {b}")
    return missing


def score(case: dict, candidates: list[dict]) -> dict:
    found, used = [], set()
    for exp in case["expected"]:
        hit = next((c for c in candidates if not matches(exp, c)), None)
        if hit is not None:
            used.add(hit["id"])
        closest = min(candidates, key=lambda c: len(matches(exp, c)), default=None)
        found.append(
            {
                "what": exp["what"],
                "hit": hit is not None,
                "candidate": hit["subject"] if hit else None,
                "closestMissing": [] if hit or closest is None else matches(exp, closest),
            }
        )
    return {
        "case": case["id"],
        "expected": len(case["expected"]),
        "hits": sum(f["hit"] for f in found),
        "findings": found,
        "unmatched": [c["subject"] for c in candidates if c["id"] not in used],
    }


def load_candidates(run_id: str) -> list[dict]:
    import psycopg

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        rows = conn.execute(
            'SELECT id, "checkType", subject, question, evidence FROM rfi_candidates WHERE "reviewRunId" = %s',
            (run_id,),
        ).fetchall()
    return [dict(zip(("id", "checkType", "subject", "question", "evidence"), r)) for r in rows]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--run", required=True, help="rfi_review_runs.id to score")
    parser.add_argument("--case", default="rfi-002")
    args = parser.parse_args()
    cases = {c["id"]: c for c in json.loads(CASES.read_text())["cases"]}
    if args.case not in cases:
        print(f"no case {args.case!r}; have {', '.join(cases)}", file=sys.stderr)
        return 2
    candidates = load_candidates(args.run)
    result = score(cases[args.case], candidates)
    print(f"{result['case']}: {result['hits']} of {result['expected']} expected finding(s) found "
          f"among {len(candidates)} candidate(s)")
    for f in result["findings"]:
        print(f"  {'HIT ' if f['hit'] else 'MISS'} {f['what']}")
        if f["hit"]:
            print(f"       as: {f['candidate']}")
        elif f["closestMissing"]:
            print(f"       closest candidate: {'; '.join(f['closestMissing'])}")
    if result["unmatched"]:
        print(f"  {len(result['unmatched'])} other candidate(s), not scored:")
        for subject in result["unmatched"]:
            print(f"       - {subject}")
    return 0 if result["hits"] == result["expected"] else 1


if __name__ == "__main__":
    sys.exit(main())
