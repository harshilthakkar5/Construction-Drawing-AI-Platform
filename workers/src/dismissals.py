"""Dismissals remembered beyond one finding (rfi_dismissal_rules).

A dismissed finding already stays dismissed by its fingerprint. A RULE goes
further: the person said "never raise F6.0 again" (scope item) or "never raise
any R mark again" (scope family: the project's R7..R18 are insulation values,
not marks). Code checks drop findings a rule covers before saving, so they do
not come back on a rescan from scratch, on a targeted review, or as a new
fingerprint for the same item.

What an item IS per check lives in ITEM_FACT; the family rule is mirrored in
@cdip/shared `dismissalFamily`, and both read
packages/shared/fixtures/dismissal-keys.json.
"""

from __future__ import annotations

import re

# The fact that names what a finding is about.
ITEM_FACT = {
    "unscheduled_mark": "mark",
    "schedule_plan_loop": "mark",
    "tag_value_conflict": "tag",
    "dangling_reference": "missingSheet",
}
FAMILY_CHECKS = ("unscheduled_mark", "schedule_plan_loop")


def item_key(finding) -> str | None:
    fact = ITEM_FACT.get(finding.check_type)
    value = (finding.facts or {}).get(fact) if fact else None
    return str(value).strip().upper() or None if value else None


def same_item(a: str | None, b: str | None) -> bool:
    """One mark however it is printed: PC4 on one sheet is PC-4 on the next,
    and which form a finding carries depends on the sheet it met first."""
    def norm(s):
        return re.sub(r"[\s-]", "", (s or "").upper())
    return bool(a) and norm(a) == norm(b)


def family(check_type: str, key: str | None) -> str | None:
    if not key or check_type not in FAMILY_CHECKS:
        return None
    m = re.match(r"^\d*([A-Z]+)", key.upper())
    return m.group(1) if m else None


def load_rules(conn, project_id: str) -> list[tuple[str, str, str, str]]:
    """(checkType, scope, key, reason) for the project; none on a database
    that has not had the migration yet (the scan must still run)."""
    try:
        return [tuple(r) for r in conn.execute(
            'SELECT "checkType", scope, key, reason FROM rfi_dismissal_rules WHERE "projectId" = %s',
            (project_id,),
        ).fetchall()]
    except Exception:  # noqa: BLE001 - a missing table means no rules
        conn.rollback()
        return []


def covered(finding, rules) -> tuple[str, str, str] | None:
    """The (scope, key, reason) of the rule covering this finding, if any."""
    key = item_key(finding)
    if not key:
        return None
    fam = family(finding.check_type, key)
    for check_type, scope, rule_key, reason in rules:
        if check_type != finding.check_type:
            continue
        if (scope == "item" and same_item(rule_key, key)) or (scope == "family" and fam and rule_key.upper() == fam):
            return scope, rule_key, reason
    return None


def apply(findings: list, rules) -> tuple[list, list[str]]:
    """(findings no rule covers, one note naming what was left out)."""
    if not rules:
        return findings, []
    kept, left_out = [], []
    for f in findings:
        rule = covered(f, rules)
        if rule:
            left_out.append((item_key(f), rule))
        else:
            kept.append(f)
    if not left_out:
        return kept, []
    names = sorted({(f"{scope_key} marks" if scope == "family" else key) for key, (scope, scope_key, _r) in left_out})
    return kept, [f"{len(left_out)} finding(s) not raised again because you dismissed them before and asked "
                  f"for that to be remembered: {', '.join(names[:8])}{'…' if len(names) > 8 else ''}."]
