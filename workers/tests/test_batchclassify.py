"""Rules-first pre-pass + batched sheet reading.

Two changes with one goal — stop paying a provider round trip per page — and
two very different risks:

  * The pre-pass is a PRECISION problem. It sits in front of the model, so a
    string it resolves is a string the model never sees; being wrong there is
    silent. Most of these tests are about what it must REFUSE to answer.
  * Batching is an ALIGNMENT problem. Twenty-five answers come back in one
    response and each has to land on the page it belongs to. A drifted or
    truncated answer must degrade to "unresolved", never to a confident wrong
    discipline.
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import classify  # noqa: E402
import pytest  # noqa: E402


class FakeRedis:
    def __init__(self):
        self.store: dict[str, bytes] = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, ex=None):
        self.store[key] = value.encode() if isinstance(value, str) else value


class FakeProvider:
    """Stands in for sheetllm.complete_json, answering a batch the way a
    well-behaved model would — and, on request, the way a misbehaving one
    would: dropping entries, scrambling their order, or drifting the index."""

    def __init__(self, answers, drop=(), scramble=False, index_offset=0):
        self.answers = answers
        self.drop = set(drop)
        self.scramble = scramble
        self.index_offset = index_offset
        self.calls: list[tuple[list[str], int | None]] = []

    def __call__(self, system, user, project=None, max_tokens=None):
        snippets = re.findall(r'<page index="\d+">(.*?)</page>', user, re.S)
        self.calls.append((snippets, max_tokens))
        entries = []
        for position, snippet in enumerate(snippets, start=1):
            if snippet in self.drop:
                continue
            sheet = self.answers.get(snippet)
            entries.append(
                {
                    "index": position + self.index_offset,
                    "sheet_number": sheet,
                    "prefix": "".join(c for c in (sheet or "") if c.isalpha())[:1] or None,
                    "confidence": 0.95 if sheet else 0.1,
                }
            )
        if self.scramble:
            entries.reverse()
        return json.dumps({"sheets": entries})

    @property
    def call_count(self):
        return len(self.calls)

    @property
    def pages_sent(self):
        return [snippet for snippets, _ in self.calls for snippet in snippets]


@pytest.fixture
def ai_mode(monkeypatch):
    monkeypatch.setenv("SHEET_EXTRACTION", "ai")
    monkeypatch.setenv("SHEET_PROVIDER", "claude")
    monkeypatch.delenv("SHEET_RULES_FIRST", raising=False)


class TestRulesFirstPrePass:
    """What it may answer, and — mostly — what it must not."""

    def test_a_bare_sheet_number_needs_no_model(self):
        assert classify.confident_sheet_from_region("S-003.0") == ("S-003.0", "structural")

    def test_sheet_number_with_a_drawing_title(self):
        assert classify.confident_sheet_from_region("A17-11 EQUIPMENT PLANS") == (
            "A17-11",
            "architectural",
        )

    def test_lowercase_scrape_is_normalised(self):
        assert classify.confident_sheet_from_region("m301 mechanical plan") == (
            "M301",
            "mechanical",
        )

    def test_abstains_when_a_license_number_is_in_the_box(self):
        """The real sheet number IS there — and it still abstains, because a
        box holding 'F-1105' next to 'S-003.0' is exactly the judgement call
        the model was brought in for."""
        assert (
            classify.confident_sheet_from_region("S-003.0 NC LICENSE NO. F-1105") is None
        )

    def test_abstains_on_a_reference_to_another_sheet(self):
        assert classify.confident_sheet_from_region("A-101 PLAN SEE A-501 FOR TYPICAL") is None

    def test_abstains_when_two_sheet_numbers_are_present(self):
        assert classify.confident_sheet_from_region("A-101 S-201") is None

    def test_abstains_on_a_weak_content_callout(self):
        """'TYPE E2' is a fixture mark, not a sheet number — one letter, one
        digit. The permissive fallback may guess at it; the pre-pass may not."""
        assert classify.confident_sheet_from_region("EQUIPMENT TYPE E2") is None

    def test_abstains_on_a_job_number(self):
        assert classify.confident_sheet_from_region("B & P JOB NUMBER M24.07") is None

    def test_abstains_on_an_empty_box(self):
        assert classify.confident_sheet_from_region("") is None
        assert classify.confident_sheet_from_region(None) is None

    def test_the_same_number_scraped_twice_is_still_one_answer(self):
        """Two text layers over one title block scrape the number twice; that
        is not ambiguity."""
        assert classify.confident_sheet_from_region("E1.1 E1.1 POWER PLAN") == (
            "E1.1",
            "electrical",
        )


class TestPrePassShortCircuitsTheModel:
    def test_a_confident_read_never_reaches_a_provider(self, ai_mode, monkeypatch):
        def boom(*args, **kwargs):
            raise AssertionError("the model was called for an unambiguous box")

        monkeypatch.setattr(classify.sheetllm, "complete_json", boom)
        assert classify.classify_region_text("S-003.0", None, None) == (
            "S-003.0",
            "structural",
            "region_rules",
        )

    def test_the_pre_pass_can_be_switched_off(self, ai_mode, monkeypatch):
        """An escape hatch that must actually put the model back in the path —
        the same box, now read by the provider."""
        monkeypatch.setenv("SHEET_RULES_FIRST", "false")
        seen: list[str] = []

        def answer(system, user, project=None, max_tokens=None):
            seen.append(user)
            return '{"sheet_number": "S-003.0", "prefix": "S", "confidence": 0.9}'

        monkeypatch.setattr(classify.sheetllm, "complete_json", answer)
        assert classify.classify_region_text("S-003.0", None, None)[2] == "region_ai"
        assert seen, "the provider was not consulted with the pre-pass disabled"

    def test_switching_it_off_also_reaches_the_batch_path(self, ai_mode, monkeypatch):
        monkeypatch.setenv("SHEET_RULES_FIRST", "false")
        provider = FakeProvider({"S-003.0": "S-003.0"})
        monkeypatch.setattr(classify.sheetllm, "complete_json", provider)
        results = classify.classify_region_batch([("S-003.0", None)])
        assert provider.pages_sent == ["S-003.0"]
        assert results == [("S-003.0", "structural", "region_ai")]


class TestBatchParsing:
    def test_reads_the_wrapped_array(self):
        raw = json.dumps(
            {"sheets": [{"index": 1, "sheet_number": "A-101", "prefix": "A", "confidence": 0.9}]}
        )
        assert classify.parse_sheet_batch_response(raw, 1) == {0: ("A-101", "architectural")}

    def test_reads_a_bare_array_too(self):
        raw = json.dumps([{"index": 1, "sheet_number": "M301", "prefix": "M", "confidence": 0.9}])
        assert classify.parse_sheet_batch_response(raw, 1) == {0: ("M301", "mechanical")}

    def test_tolerates_a_code_fence(self):
        raw = '```json\n{"sheets": [{"index": 1, "sheet_number": "C2.0", "prefix": "C", "confidence": 0.9}]}\n```'
        assert classify.parse_sheet_batch_response(raw, 1) == {0: ("C2.0", "civil")}

    def test_an_index_outside_the_batch_is_dropped(self):
        """The drift case: an answer for page 9 in a batch of 2 belongs to no
        page here, so it is thrown away rather than wrapped around."""
        raw = json.dumps({"sheets": [{"index": 9, "sheet_number": "A-101", "confidence": 0.9}]})
        assert classify.parse_sheet_batch_response(raw, 2) == {}

    def test_a_repeated_index_keeps_only_the_first(self):
        raw = json.dumps(
            {
                "sheets": [
                    {"index": 1, "sheet_number": "A-101", "confidence": 0.9},
                    {"index": 1, "sheet_number": "S-201", "confidence": 0.9},
                ]
            }
        )
        assert classify.parse_sheet_batch_response(raw, 2) == {0: ("A-101", "architectural")}

    def test_a_low_confidence_entry_counts_as_answered(self):
        """Answered-with-nothing is not the same as unanswered: the page was
        looked at, so it must not trigger a retry."""
        raw = json.dumps(
            {"sheets": [{"index": 1, "sheet_number": None, "confidence": 0.1}]}
        )
        assert classify.parse_sheet_batch_response(raw, 1) == {0: None}

    def test_junk_is_not_an_exception(self):
        assert classify.parse_sheet_batch_response("sorry, I cannot help", 3) == {}
        assert classify.parse_sheet_batch_response("", 3) == {}

    def test_entries_that_are_not_objects_are_skipped(self):
        raw = json.dumps({"sheets": ["A-101", 7, None]})
        assert classify.parse_sheet_batch_response(raw, 3) == {}


class TestBatchedReading:
    ANSWERS = {
        "A-101 PLAN SEE A-501": "A-101",
        "S-201 FRAMING SEE S-001": "S-201",
        "M301 SEE M-001 FOR NOTES": "M301",
    }

    def test_many_pages_one_call(self, ai_mode, monkeypatch):
        provider = FakeProvider(self.ANSWERS)
        monkeypatch.setattr(classify.sheetllm, "complete_json", provider)
        results = classify.extract_sheets_from_regions(list(self.ANSWERS), None)
        assert provider.call_count == 1
        assert results == [
            ("A-101", "architectural"),
            ("S-201", "structural"),
            ("M301", "mechanical"),
        ]

    def test_answers_land_on_the_right_page_when_reordered(self, ai_mode, monkeypatch):
        """The model is told to keep the order; the index is what we actually
        trust, so a reordered response must still align."""
        provider = FakeProvider(self.ANSWERS, scramble=True)
        monkeypatch.setattr(classify.sheetllm, "complete_json", provider)
        results = classify.extract_sheets_from_regions(list(self.ANSWERS), None)
        assert [r[0] for r in results] == ["A-101", "S-201", "M301"]

    def test_an_off_by_one_response_resolves_nothing_rather_than_mislabelling(
        self, ai_mode, monkeypatch
    ):
        """The failure this design exists to prevent. Every index shifted by
        one: the last answer falls off the end, the rest would each land on
        their neighbour. The right outcome is unresolved pages, not a project
        classified one sheet out of step."""
        provider = FakeProvider(self.ANSWERS, index_offset=100)
        monkeypatch.setattr(classify.sheetllm, "complete_json", provider)
        results = classify.extract_sheets_from_regions(list(self.ANSWERS), None)
        assert results == [None, None, None]

    def test_identical_boxes_are_read_once(self, ai_mode, monkeypatch):
        """A drawing set repeats its blank and boilerplate title blocks; those
        are one string, not fifty."""
        provider = FakeProvider({"A-101 PLAN SEE A-501": "A-101"})
        monkeypatch.setattr(classify.sheetllm, "complete_json", provider)
        pages = ["A-101 PLAN SEE A-501"] * 50
        results = classify.extract_sheets_from_regions(pages, None)
        assert provider.pages_sent == ["A-101 PLAN SEE A-501"]
        assert all(r == ("A-101", "architectural") for r in results)

    def test_batches_respect_the_configured_size(self, ai_mode, monkeypatch):
        monkeypatch.setattr(classify, "SHEET_BATCH_SIZE", 2)
        pages = [f"SHEET {n} SEE OTHER" for n in range(5)]
        provider = FakeProvider({p: None for p in pages})
        monkeypatch.setattr(classify.sheetllm, "complete_json", provider)
        classify.extract_sheets_from_regions(pages, None)
        assert [len(sent) for sent, _ in provider.calls] == [2, 2, 1]

    def test_the_output_cap_scales_with_the_batch(self, ai_mode, monkeypatch):
        """A cap sized for one answer truncates the array and silently loses
        the tail of the batch."""
        pages = [f"SHEET {n} SEE OTHER" for n in range(10)]
        provider = FakeProvider({p: None for p in pages})
        monkeypatch.setattr(classify.sheetllm, "complete_json", provider)
        classify.extract_sheets_from_regions(pages, None)
        _, max_tokens = provider.calls[0]
        assert max_tokens > 10 * 25

    def test_unanswered_pages_are_retried_in_a_smaller_batch(self, ai_mode, monkeypatch):
        """A truncated array leaves pages with no entry. Those get re-asked —
        the answer is recovered, not lost."""
        pages = ["A-101 PLAN SEE A-501", "S-201 FRAMING SEE S-001"]
        provider = FakeProvider(self.ANSWERS, drop=["S-201 FRAMING SEE S-001"])
        # Drop it only on the first call, the way a truncation would behave.
        original = provider.__call__

        def once(system, user, project=None, max_tokens=None):
            out = original(system, user, project, max_tokens)
            provider.drop = set()
            return out

        monkeypatch.setattr(classify.sheetllm, "complete_json", once)
        results = classify.extract_sheets_from_regions(pages, None)
        assert provider.call_count == 2
        assert results == [("A-101", "architectural"), ("S-201", "structural")]

    def test_a_page_that_stays_unanswered_gives_up_quietly(self, ai_mode, monkeypatch):
        pages = ["A-101 PLAN SEE A-501", "S-201 FRAMING SEE S-001"]
        provider = FakeProvider(self.ANSWERS, drop=["S-201 FRAMING SEE S-001"])
        monkeypatch.setattr(classify.sheetllm, "complete_json", provider)
        results = classify.extract_sheets_from_regions(pages, None)
        assert results == [("A-101", "architectural"), None]

    def test_blank_boxes_never_reach_the_provider(self, ai_mode, monkeypatch):
        provider = FakeProvider(self.ANSWERS)
        monkeypatch.setattr(classify.sheetllm, "complete_json", provider)
        results = classify.extract_sheets_from_regions(["", "   ", "A-101 PLAN SEE A-501"], None)
        assert provider.pages_sent == ["A-101 PLAN SEE A-501"]
        assert results[0] is None and results[1] is None


class TestBatchCache:
    def test_answers_are_cached_per_string(self, ai_mode, monkeypatch):
        redis = FakeRedis()
        provider = FakeProvider({"A-101 PLAN SEE A-501": "A-101"})
        monkeypatch.setattr(classify.sheetllm, "complete_json", provider)
        classify.extract_sheets_from_regions(["A-101 PLAN SEE A-501"], redis)
        classify.extract_sheets_from_regions(["A-101 PLAN SEE A-501"], redis)
        assert provider.call_count == 1

    def test_a_batch_reuses_what_a_single_read_cached(self, ai_mode, monkeypatch):
        """Same key either way — the preview reads one page, the scrape reads
        the rest, and neither re-pays for the other's work."""
        redis = FakeRedis()
        monkeypatch.setattr(
            classify.sheetllm,
            "complete_json",
            lambda *a, **k: '{"sheet_number": "A-101", "prefix": "A", "confidence": 0.9}',
        )
        classify.extract_sheet_from_region("A-101 PLAN SEE A-501", None, redis)

        provider = FakeProvider({})
        monkeypatch.setattr(classify.sheetllm, "complete_json", provider)
        results = classify.extract_sheets_from_regions(["A-101 PLAN SEE A-501"], redis)
        assert provider.call_count == 0
        assert results == [("A-101", "architectural")]

    def test_only_the_uncached_pages_are_sent(self, ai_mode, monkeypatch):
        redis = FakeRedis()
        answers = {"A-101 PLAN SEE A-501": "A-101", "S-201 FRAMING SEE S-001": "S-201"}
        provider = FakeProvider(answers)
        monkeypatch.setattr(classify.sheetllm, "complete_json", provider)
        classify.extract_sheets_from_regions(["A-101 PLAN SEE A-501"], redis)
        classify.extract_sheets_from_regions(list(answers), redis)
        assert provider.calls[1][0] == ["S-201 FRAMING SEE S-001"]

    def test_an_outage_is_never_cached(self, ai_mode, monkeypatch):
        """A provider that is down must not write 'no sheet number' against
        every page for the next 30 days."""
        redis = FakeRedis()
        monkeypatch.setattr(classify.sheetllm, "complete_json", lambda *a, **k: None)
        assert classify.extract_sheets_from_regions(["A-101 PLAN SEE A-501"], redis) == [None]
        assert redis.store == {}

    def test_a_real_no_answer_is_cached(self, ai_mode, monkeypatch):
        """The model looked and found nothing — worth remembering, unlike an
        outage."""
        redis = FakeRedis()
        provider = FakeProvider({"SCALE 1/4 IN SEE PLAN": None})
        monkeypatch.setattr(classify.sheetllm, "complete_json", provider)
        classify.extract_sheets_from_regions(["SCALE 1/4 IN SEE PLAN"], redis)
        classify.extract_sheets_from_regions(["SCALE 1/4 IN SEE PLAN"], redis)
        assert provider.call_count == 1

    def test_providers_do_not_share_cached_batches(self, ai_mode, monkeypatch):
        redis = FakeRedis()
        provider = FakeProvider({"A-101 PLAN SEE A-501": "A-101"})
        monkeypatch.setattr(classify.sheetllm, "complete_json", provider)
        classify.extract_sheets_from_regions(["A-101 PLAN SEE A-501"], redis)
        monkeypatch.setenv("SHEET_PROVIDER", "gemini")
        classify.extract_sheets_from_regions(["A-101 PLAN SEE A-501"], redis)
        assert provider.call_count == 2


