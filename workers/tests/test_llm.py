"""Provider switching for the shared model transport.

The invariant under test is the same one test_sheetllm.py pins for sheet
reads, applied to every call site: choosing Gemini changes WHO answers and
nothing about what the answer is allowed to be. The instructions sent, the
parser applied to the reply, and the failure behaviour must all be identical.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import llm  # noqa: E402
import summarize  # noqa: E402
import usage  # noqa: E402


class TestResolve:
    def test_defaults_when_unset(self, monkeypatch):
        monkeypatch.delenv("SUMMARY_PROVIDER", raising=False)
        assert llm.resolve("SUMMARY_PROVIDER") == "claude"

    def test_reads_the_env_var(self, monkeypatch):
        monkeypatch.setenv("SUMMARY_PROVIDER", "gemini")
        assert llm.resolve("SUMMARY_PROVIDER") == "gemini"

    def test_case_and_whitespace_tolerated(self, monkeypatch):
        monkeypatch.setenv("SUMMARY_PROVIDER", "  GEMINI ")
        assert llm.resolve("SUMMARY_PROVIDER") == "gemini"

    def test_a_typo_falls_back_rather_than_crashing(self, monkeypatch):
        monkeypatch.setenv("SUMMARY_PROVIDER", "gpt4")
        assert llm.resolve("SUMMARY_PROVIDER") == "claude"

    def test_call_sites_switch_independently(self, monkeypatch):
        """Sheet reads are cheap per-page classification and summaries are the
        expensive reasoning step — there is no reason they share a vendor."""
        monkeypatch.setenv("SHEET_PROVIDER", "gemini")
        monkeypatch.setenv("SUMMARY_PROVIDER", "claude")
        assert llm.resolve("SHEET_PROVIDER") == "gemini"
        assert llm.resolve("SUMMARY_PROVIDER") == "claude"


class TestSystemPrompt:
    """Both providers must receive the SAME instructions, however they are
    packaged — otherwise the two are not comparable."""

    BLOCKS = [
        {"type": "text", "text": "FIRST BLOCK", "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "SECOND BLOCK"},
    ]

    def test_gemini_receives_every_block(self):
        flat = llm._flatten_system(self.BLOCKS)
        assert "FIRST BLOCK" in flat and "SECOND BLOCK" in flat

    def test_claude_blocks_pass_through_untouched(self):
        """Summaries hand-build their blocks so the first keeps a
        byte-identical cache prefix; wrapping them would break that."""
        assert llm._system_blocks(self.BLOCKS, cache=True) is self.BLOCKS

    def test_a_plain_string_gets_a_cache_breakpoint(self):
        (block,) = llm._system_blocks("INSTRUCTIONS", cache=True)
        assert block == {
            "type": "text",
            "text": "INSTRUCTIONS",
            "cache_control": {"type": "ephemeral"},
        }

    def test_caching_can_be_declined(self):
        (block,) = llm._system_blocks("INSTRUCTIONS", cache=False)
        assert "cache_control" not in block


class TestStopReasonIsNormalized:
    """Callers branch on truncation ("retry shorter, with more room"), so the
    two providers' names for it have to collapse into one."""

    class _Candidate:
        def __init__(self, reason):
            self.finish_reason = reason

    class _Response:
        def __init__(self, reason):
            self.candidates = [TestStopReasonIsNormalized._Candidate(reason)]

    def test_gemini_truncation_maps_to_max_tokens(self):
        assert llm._gemini_stop_reason(self._Response("MAX_TOKENS")) == "max_tokens"

    def test_enum_style_reason_is_handled(self):
        class Enum:
            name = "MAX_TOKENS"

        assert llm._gemini_stop_reason(self._Response(Enum())) == "max_tokens"

    def test_a_normal_finish_is_not_reported_as_truncation(self):
        assert llm._gemini_stop_reason(self._Response("STOP")) == "stop"

    def test_no_candidates(self):
        class Empty:
            candidates = []

        assert llm._gemini_stop_reason(Empty()) is None


