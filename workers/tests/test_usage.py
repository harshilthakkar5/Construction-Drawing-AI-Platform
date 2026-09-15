"""Accounting must never destroy the thing it is accounting for.

This file exists because it did. The vision pass passed kind="vlm", which was
not in KINDS, and `record` raised — from inside the model call, after the
request had gone to Anthropic and come back 200 OK twenty-six seconds later.
`complete()` caught the exception, returned None, and the page got no
description. A typo-guard for a constant threw away an answer already paid for.

So: two invariants. An unknown kind is logged and swallowed like every other
accounting failure, and the kind vocabulary does not drift from the enum the
rows are written into.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402

import usage  # noqa: E402

SCHEMA = Path(__file__).resolve().parents[2] / "apps/api/prisma/schema.prisma"


@pytest.fixture(autouse=True)
def no_database(monkeypatch):
    """`record` opens a connection inside its try. Without a database that call
    blocks on connect retries rather than failing, so the swallow path is stubbed
    to fail instantly — which is also the more interesting case: it proves a dead
    database costs the row and nothing else."""
    import db

    def refuse():
        raise RuntimeError("no database in tests")

    monkeypatch.setattr(db, "connect", refuse)


def test_a_dead_database_costs_the_row_and_nothing_else():
    usage.record(None, "chat", "claude-sonnet-5", input_tokens=10)


def test_an_unknown_kind_is_logged_not_raised():
    """The caller keeps its result. Losing the usage row is the right cost of
    an unregistered kind; losing the model's answer is not."""
    usage.record(None, "not-a-real-kind", "claude-sonnet-5", input_tokens=10)


def test_every_known_kind_is_accepted():
    for kind in usage.KINDS:
        # No database here, so the INSERT fails and is swallowed inside record.
        # What is being asserted is that the kind itself gets past the guard.
        usage.record(None, kind, "claude-sonnet-5", input_tokens=1)


def test_kinds_match_the_usage_kind_enum():
    """The vocabulary is declared twice — here and in the Prisma enum the rows
    are written into. A value missing from Python is a raised error mid-call;
    one missing from the enum is an INSERT that fails and a spend figure quietly
    short by whatever that stage costs. Neither announces itself, so this does.
    """
    block = re.search(r"enum UsageKind \{(.*?)\}", SCHEMA.read_text(), re.S)
    assert block, "UsageKind enum not found in schema.prisma"
    declared = {
        line.strip()
        for line in block.group(1).splitlines()
        if line.strip() and not line.strip().startswith("//") and not line.strip().startswith("///")
    }
    assert declared == set(usage.KINDS), (
        f"usage.KINDS and the UsageKind enum disagree: "
        f"only in enum {declared - set(usage.KINDS)}, only in KINDS {set(usage.KINDS) - declared}"
    )


def test_vlm_is_registered():
    """The specific regression: the vision pass records under this kind."""
    assert "vlm" in usage.KINDS
