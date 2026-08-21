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

        def project_roles(self, project_id):
            return []

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


class TestSectionTierSkip:
    """Sections bound the portion rollup's input. At or below SECTION_SIZE
    there is exactly one group, so the section call only restates the page
    summaries before the portion rollup restates them again — two paid calls
    to launder one summary."""

    def test_predicate(self):
        assert summarize.needs_section_tier(summarize.SECTION_SIZE + 1) is True
        assert summarize.needs_section_tier(summarize.SECTION_SIZE) is False
        assert summarize.needs_section_tier(1) is False
        assert summarize.needs_section_tier(0) is False

    def test_boundary_matches_group_sections(self):
        """The skip is only safe while ≤SECTION_SIZE really is one group."""
        for count in (1, summarize.SECTION_SIZE):
            pages = [{"combinedPage": i} for i in range(1, count + 1)]
            assert len(summarize.group_sections(pages)) == 1
        over = [{"combinedPage": i} for i in range(1, summarize.SECTION_SIZE + 2)]
        assert len(summarize.group_sections(over)) == 2


class TestRunPortionTiers:
    """End-to-end tier accounting for one discipline, with the DB and model
    faked out — the thing being asserted is how many paid calls happen."""

    PAGE_SUMMARY = {
        "overview": "Special inspections.",
        "items": [{"text": "Footings", "chunkIds": [ID_A], "page": 1}],
        "documentId": "doc1",
        "pageNumber": 1,
    }

    class _Db:
        def __init__(self, page_count):
            self.page_count = page_count
            self.inserted: list[str] = []
            self.statuses: list[str] = []

        # --- reads ---
        def project_exists(self, _):
            return True

        def project_roles(self, _):
            return []

        def project_portions(self, _):
            return [
                {
                    "id": "p1",
                    "name": "Architectural",
                    "discipline": "architectural",
                    "start_page": 1,
                    "end_page": self.page_count,
                    "summary_status": "queued",
                }
            ]

        def chunk_page_map(self, _):
            return {ID_A: 1}

        def page_index(self, _):
            return {
                ("doc1", n): {"combined": n, "discipline": "architectural"}
                for n in range(1, self.page_count + 1)
            }

        def pages_with_chunks(self, _):
            return [
                {
                    "document_id": "doc1",
                    "page_number": n,
                    "combined_page": n,
                    "chunks": [{"id": ID_A, "text": "t"}],
                }
                for n in range(1, self.page_count + 1)
            ]

        def existing_page_summaries(self, _):
            # Already summarized, so the page tier costs nothing here and the
            # only calls left are the rollups under test.
            return {("doc1", n): [ID_A] for n in range(1, self.page_count + 1)}

        def page_summaries(self, _):
            return [
                {**TestRunPortionTiers.PAGE_SUMMARY, "pageNumber": n}
                for n in range(1, self.page_count + 1)
            ]

        # --- writes ---
        def insert_summary(self, project_id, portion_id, level, summary, sources):
            self.inserted.append(level)

        def delete_portion_summaries(self, _):
            pass

        def set_portion_summary_text(self, *_):
            pass

        def set_portion_summary_status(self, portion_id, status, **kwargs):
            self.statuses.append(status)

        def mark_project_summary_stale(self, _):
            pass

    def _run(self, monkeypatch, page_count):
        db = self._Db(page_count)
        calls: list[str] = []
        monkeypatch.setitem(sys.modules, "db", db)
        monkeypatch.setitem(
            sys.modules,
            "cache",
            type("c", (), {"invalidate_summaries": staticmethod(lambda p: None)}),
        )
        monkeypatch.setattr(summarize, "_preflight", lambda _: None)

        def fake_rollup(kind, label, lower, chunk_pages):
            calls.append(kind)
            return {
                "overview": f"{kind} rollup",
                "items": [{"text": "Footings", "chunkIds": [ID_A], "page": 1}],
            }

        monkeypatch.setattr(summarize, "_rollup", fake_rollup)
        result = summarize.run_portion("proj", "p1")
        return db, calls, result

    def test_single_page_discipline_pays_for_one_rollup(self, monkeypatch):
        db, calls, result = self._run(monkeypatch, 1)
        assert calls == ["portion"]  # no section call at all
        assert db.inserted == ["portion"]
        assert result["sections"] == 0
        assert db.statuses[-1] == "ready"

    def test_large_discipline_keeps_the_section_tier(self, monkeypatch):
        db, calls, _ = self._run(monkeypatch, summarize.SECTION_SIZE + 1)
        assert calls.count("section") == 2  # 11 pages → two groups
        assert calls[-1] == "portion"
        assert db.inserted == ["section", "section", "portion"]

    def test_boundary_page_count_skips_sections(self, monkeypatch):
        _, calls, _ = self._run(monkeypatch, summarize.SECTION_SIZE)
        assert "section" not in calls