class TestClassifyRegionBatch:
    """The whole-project entry point the scrape job uses: same ladder as the
    per-page function, same answers, far fewer calls."""

    def test_only_the_ambiguous_pages_cost_a_call(self, ai_mode, monkeypatch):
        provider = FakeProvider({"A-101 PLAN SEE A-501": "A-101"})
        monkeypatch.setattr(classify.sheetllm, "complete_json", provider)
        results = classify.classify_region_batch(
            [
                ("S-003.0", None),
                ("A-101 PLAN SEE A-501", None),
                ("M301 MECHANICAL PLAN", None),
            ]
        )
        assert provider.pages_sent == ["A-101 PLAN SEE A-501"]
        assert [r[1] for r in results] == ["structural", "architectural", "mechanical"]
        assert [r[2] for r in results] == ["region_rules", "region_ai", "region_rules"]

    def test_results_stay_aligned_with_the_pages(self, ai_mode, monkeypatch):
        provider = FakeProvider({"A-101 PLAN SEE A-501": "A-101"})
        monkeypatch.setattr(classify.sheetllm, "complete_json", provider)
        items = [("S-003.0", None)] * 3 + [("A-101 PLAN SEE A-501", None)] + [("E1.1", None)]
        results = classify.classify_region_batch(items)
        assert [r[0] for r in results] == ["S-003.0", "S-003.0", "S-003.0", "A-101", "E1.1"]

    def test_a_page_the_model_cannot_read_falls_back_to_the_filename(
        self, ai_mode, monkeypatch
    ):
        provider = FakeProvider({"SCALE 1/4 IN SEE PLAN": None})
        monkeypatch.setattr(classify.sheetllm, "complete_json", provider)
        results = classify.classify_region_batch(
            [("SCALE 1/4 IN SEE PLAN", "E1.1 POWER PLAN.pdf")]
        )
        assert results == [(None, "electrical", "filename")]

    def test_an_unreadable_page_resolves_to_nothing_for_the_fill_pass(
        self, ai_mode, monkeypatch
    ):
        provider = FakeProvider({"SCALE 1/4 IN SEE PLAN": None})
        monkeypatch.setattr(classify.sheetllm, "complete_json", provider)
        assert classify.classify_region_batch([("SCALE 1/4 IN SEE PLAN", "plans.pdf")]) == [
            (None, None, None)
        ]

    def test_rules_mode_calls_no_provider_at_all(self, monkeypatch):
        monkeypatch.setenv("SHEET_EXTRACTION", "rules")

        def boom(*args, **kwargs):
            raise AssertionError("SHEET_EXTRACTION=rules must not call a provider")

        monkeypatch.setattr(classify.sheetllm, "complete_json", boom)
        results = classify.classify_region_batch(
            [("S-003.0", None), ("A-101 PLAN SEE A-501", None)]
        )
        assert [r[1] for r in results] == ["structural", "architectural"]

    def test_offline_matches_the_per_page_function(self, monkeypatch):
        """The batch path is an optimisation, not a second implementation: with
        no provider at all it must produce exactly what the old loop did."""
        monkeypatch.setenv("SHEET_EXTRACTION", "rules")
        items = [
            ("S-003.0", None),
            ("", "E1.1 POWER PLAN.pdf"),
            ('SCALE 1/4" = 1\'-0"', "plans.pdf"),
            ("A-101 PLAN SEE A-501", None),
        ]
        assert classify.classify_region_batch(items) == [
            classify.classify_region_text(text, filename, None) for text, filename in items
        ]
