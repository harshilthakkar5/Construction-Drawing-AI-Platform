"""Score a targeted RFI review run against RFIs a person really issued.

    python benchmarks/rfi_eval.py --run <review run id> [--case rfi-002]
    python benchmarks/rfi_eval.py --scan <rfi scan id> --case fp-s8-door-schedule
    python benchmarks/rfi_eval.py --fullscan <full scan id> --case rfi-002,rfi-015,fp-a303-a305-levels
    python benchmarks/rfi_eval.py --project-reviews <project id> --case leland-rfi-001,leland-rfi-007

`--project-reviews` scores every "By element" review of a project together:
the latest finished run of each element, the candidates those runs saved, and
the ones they reported "found again" (already on file from a scan or an
earlier review). A By element finding is filed under its LIBRARY question
(CE-05-01-Q03), so a case lists the `elements` whose questions count as its
check. It also says, per case, whether one of those elements answered a
question "no issue" while the RFI went unfound — the review looked and missed,
which is worse than "not enough evidence" — and how many proposals the guards
rejected or overruled across the runs.

Reads the run's candidates from Postgres (DATABASE_URL) and, for each expected
finding in the case, reports whether ONE candidate matches all of it: its check,
every sheet named in its evidence, and every `a = b` pair in its wording.
Candidates that match nothing are LISTED, never counted as wrong — a sheet can
carry a real problem nobody wrote an RFI for, and the benchmark may report bad
news but never invent it.

A case may also list REJECTED findings: candidates a person reviewed and
judged wrong, with the reason. Those are the one exception to "never counted
as wrong" — a person has already said so — and a run that raises one again is
a false positive and fails. Four were added after the client tested the first
drafts (A3.03 vs A3.05 at two levels; S1.102 vs A3.36 at two scales; S8, a
screw type, against a door schedule; SR-25 against a level schedule).

A full AI scan covers the whole project, so several cases can be scored
against it at once (`--case a,b,c`); it passes only if every one does. This is
the measurement RFI_FULL_SCAN=on waits for: until a full scan of a real set
finds the issued RFIs and raises none of the rejected ones, the feature stays
labelled beta.

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


def code_check_types() -> set[str]:
    """Every check the CODE decides (rfi_checks.CHECK_TYPES), never a model's."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "workers" / "src"))
    import rfi_checks

    return set(rfi_checks.CHECK_TYPES) | set(rfi_checks.REVIEW_CHECK_TYPES)


def code_only(candidates: list[dict], types: set[str]) -> list[dict]:
    """The candidates a code check made. Pure. Scoring these alone measures the
    checks without the model — e.g. a run whose AI calls went to a stand-in."""
    return [c for c in candidates if c["checkType"] in types]