class TestSalvageRetry:
    """Every tier gets one retry. The rollups did not, so a single bad response
    dropped a whole discipline to _merge_lower — a mechanical concatenation of
    the level below, shown to the user in place of a real summary."""

    ITEM = {"text": "Footings bear on undisturbed soil.", "chunkIds": ["c1"]}
    GOOD = '{"overview": "Structural notes.", "items": [' + json.dumps(ITEM) + "]}"

    def _calls(self, monkeypatch, first: str):
        """Returns (result, retries sent). `first` is the response already in
        hand; _call_direct is only ever the RETRY, so it always answers well —
        the test measures how the salvage asks, not whether the model obliges."""
        sent = []

        def fake_direct(prompt, max_tokens=None):
            sent.append((prompt, max_tokens))
            return self.GOOD, None

        monkeypatch.setattr(summarize, "_call_direct", fake_direct)
        result = summarize._parse_or_retry(
            "PROMPT", first, {"c1"}, "the portion rollup for Structural"
        )
        return result, sent

    def test_a_good_response_never_retries(self, monkeypatch):
        result, sent = self._calls(monkeypatch, self.GOOD)
        assert result is not None and sent == []

    def test_a_truncated_answer_is_retried_with_more_room(self, monkeypatch):
        """Re-sending the identical request paid twice for the same cut-off
        JSON, so the retry must also ask for a shorter answer."""
        cut_off = '{"overview": "Structural notes.", "items": [{"text": "Foot'
        result, sent = self._calls(monkeypatch, cut_off)
        assert result is not None
        prompt, max_tokens = sent[0]
        assert max_tokens == summarize.MAX_TOKENS * 2
        assert "Keep it short" in prompt

    def test_a_malformed_answer_is_reminded_not_given_more_room(self, monkeypatch):
        result, sent = self._calls(monkeypatch, "Here is your JSON: not json")
        assert result is not None
        prompt, max_tokens = sent[0]
        assert max_tokens is None
        assert "ONLY the JSON object" in prompt

    def test_it_gives_up_after_one_retry(self, monkeypatch):
        monkeypatch.setattr(summarize, "_call_direct", lambda p, max_tokens=None: ("junk", None))
        assert summarize._parse_or_retry("PROMPT", "junk", {"c1"}, "label") is None


class TestRollupSalvage:
    def test_the_rollup_retries_before_falling_back_to_a_merge(self, monkeypatch):
        """The regression: gemini-3.1-pro-preview truncated the rollup and the
        discipline silently got a concatenation instead of a summary."""
        lower = [{"overview": "Page 1.", "items": [{"text": "A note.", "chunkIds": ["c1"]}]}]
        good = '{"overview": "Rolled up.", "items": [{"text": "A note.", "chunkIds": ["c1"]}]}'
        responses = iter([('{"overview": "cut', None), (good, None)])
        monkeypatch.setattr(summarize, "_call_direct", lambda p, max_tokens=None: next(responses))

        result = summarize._rollup("portion", "Structural", lower, {"c1": 7})
        assert result["overview"] == "Rolled up."

    def test_it_still_merges_when_the_retry_also_fails(self, monkeypatch):
        """The safety net stays: a failed rollup must never leave a discipline
        with no summary at all."""
        lower = [{"overview": "Page 1.", "items": [{"text": "A note.", "chunkIds": ["c1"]}]}]
        monkeypatch.setattr(summarize, "_call_direct", lambda p, max_tokens=None: ("junk", None))
        result = summarize._rollup("portion", "Structural", lower, {"c1": 7})
        assert result["overview"] == "Page 1."
        assert result["items"][0]["text"] == "A note."
