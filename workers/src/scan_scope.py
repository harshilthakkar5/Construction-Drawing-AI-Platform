"""What one "Scan all drawings" run compares and asks. Pure.

The scope travels as JSON on rfi_scans.scope and rfi_full_scans.scope
(RfiScanScopeDto in @cdip/shared, validated and normalised by the API's
rfiScanScope.ts). None means everything — every scan before scoped scans
existed — and every function here treats it so, which is what keeps an
unscoped scan byte-for-byte the scan it always was.

Three decisions live here so the code checks, the plan and the AI comparison
cannot disagree about them:
  * which sheet PAIRS belong to the comparison (`pair_in`), and which SHEETS
    (`facts_in`) — "Structural vs Architectural" pairs one structural plan
    with one architectural plan and nothing else;
  * which code CHECKS run (`check_types`) — the whole-project text checks
    when ticked, and the code check behind each question asked;
  * which QUESTIONS the AI is asked (`questions`) — the ticked catalogue
    entries plus the person's own, each with a stable id.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from generated import (
    RFI_CUSTOM_QUESTION_CHARS,
    RFI_CUSTOM_QUESTION_IDS,
    RFI_PROJECT_CHECK_TYPES,
    RFI_QUESTION_CODE_CHECKS,
    RFI_REVIEW_CHECKS,
)

ALL = "all"
CATALOGUE = {c["id"]: c for c in RFI_REVIEW_CHECKS}
CATALOGUE_IDS = tuple(c["id"] for c in RFI_REVIEW_CHECKS)
# Every check type a scan can run: the project text checks and the code
# checks behind the questions (grid naming and spacing, the column overlay).
QUESTION_CHECK_TYPES = frozenset(t for types in RFI_QUESTION_CODE_CHECKS.values() for t in types)
ALL_CHECK_TYPES = frozenset(RFI_PROJECT_CHECK_TYPES) | QUESTION_CHECK_TYPES


@dataclass(frozen=True)
class Scope:
    a: str = ALL
    b: str = ALL
    check_ids: tuple[str, ...] = CATALOGUE_IDS
    custom: tuple[str, ...] = ()
    project_checks: bool = True
    # True only for a scope read from a row: None (everything) builds the
    # default, which must not claim to be narrowed.
    stored: bool = field(default=False, compare=False)

    @property
    def everything(self) -> bool:
        return (self.a == ALL and self.b == ALL and set(self.check_ids) == set(CATALOGUE_IDS)
                and not self.custom and self.project_checks)

    @property
    def disciplines(self) -> set[str] | None:
        """The disciplines named, or None when either side is every discipline."""
        if self.a == ALL or self.b == ALL:
            return None
        return {self.a, self.b}


EVERYTHING = Scope()


def parse(raw) -> Scope:
    """A stored scope, or EVERYTHING for none. Defensive: the API validated
    it, but a row is data, and an unreadable one must not narrow a scan."""
    if not isinstance(raw, dict):
        return EVERYTHING
    a = raw.get("disciplineA") if isinstance(raw.get("disciplineA"), str) else ALL
    b = raw.get("disciplineB") if isinstance(raw.get("disciplineB"), str) else ALL
    if a == ALL and b != ALL:
        a, b = b, a
    ids = raw.get("checkIds")
    check_ids = CATALOGUE_IDS if not isinstance(ids, list) else tuple(i for i in CATALOGUE_IDS if i in ids)
    custom = tuple(
        " ".join(q.split())[:RFI_CUSTOM_QUESTION_CHARS]
        for q in (raw.get("customQuestions") or [])
        if isinstance(q, str) and q.strip()
    )[: len(RFI_CUSTOM_QUESTION_IDS)]
    return Scope(a, b, check_ids, custom, raw.get("projectChecks") is not False, stored=True)


# --- Sheets and pairs -------------------------------------------------------------


def side_has(side: str, discipline: str | None) -> bool:
    return side == ALL or discipline == side


def pair_in(scope: Scope, da: str | None, db: str | None) -> bool:
    """Whether two sheets of these disciplines are a pair this scan compares.

      all  / all          every pair (the unscoped scan)
      X    / all          every pair with an X sheet on either side
      X    / X            two X sheets (an enlarged plan with its overall)
      X    / Y            one X sheet and one Y sheet, either way round
    """
    if scope.a == ALL and scope.b == ALL:
        return True
    if scope.b == ALL:
        return scope.a in (da, db)
    if scope.a == scope.b:
        return da == db == scope.a
    return {da, db} == {scope.a, scope.b}


def facts_in(scope: Scope, facts: list) -> tuple[list, int]:
    """(the sheets this scan pairs from, how many were left out as another
    discipline). With both sides named, only those disciplines' sheets are
    paired at all — so "why was this plan not paired" is answered against
    the chosen disciplines, not against every consultant in the set."""
    named = scope.disciplines
    if named is None:
        return list(facts), 0
    kept = [f for f in facts if f.discipline in named]
    return kept, len(facts) - len(kept)


def finding_in(scope: Scope, disciplines: set[str | None]) -> bool:
    """Whether a code finding whose evidence sits on sheets of these
    disciplines belongs to this scan — what it shows, and what its re-run may
    remove as resolved. A finding on one discipline's sheets belongs when that
    discipline is a chosen side; one across two must be a pair this scan
    compares (a merged naming dispute may list a consultant sheet as well)."""
    if scope.a == ALL and scope.b == ALL:
        return True
    known = {d for d in disciplines if d}
    if not known:
        return False
    if scope.b == ALL:
        return scope.a in known
    if scope.a == scope.b:
        return known == {scope.a}
    if len(known) == 1:
        return known <= {scope.a, scope.b}
    return scope.a in known and scope.b in known


# --- Checks ------------------------------------------------------------------------


def check_types(scope: Scope) -> set[str]:
    """The code checks this scan runs."""
    if scope.everything:
        return set(ALL_CHECK_TYPES)
    out = set(RFI_PROJECT_CHECK_TYPES) if scope.project_checks else set()
    for check_id in scope.check_ids:
        out |= set(RFI_QUESTION_CODE_CHECKS.get(check_id) or ())
    return out


# --- Questions ---------------------------------------------------------------------


def custom_fingerprint_id(text: str) -> str:
    """A custom question's identity across scans: its words, not its slot.
    "Q1" is whatever was typed first THIS time, so a fingerprint built on it
    would merge two different questions' findings."""
    return "custom-" + hashlib.sha256(" ".join(text.lower().split()).encode()).hexdigest()[:10]


