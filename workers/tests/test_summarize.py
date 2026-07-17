import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from summarize import (  # noqa: E402
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