def _norm(sheet: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", sheet.upper())


def _pair(a: str, b: str, text: str) -> bool:
    def one(x: str, y: str) -> bool:
        return re.search(rf"(?<![\w.]){re.escape(x)}\s*=\s*{re.escape(y)}(?![\w.])", text) is not None

    return one(a, b) or one(b, a)


def matches(expected: dict, candidate: dict, elements: tuple[str, ...] = ()) -> list[str]:
    """What this candidate is missing for this expectation; [] is a match.
    `elements`: library elements whose questions (CE-05-01-Q03) count too —
    and so does a project's own question (checkType "custom") asked in a review
    of one of them, since the test guide's second pass is exactly that."""
    missing = []
    if candidate["checkType"] not in expected["checkTypes"] and not _by_element(candidate, elements):
        missing.append(f"check {candidate['checkType']} is not {'/'.join(expected['checkTypes'])}")
    named = {_norm(e.get("sheetNumber") or "") for e in candidate.get("evidence") or []}
    for sheet in expected.get("sheets", []):
        if _norm(sheet) not in named:
            missing.append(f"no evidence on {sheet}")
    text = f"{candidate['subject']}\n{candidate['question']}"
    for a, b in expected.get("pairs", []):
        if not _pair(a, b, text):
            missing.append(f"does not say {a} = {b}")
    for m in expected.get("mentions", []):
        # "pile|pier": any one of them (Leland's drawings say pier, its RFIs pile).
        if not any(_norm(alt) in _norm(text) for alt in m.split("|")):
            missing.append(f"does not mention {m}")
    return missing


def _by_element(candidate: dict, elements: tuple[str, ...]) -> bool:
    return any(candidate["checkType"].startswith(f"{e}-") for e in elements) or (
        candidate["checkType"] == "custom" and candidate.get("element") in elements)


def is_rejected(rejected: dict, candidate: dict, elements: tuple[str, ...] = ()) -> bool:
    """Whether this candidate is a finding a person already judged wrong: the
    same check (or a question of the case's element), evidence on every named
    sheet, and every named mark in its wording."""
    if candidate["checkType"] not in rejected["checkTypes"] and not _by_element(candidate, elements):
        return False
    named = {_norm(e.get("sheetNumber") or "") for e in candidate.get("evidence") or []}
    if any(_norm(sheet) not in named for sheet in rejected.get("sheets", [])):
        return False
    text = _norm(f"{candidate['subject']} {candidate['question']}")
    return all(_norm(m) in text for m in rejected.get("mentions", []))


def score(case: dict, candidates: list[dict], elements: tuple[str, ...] = ()) -> dict:
    found, used = [], set()
    for exp in case["expected"]:
        hit = next((c for c in candidates if not matches(exp, c, elements)), None)
        if hit is not None:
            used.add(hit["id"])
        closest = min(candidates, key=lambda c: len(matches(exp, c, elements)), default=None)
        found.append(
            {
                "what": exp["what"],
                "hit": hit is not None,
                "candidate": hit["subject"] if hit else None,
                "closestMissing": [] if hit or closest is None else matches(exp, closest, elements),
            }
        )
    false_positives = []
    for rej in case.get("rejected", []):
        for c in candidates:
            if c["id"] not in used and is_rejected(rej, c, elements):
                used.add(c["id"])
                false_positives.append({"what": rej["what"], "why": rej["why"], "candidate": c["subject"]})
    return {
        "case": case["id"],
        "expected": len(case["expected"]),
        "hits": sum(f["hit"] for f in found),
        "findings": found,
        "falsePositives": false_positives,
        "unmatched": [c["subject"] for c in candidates if c["id"] not in used],
    }


def source_column(run_id: str | None = None, scan_id: str | None = None, full_scan_id: str | None = None) -> tuple[str, str]:
    """Which candidate column names the source being scored. Exactly one."""
    given = [(c, v) for c, v in (('"reviewRunId"', run_id), ('"scanId"', scan_id), ('"fullScanId"', full_scan_id)) if v]
    if len(given) != 1:
        raise ValueError("score exactly one run, scan or full scan")
    return given[0]


def load_candidates(run_id: str | None = None, scan_id: str | None = None, full_scan_id: str | None = None) -> list[dict]:
    import psycopg

    column, value = source_column(run_id, scan_id, full_scan_id)
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        rows = conn.execute(
            f'SELECT id, "checkType", subject, question, evidence FROM rfi_candidates WHERE {column} = %s',
            (value,),
        ).fetchall()
    return [dict(zip(("id", "checkType", "subject", "question", "evidence"), r)) for r in rows]


_FOUND_AGAIN = re.compile(r"Found again, not added twice: “(.+?)” is")


def element_runs(rows: list[tuple]) -> dict[str, dict]:
    """The latest finished By element run per element. rows: (id, elementId,
    status, notes, checkResults, createdAt), any order. Pure."""
    latest: dict[str, dict] = {}
    for run_id, element, status, notes, results, created in rows:
        if not element or status not in ("ready", "partial"):
            continue
        if element not in latest or created > latest[element]["createdAt"]:
            latest[element] = {"id": run_id, "notes": notes or [], "checkResults": results or {}, "createdAt": created}
    return latest


def guard_tally(runs: dict[str, dict]) -> dict[str, int]:
    """Proposals the guards turned away, and rejections overruled, from the
    runs' notes. Pure."""
    proposed = kept = overruled = 0
    for run in runs.values():
        for note in run["notes"]:
            m = re.match(r"Verification kept (\d+) of (\d+)", note)
            if m:
                kept, proposed = kept + int(m[1]), proposed + int(m[2])
            if note.startswith("Kept ") and "could not show where" in note:
                overruled += 1
    return {"proposed": proposed, "kept": kept, "rejected": proposed - kept, "overruled": overruled}


def missed_with_no_issue(case: dict, runs: dict[str, dict]) -> list[str]:
    """The case's elements that answered a question "no issue": when the RFI
    was not found, those answers are wrong. Pure."""
    out = []
    for element in case.get("elements", []):
        run = runs.get(element)
        if run is None:
            continue
        ids = [q for q, r in run["checkResults"].items() if (r or {}).get("outcome") == "complete_no_issue"]
        if ids:
            out.append(f"{element}: {len(ids)} question(s) said no issue ({', '.join(sorted(ids)[:6])})")
    return out


def load_project_reviews(project_id: str) -> tuple[dict[str, dict], list[dict]]:
    """Each element's latest By element run, and every candidate they saved
    or reported found again."""
    import psycopg

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        rows = conn.execute(
            """SELECT id, scope->'library'->'element'->>'id', status, notes, "checkResults", "createdAt"
               FROM rfi_review_runs WHERE "projectId" = %s AND scope ? 'library'""",
            (project_id,),
        ).fetchall()
        runs = element_runs(rows)
        # Found again: by fingerprint (runs record it per question), else by the
        # note's wording — runs made before the fingerprint was recorded.
        again_fps = sorted({fp for r in runs.values() for res in r["checkResults"].values() for fp in (res or {}).get("foundAgain") or []})
        again = sorted({m[1] for r in runs.values() for n in r["notes"] for m in [_FOUND_AGAIN.match(n)] if m})
        element_of = {run_id: element for run_id, element, *_ in rows}
        found = conn.execute(
            """SELECT id, "checkType", subject, question, evidence, "reviewRunId" FROM rfi_candidates
               WHERE "projectId" = %s
                 AND ("reviewRunId" = ANY(%s::text[]) OR fingerprint = ANY(%s::text[]) OR subject = ANY(%s::text[]))""",
            (project_id, [r["id"] for r in runs.values()], again_fps, again),
        ).fetchall()
    keys = ("id", "checkType", "subject", "question", "evidence")
    return runs, [dict(zip(keys, r[:5]), element=element_of.get(r[5])) for r in found]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--run", help="rfi_review_runs.id to score")
    source.add_argument("--scan", help="rfi_scans.id to score (the project-wide checks)")
    source.add_argument("--fullscan", help="rfi_full_scans.id to score (the full AI scan)")
    source.add_argument("--project-reviews", help="projects.id: score every By element review of the project")
    parser.add_argument("--case", default="rfi-002", help="a case id, or several separated by commas")
    parser.add_argument("--code-only", action="store_true",
                        help="score only findings a code check made, not the model's (an offline or stub run)")
    args = parser.parse_args()
    cases = {c["id"]: c for c in json.loads(CASES.read_text())["cases"]}
    wanted = [c.strip() for c in args.case.split(",") if c.strip()]
    unknown = [c for c in wanted if c not in cases]
    if unknown:
        print(f"no case {', '.join(map(repr, unknown))}; have {', '.join(cases)}", file=sys.stderr)
        return 2
    if args.project_reviews:
        runs, candidates = load_project_reviews(args.project_reviews)
        if args.code_only:
            candidates = code_only(candidates, code_check_types())
        print(f"{len(runs)} element(s) reviewed, {len(candidates)} candidate(s) saved or found again")
        failed = 0
        for case_id in wanted:
            case = cases[case_id]
            result = score(case, candidates, tuple(case.get("elements", [])))
            failed += report(result, len(candidates), show_unmatched=False)
            if result["hits"] < result["expected"]:
                for line in missed_with_no_issue(case, runs):
                    print(f"  LOOKED AND MISSED {line}")
                for element in case.get("elements", []):
                    if element not in runs:
                        print(f"  NOT RUN {element}: no finished By element review of it")
        t = guard_tally(runs)
        print(f"Guards: {t['proposed']} proposed by the AI, {t['kept']} kept by verification, "
              f"{t['rejected']} rejected, {t['overruled']} rejection(s) overruled")
        return 1 if failed else 0
    candidates = load_candidates(args.run, args.scan, args.fullscan)
    if args.code_only:
        candidates = code_only(candidates, code_check_types())
    failed = 0
    for case_id in wanted:
        failed += report(score(cases[case_id], candidates), len(candidates))
    return 1 if failed else 0


def report(result: dict, total: int, show_unmatched: bool = True) -> int:
    """Print one case's score; 1 when it did not pass."""
    print(f"{result['case']}: {result['hits']} of {result['expected']} expected finding(s) found "
          f"among {total} candidate(s)")
    for f in result["findings"]:
        print(f"  {'HIT ' if f['hit'] else 'MISS'} {f['what']}")
        if f["hit"]:
            print(f"       as: {f['candidate']}")
        elif f["closestMissing"]:
            print(f"       closest candidate: {'; '.join(f['closestMissing'])}")
    for fp in result["falsePositives"]:
        print(f"  FALSE POSITIVE {fp['what']}\n       as: {fp['candidate']}\n       why it is wrong: {fp['why']}")
    if result["unmatched"] and show_unmatched:
        print(f"  {len(result['unmatched'])} other candidate(s), not scored:")
        for subject in result["unmatched"]:
            print(f"       - {subject}")
    return 0 if result["hits"] == result["expected"] and not result["falsePositives"] else 1


if __name__ == "__main__":
    sys.exit(main())