def questions(scope: Scope) -> list[dict]:
    """[{id, label, question, rules, custom}] in the order asked: the catalogue
    questions, then the person's own as Q1..Q5."""
    out = []
    for check_id in scope.check_ids:
        c = CATALOGUE[check_id]
        out.append({
            "id": check_id,
            "label": c["label"],
            "question": c["originalQuestion"],
            "rules": f"{c['comparisonRules']} {c['candidateRules']}",
            "custom": False,
        })
    for qid, text in zip(RFI_CUSTOM_QUESTION_IDS, scope.custom):
        out.append({"id": qid, "label": text, "question": text, "rules": "", "custom": True})
    return out


def prompt_extra_tokens(scope: Scope) -> int:
    """Roughly how many more system-prompt tokens a narrowed scan's first
    look carries than the unscoped one (fullscan_run.question_block spells out
    what each chosen question asks and when it is a problem). It is in every
    first-look call, so the plan's price has to include it."""
    if scope.everything:
        return 0
    extra = 60
    for q in questions(scope):
        extra += (len(q["question"]) + len(q["rules"])) // 4 + 12
    return extra


def describe(scope: Scope, labels: dict[str, str] | None = None) -> str:
    """"Structural vs Architectural, 4 questions + 1 of yours" for notes."""
    name = (labels or {}).get
    label = lambda s: "every discipline" if s == ALL else (name(s) or s.replace("_", " ").title())  # noqa: E731
    if scope.a == ALL and scope.b == ALL:
        pair = "every discipline"
    elif scope.b == ALL:
        pair = f"{label(scope.a)} against every discipline"
    elif scope.a == scope.b:
        pair = f"{label(scope.a)} within itself"
    else:
        pair = f"{label(scope.a)} vs {label(scope.b)}"
    n = len(scope.check_ids)
    own = f" + {len(scope.custom)} of yours" if scope.custom else ""
    return f"{pair}, {n} question{'' if n == 1 else 's'}{own}"