class TestUnavailableProviderDegrades:
    """A missing key must fall back, never raise — the same contract the rules
    ladder relies on for sheet reads."""

    def test_no_key_returns_none(self, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.setattr(llm, "_gemini", None)
        monkeypatch.setattr(llm, "_gemini_unavailable", False)
        assert (
            llm.complete(
                "system",
                "user",
                provider="gemini",
                claude_model="c",
                gemini_model="g",
                max_tokens=10,
                kind="summary",
            )
            is None
        )

    def test_a_raising_provider_returns_none(self, monkeypatch):
        def boom(*_a, **_k):
            raise RuntimeError("upstream 500")

        monkeypatch.setattr(llm, "_complete_claude", boom)
        assert (
            llm.complete(
                "system",
                "user",
                provider="claude",
                claude_model="c",
                gemini_model="g",
                max_tokens=10,
                kind="summary",
            )
            is None
        )


class TestSummaryProvider:
    def test_model_follows_the_provider(self, monkeypatch):
        monkeypatch.setenv("SUMMARY_PROVIDER", "gemini")
        assert summarize.model_name() == summarize.SUMMARY_GEMINI_MODEL
        monkeypatch.setenv("SUMMARY_PROVIDER", "claude")
        assert summarize.model_name() == summarize.SUMMARY_MODEL

    def test_availability_checks_that_provider_s_key(self, monkeypatch):
        monkeypatch.setenv("SUMMARY_PROVIDER", "gemini")
        monkeypatch.setenv("GEMINI_API_KEY", "g")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        assert summarize.available()

    def test_both_providers_batch(self, monkeypatch):
        """Gemini's inline batch jobs mean bulk summaries no longer drop to
        sequential calls — the expensive path on a 400-page project."""
        for name in llm.PROVIDERS:
            monkeypatch.setenv("SUMMARY_PROVIDER", name)
            assert summarize.batch_supported(), name

    def test_the_batch_goes_to_the_active_provider(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(llm, "complete_batch", lambda prompts, **kw: seen.update(kw) or {})
        monkeypatch.setenv("SUMMARY_PROVIDER", "gemini")
        summarize._call_batch({"page-0": "prompt"})
        assert seen["provider"] == "gemini"
        assert seen["gemini_model"] == summarize.SUMMARY_GEMINI_MODEL

    def test_an_unavailable_provider_yields_no_summary_rather_than_an_error(
        self, monkeypatch
    ):
        """_call_direct returning empty text is what the strict parser then
        rejects, so the page is skipped like any other unusable answer."""
        monkeypatch.setattr(llm, "complete", lambda *a, **k: None)
        assert summarize._call_direct("prompt") == ("", None)
        assert summarize.parse_summary_json("", {"chunk-1"}) is None


class TestBatchPolling:
    """A batch that never finishes must not pin a worker thread for a day."""

    def test_returns_the_finished_job(self, monkeypatch):
        monkeypatch.setattr(llm.time, "sleep", lambda _s: None)
        states = iter(["running", "running", "done"])
        current = {"state": next(states)}

        def refresh():
            return current["state"]

        def finished(state):
            if state != "done":
                current["state"] = next(states)
            return state == "done"

        assert llm._await_batch("job", refresh, finished) == "done"

    def test_times_out_rather_than_waiting_forever(self, monkeypatch):
        """The Anthropic loop used to be `while not ended: sleep(2)` with no
        bound. A stuck batch hung the summarize job indefinitely."""
        monkeypatch.setattr(llm, "BATCH_TIMEOUT_SECONDS", 0.05)
        monkeypatch.setattr(llm, "BATCH_POLL_SECONDS", 0.01)
        monkeypatch.setattr(llm.time, "sleep", lambda _s: None)
        import pytest

        with pytest.raises(llm.BatchTimeout):
            llm._await_batch("job", lambda: "running", lambda _j: False)

    def test_an_already_finished_job_never_sleeps(self, monkeypatch):
        def boom(_s):
            raise AssertionError("slept on an already-finished batch")

        monkeypatch.setattr(llm.time, "sleep", boom)
        assert llm._await_batch("job", lambda: "done", lambda j: j == "done") == "done"


class TestGeminiBatchResults:
    """Result-shape handling, which is where a batch quietly loses pages."""

    class _Meta:
        prompt_token_count = 120
        candidates_token_count = 40
        cached_content_token_count = 20

    class _Response:
        def __init__(self, text):
            self.text = text
            self.usage_metadata = TestGeminiBatchResults._Meta()

    class _Entry:
        def __init__(self, text=None, metadata=None, error=None):
            self.response = TestGeminiBatchResults._Response(text) if text else None
            self.metadata = metadata
            self.error = error

    def _run(self, monkeypatch, entries, state="JOB_STATE_SUCCEEDED"):
        class _Job:
            name = "batches/1"

        job = _Job()
        job.state = state
        job.dest = type("Dest", (), {"inlined_responses": entries})()

        class _Batches:
            def create(self, **_kw):
                return job

            def get(self, **_kw):
                return job

        monkeypatch.setattr(
            llm, "gemini_client", lambda: type("C", (), {"batches": _Batches()})()
        )
        # Token accounting writes to Postgres; these tests only care that the
        # batch path records without blowing up.
        monkeypatch.setattr(usage, "record", lambda *a, **k: None)
        return llm._batch_gemini(
            {"page-0": "a", "page-1": "b"},
            system="system",
            model="gemini-2.5-pro",
            max_tokens=100,
            kind="summary",
            project_id=None,
            json_only=True,
        )

    def test_matches_answers_to_pages_by_custom_id(self, monkeypatch):
        results = self._run(
            monkeypatch,
            [
                self._Entry("second", metadata={"custom_id": "page-1"}),
                self._Entry("first", metadata={"custom_id": "page-0"}),
            ],
        )
        # Deliberately out of order: an id-keyed result must not depend on it.
        assert results == {"page-0": "first", "page-1": "second"}

    def test_falls_back_to_position_when_no_id_comes_back(self, monkeypatch):
        results = self._run(monkeypatch, [self._Entry("first"), self._Entry("second")])
        assert results == {"page-0": "first", "page-1": "second"}

    def test_a_failed_entry_is_skipped_without_shifting_the_others(self, monkeypatch):
        """Positional matching is only safe because a failure is still an entry."""
        results = self._run(
            monkeypatch, [self._Entry(error="quota"), self._Entry("second")]
        )
        assert results == {"page-1": "second"}

    def test_a_partially_succeeded_job_keeps_what_it_produced(self, monkeypatch):
        """Those summaries were paid for; discarding them re-spends on a retry."""
        results = self._run(
            monkeypatch,
            [self._Entry("first", metadata={"custom_id": "page-0"})],
            state="JOB_STATE_PARTIALLY_SUCCEEDED",
        )
        assert results == {"page-0": "first"}

    def test_no_key_returns_empty_rather_than_raising(self, monkeypatch):
        monkeypatch.setattr(llm, "gemini_client", lambda: None)
        assert (
            llm._batch_gemini(
                {"page-0": "a"},
                system="s",
                model="m",
                max_tokens=10,
                kind="summary",
                project_id=None,
                json_only=True,
            )
            == {}
        )


class TestThinkingBudget:
    """Thinking tokens are spent from max_output_tokens BEFORE the answer is
    written, so an unbounded budget truncates the JSON the parser needs. This
    is what made every gemini-3.1-pro-preview summary come back cut off."""

    def test_disabled_by_default(self, monkeypatch):
        monkeypatch.setattr(llm, "GEMINI_THINKING_BUDGET", 0)
        monkeypatch.setattr(llm, "_no_thinking_config", set())
        config = llm._gemini_config(model="gemini-x", max_tokens=2000)
        assert config["thinking_config"] == {"thinking_budget": 0}

    def test_can_be_raised_by_env(self, monkeypatch):
        monkeypatch.setattr(llm, "GEMINI_THINKING_BUDGET", 512)
        monkeypatch.setattr(llm, "_no_thinking_config", set())
        config = llm._gemini_config(model="gemini-x", max_tokens=2000)
        assert config["thinking_config"] == {"thinking_budget": 512}

    def test_omitted_for_a_model_that_refused_it(self, monkeypatch):
        monkeypatch.setattr(llm, "_no_thinking_config", {"gemini-x"})
        assert "thinking_config" not in llm._gemini_config(model="gemini-x", max_tokens=100)

    def test_the_batch_and_single_paths_ask_for_the_same_thing(self, monkeypatch):
        """A page summarized in a batch and the same page retried directly must
        get an identical request, or the retry silently changes the shape."""
        monkeypatch.setattr(llm, "_no_thinking_config", set())
        kwargs = dict(model="gemini-x", max_tokens=2000, system="sys", json_only=True)
        assert llm._gemini_config(**kwargs) == llm._gemini_config(**kwargs)
        assert llm._gemini_config(**kwargs)["response_mime_type"] == "application/json"

    def test_a_model_that_rejects_the_budget_is_retried_without_it(self, monkeypatch):
        """Some tiers cannot turn thinking off. That must degrade, not fail
        every page of the project."""
        monkeypatch.setattr(llm, "_no_thinking_config", set())
        calls = []

        class _Models:
            def generate_content(self, *, model, contents, config):
                calls.append(config)
                if "thinking_config" in config:
                    raise ValueError("thinking_config is not supported for this model")
                return type(
                    "R", (), {"text": "ok", "usage_metadata": None, "candidates": []}
                )()

        monkeypatch.setattr(
            llm, "gemini_client", lambda: type("C", (), {"models": _Models()})()
        )
        monkeypatch.setattr(usage, "record", lambda *a, **k: None)

        reply = llm._complete_gemini(
            "sys", "user", model="gemini-x", max_tokens=100,
            kind="summary", project_id=None, json_only=True,
        )
        assert reply.text == "ok"
        assert len(calls) == 2 and "thinking_config" not in calls[1]
        # Latched: the next page skips straight to the working shape.
        assert "gemini-x" in llm._no_thinking_config

    def test_an_unrelated_error_still_propagates(self, monkeypatch):
        """Only a thinking refusal earns the retry — a 500 must not be retried
        as though the config were at fault."""
        monkeypatch.setattr(llm, "_no_thinking_config", set())

        class _Models:
            def generate_content(self, **_kw):
                raise RuntimeError("upstream 500")

        monkeypatch.setattr(
            llm, "gemini_client", lambda: type("C", (), {"models": _Models()})()
        )
        import pytest

        with pytest.raises(RuntimeError):
            llm._complete_gemini(
                "sys", "user", model="gemini-x", max_tokens=100,
                kind="summary", project_id=None, json_only=True,
            )


class TestThinkingTokensAreBilled:
    """Google bills thinking as output. Recording only candidatesTokenCount
    under-reported spend on exactly the models that think the most."""

    class _Meta:
        prompt_token_count = 1000
        candidates_token_count = 200
        thoughts_token_count = 1500
        cached_content_token_count = 400

    def _recorded(self, monkeypatch, meta):
        rows = []
        monkeypatch.setattr(usage, "record", lambda *a, **k: rows.append(k))
        llm._record_gemini_usage(meta, kind="summary", model="m", project_id=None)
        return rows[0]

    def test_thinking_counts_as_output(self, monkeypatch):
        row = self._recorded(monkeypatch, self._Meta())
        assert row["output_tokens"] == 200 + 1500

    def test_cached_tokens_are_not_billed_twice(self, monkeypatch):
        # prompt_token_count INCLUDES the cached ones.
        row = self._recorded(monkeypatch, self._Meta())
        assert row["input_tokens"] == 1000 - 400
        assert row["cache_read_tokens"] == 400

    def test_a_response_with_no_metadata_records_zeros(self, monkeypatch):
        row = self._recorded(monkeypatch, None)
        assert row["output_tokens"] == 0 and row["input_tokens"] == 0
