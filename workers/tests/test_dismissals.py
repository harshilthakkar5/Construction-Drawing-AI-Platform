"""Dismissals remembered for an item or a mark family (dismissals.py)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import dismissals  # noqa: E402
from rfi_checks import Finding  # noqa: E402

FIXTURE = Path(__file__).resolve().parents[2] / "packages/shared/fixtures/dismissal-keys.json"


def _f(check, fact=None, value=None, fp="fp"):
    return Finding(check_type=check, fingerprint=fp, confidence="high", subject="s", question="q",
                   evidence=[], facts={fact: value} if fact else {})


def test_family_matches_the_shared_fixture():
    for check, key, fam in json.loads(FIXTURE.read_text())["cases"]:
        assert dismissals.family(check, key) == fam, (check, key)


def test_family_checks_match_the_typescript():
    ts = (Path(__file__).resolve().parents[2] / "packages/shared/src/index.ts").read_text()
    line = next(line for line in ts.splitlines() if "DISMISS_FAMILY_CHECKS" in line and "=" in line)
    assert all(f'"{c}"' in line for c in dismissals.FAMILY_CHECKS)


def test_item_key_per_check():
    assert dismissals.item_key(_f("unscheduled_mark", "mark", "f6.0")) == "F6.0"
    assert dismissals.item_key(_f("tag_value_conflict", "tag", "RTU-3")) == "RTU-3"
    assert dismissals.item_key(_f("dangling_reference", "missingSheet", "S-501")) == "S-501"
    assert dismissals.item_key(_f("grid_mismatch", "mark", "F1")) is None  # no item to remember


def test_an_item_rule_leaves_out_that_item_only():
    rules = [("unscheduled_mark", "item", "F6.0", "answered")]
    kept, notes = dismissals.apply(
        [_f("unscheduled_mark", "mark", "F6.0", "a"), _f("unscheduled_mark", "mark", "F7.0", "b"),
         _f("schedule_plan_loop", "mark", "F6.0", "c")], rules)
    assert [f.fingerprint for f in kept] == ["b", "c"]  # another mark, and another check, stay
    assert notes and "F6.0" in notes[0]


def test_a_family_rule_leaves_out_every_mark_of_the_family():
    rules = [("unscheduled_mark", "family", "R", "wrong_item")]
    kept, notes = dismissals.apply(
        [_f("unscheduled_mark", "mark", "R7", "a"), _f("unscheduled_mark", "mark", "R18", "b"),
         _f("unscheduled_mark", "mark", "RB2", "c")], rules)
    assert [f.fingerprint for f in kept] == ["c"]  # RB is another family
    assert "R marks" in notes[0]


def test_no_rules_changes_nothing():
    findings = [_f("unscheduled_mark", "mark", "F6.0")]
    assert dismissals.apply(findings, []) == (findings, [])
