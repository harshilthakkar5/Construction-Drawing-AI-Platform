import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import summarize  # noqa: E402
from summarize import (  # noqa: E402
    _merge_lower,
    _rollup,
    attach_pages,
    collect_sources,
    group_sections,
    parse_summary_json,
)

ID_A = "11111111-aaaa-4bbb-8ccc-000000000001"
ID_B = "22222222-aaaa-4bbb-8ccc-000000000002"
ID_FAKE = "99999999-aaaa-4bbb-8ccc-000000000009"
ALLOWED = {ID_A, ID_B}


class TestParseSummaryJson:
    def test_valid(self):
        raw = json.dumps(
            {"overview": "Two structural notes.", "items": [{"text": "Footings 150 kPa", "chunkIds": [ID_A]}]}
        )
        result = parse_summary_json(raw, ALLOWED)
        assert result["overview"] == "Two structural notes."
        assert result["items"] == [{"text": "Footings 150 kPa", "chunkIds": [ID_A]}]

    def test_code_fences_stripped(self):
        raw = f'```json\n{{"overview": "x", "items": [{{"text": "t", "chunkIds": ["{ID_A}"]}}]}}\n```'
        assert parse_summary_json(raw, ALLOWED) is not None

    def test_invented_chunk_ids_dropped(self):
        raw = json.dumps(
            {"overview": "x", "items": [{"text": "real+fake", "chunkIds": [ID_A, ID_FAKE]}]}
        )
        result = parse_summary_json(raw, ALLOWED)
        assert result["items"][0]["chunkIds"] == [ID_A]

    def test_item_with_only_invented_ids_removed(self):
        raw = json.dumps(
            {
                "overview": "x",
                "items": [
                    {"text": "hallucinated", "chunkIds": [ID_FAKE]},
                    {"text": "grounded", "chunkIds": [ID_B]},
                ],
            }
        )
        result = parse_summary_json(raw, ALLOWED)
        assert [i["text"] for i in result["items"]] == ["grounded"]

    def test_garbage_rejected(self):
        assert parse_summary_json("not json", ALLOWED) is None
        assert parse_summary_json('{"items": "nope"}', ALLOWED) is None
        assert parse_summary_json('{"overview": "", "items": []}', ALLOWED) is None

    def test_item_cap(self):
        items = [{"text": f"i{i}", "chunkIds": [ID_A]} for i in range(20)]
        result = parse_summary_json(json.dumps({"overview": "x", "items": items}), ALLOWED)
        assert len(result["items"]) == 8


class TestAttachPages:
    def test_page_derived_from_first_cited_chunk(self):
        summary = {"overview": "x", "items": [{"text": "t", "chunkIds": [ID_B, ID_A]}]}
        attach_pages(summary, {ID_A: 3, ID_B: 7})
        assert summary["items"][0]["page"] == 3  # min of cited pages

    def test_items_without_known_pages_dropped(self):
        summary = {"overview": "x", "items": [{"text": "t", "chunkIds": [ID_A]}]}
        attach_pages(summary, {})
        assert summary["items"] == []


class TestGroupSections:
    def test_groups_of_ten(self):
        pages = [{"combinedPage": i} for i in range(1, 24)]
        groups = group_sections(pages)
        assert [len(g) for g in groups] == [10, 10, 3]

    def test_small_portion_single_section(self):
        assert len(group_sections([{"combinedPage": 1}])) == 1

    def test_empty(self):
        assert group_sections([]) == []


class TestCollectSources:
    def test_dedupes_in_order(self):
        summary = {
            "items": [
                {"text": "a", "chunkIds": [ID_A, ID_B]},
                {"text": "b", "chunkIds": [ID_B, ID_A]},
            ]
        }
        assert collect_sources(summary) == [ID_A, ID_B]


CHUNK_PAGES = {ID_A: 3, ID_B: 7}


class TestMergeLower:
    """The deterministic merge that keeps a failed model rollup from emptying
    every level above it."""

    def test_keeps_items_and_resolves_pages(self):
        lower = [
            {"overview": "Page 3.", "items": [{"text": "Footings", "chunkIds": [ID_A]}]},
            {"overview": "Page 7.", "items": [{"text": "Columns", "chunkIds": [ID_B]}]},
        ]
        merged = _merge_lower(lower, CHUNK_PAGES)
        assert merged["overview"] == "Page 3. Page 7."
        assert merged["items"] == [
            {"text": "Footings", "chunkIds": [ID_A], "page": 3},
            {"text": "Columns", "chunkIds": [ID_B], "page": 7},
        ]

    def test_drops_items_with_no_resolvable_source(self):
        lower = [{"overview": "o", "items": [{"text": "orphan", "chunkIds": [ID_FAKE]}]}]
        assert _merge_lower(lower, CHUNK_PAGES) == {"overview": "o", "items": []}

    def test_caps_items(self):
        lower = [
            {"overview": "", "items": [{"text": f"i{n}", "chunkIds": [ID_A]} for n in range(20)]}
        ]
        assert len(_merge_lower(lower, CHUNK_PAGES)["items"]) == 8

    def test_nothing_to_merge(self):
        assert _merge_lower([{"overview": "", "items": []}], CHUNK_PAGES) is None


