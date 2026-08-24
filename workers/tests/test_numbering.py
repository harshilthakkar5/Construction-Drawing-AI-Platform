"""The combined-numbering rule, against the fixture the TypeScript side reads.

FR-6's numbering is implemented twice — workers/src/numbering.py (mirroring the
recompute SQL) and apps/api/src/manifest.ts — because the renumber must happen
in one SQL statement under the project lock while the API has to resolve the
same mapping per request. It is also the rule that decides which page a
citation points at.

One fixture, both languages: change the rule on one side and the other side's
tests fail.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402
from numbering import combined_numbering, order_documents  # noqa: E402

FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "packages"
    / "shared"
    / "fixtures"
    / "combined-numbering.json"
)
CASES = json.loads(FIXTURE.read_text())["cases"]


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_matches_the_shared_fixture(case):
    result = combined_numbering(case["documents"])
    assert result == [tuple(entry) for entry in case["expected"]]


def test_the_fixture_is_actually_loaded():
    """A fixture that silently fails to load would make every case vacuous."""
    assert len(CASES) >= 5


class TestRuleProperties:
    """Properties the fixture cases are examples of — worth asserting directly,
    because these are the ones a well-meaning refactor breaks."""

    DOCS = [
        {"id": "b", "createdAt": "2026-01-02T00:00:00Z", "pages": 3},
        {"id": "a", "createdAt": "2026-01-01T00:00:00Z", "pages": 2},
    ]

    def test_numbers_are_continuous_from_one(self):
        combined = [c for _doc, _page, c in combined_numbering(self.DOCS)]
        assert combined == list(range(1, len(combined) + 1))

    def test_every_page_gets_exactly_one_number(self):
        entries = combined_numbering(self.DOCS)
        assert len({c for _d, _p, c in entries}) == len(entries)

    def test_pages_stay_in_order_within_a_document(self):
        pages = [p for doc, p, _c in combined_numbering(self.DOCS) if doc == "b"]
        assert pages == sorted(pages)

    def test_ordering_is_stable_across_repeated_calls(self):
        """Recompute runs after every document completes; an unstable sort
        would renumber the project — and invalidate every stored citation —
        each time."""
        same_time = [
            {"id": f"doc{i}", "createdAt": "2026-01-01T00:00:00Z", "pages": 1}
            for i in range(10)
        ]
        first = combined_numbering(same_time)
        assert all(combined_numbering(list(reversed(same_time))) == first for _ in range(3))

    def test_superseded_documents_do_not_shift_later_pages(self):
        """FR-4: replacing a 5-page drawing must not renumber the sheets after
        it, or every citation into them silently moves."""
        without = combined_numbering(self.DOCS)
        with_old = combined_numbering(
            [*self.DOCS, {"id": "old", "createdAt": "2026-01-01T12:00:00Z", "pages": 5,
                          "superseded": True}]
        )
        assert with_old == without


def test_order_documents_breaks_ties_by_id():
    ordered = order_documents(
        [
            {"id": "zzz", "createdAt": "2026-01-01T00:00:00Z", "pages": 1},
            {"id": "aaa", "createdAt": "2026-01-01T00:00:00Z", "pages": 1},
        ]
    )
    assert [d["id"] for d in ordered] == ["aaa", "zzz"]