class TestRollupFallback:
    def _lower(self):
        return [{"overview": "Page 3.", "items": [{"text": "Footings", "chunkIds": [ID_A]}]}]

    def test_uses_model_output_when_usable(self, monkeypatch):
        monkeypatch.setattr(
            summarize,
            "_call_direct",
            lambda prompt, max_tokens=None: (
                json.dumps(
                    {
                        "overview": "Rolled up.",
                        "items": [{"text": "Footings", "chunkIds": [ID_A]}],
                    }
                ),
                "end_turn",
            ),
        )
        result = _rollup("section", "pages 3–3", self._lower(), CHUNK_PAGES)
        assert result["overview"] == "Rolled up."

    def test_falls_back_on_invalid_json(self, monkeypatch):
        monkeypatch.setattr(
            summarize,
            "_call_direct",
            lambda prompt, max_tokens=None: ("sorry, I cannot", "end_turn"),
        )
        result = _rollup("section", "pages 3–3", self._lower(), CHUNK_PAGES)
        assert result == {
            "overview": "Page 3.",
            "items": [{"text": "Footings", "chunkIds": [ID_A], "page": 3}],
        }

    def test_falls_back_when_model_cites_nothing(self, monkeypatch):
        monkeypatch.setattr(
            summarize,
            "_call_direct",
            lambda prompt, max_tokens=None: (
                json.dumps({"overview": "", "items": [{"text": "x", "chunkIds": []}]}),
                "end_turn",
            ),
        )
        result = _rollup("portion", "the Structural portion", self._lower(), CHUNK_PAGES)
        assert result["items"][0]["page"] == 3

    def test_no_citable_ids_skips_the_call(self):
        lower = [{"overview": "Uncited overview.", "items": []}]
        # _call_direct is untouched: calling it would raise (no API key configured).
        assert _rollup("section", "l", lower, CHUNK_PAGES) == {
            "overview": "Uncited overview.",
            "items": [],
        }


class TestUserApprovalGuard:
    """Summaries cost real tokens, so a job may only run when a user asked.
    The API sets the portion to 'queued' when the button is pressed; anything
    else reaching the worker — a job left in Redis by an older build, a replay,
    a hand-rolled enqueue — must be discarded rather than quietly spend money.
    """

    class _FakeDb:
        def __init__(self, status):
            self.portion = {
                "id": "p1",
                "name": "Structural",
                "discipline": "structural",
                "start_page": 1,
                "end_page": 1,
                "summary_status": status,
            }
            self.statuses: list[tuple] = []
            self.summarized = False

        def project_exists(self, project_id):
            return True

        def project_portions(self, project_id):
            return [self.portion]

        def set_portion_summary_status(self, portion_id, status, **kwargs):
            self.statuses.append((status, kwargs))

        def chunk_page_map(self, project_id):
            self.summarized = True  # first thing the real run touches
            return {}

        def page_index(self, project_id):
            return {}

        def pages_with_chunks(self, project_id):
            return []

    def _run(self, monkeypatch, status, **kwargs):
        fake = self._FakeDb(status)
        monkeypatch.setitem(sys.modules, "db", fake)
        monkeypatch.setattr(summarize, "_preflight", lambda project_id: None)
        monkeypatch.setitem(sys.modules, "cache", type("c", (), {"invalidate_summaries": staticmethod(lambda p: None)}))
        return fake, summarize.run_portion("proj", "p1", **kwargs)

    def test_unrequested_portion_is_discarded(self, monkeypatch):
        fake, result = self._run(monkeypatch, "none")
        assert result == {"skipped": "not requested by a user"}
        assert fake.summarized is False  # no model call was even set up

    def test_ready_portion_is_not_resummarized_by_a_stray_job(self, monkeypatch):
        _, result = self._run(monkeypatch, "ready")
        assert result == {"skipped": "not requested by a user"}

    def test_queued_portion_proceeds(self, monkeypatch):
        fake, result = self._run(monkeypatch, "queued")
        assert result != {"skipped": "not requested by a user"}
        assert ("running", {}) in fake.statuses

    def test_admin_rebuild_bypasses_the_guard(self, monkeypatch):
        fake, result = self._run(monkeypatch, "none", requested=True)
        assert result != {"skipped": "not requested by a user"}
        assert ("running", {}) in fake.statuses
