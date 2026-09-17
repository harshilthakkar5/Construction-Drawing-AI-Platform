"""Provider switching for the shared model transport.

The invariant under test is the same one test_sheetllm.py pins for sheet
reads, applied to every call site: choosing Gemini changes WHO answers and
nothing about what the answer is allowed to be. The instructions sent, the
parser applied to the reply, and the failure behaviour must all be identical.
"""

import types
import sys

import pytest
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

        assert llm.await_batch("job", refresh, finished) == "done"

    def test_times_out_rather_than_waiting_forever(self, monkeypatch):
        """The Anthropic loop used to be `while not ended: sleep(2)` with no
        bound. A stuck batch hung the summarize job indefinitely."""
        monkeypatch.setattr(llm, "BATCH_TIMEOUT_SECONDS", 0.05)
        monkeypatch.setattr(llm, "BATCH_POLL_SECONDS", 0.01)
        monkeypatch.setattr(llm.time, "sleep", lambda _s: None)
        import pytest

        with pytest.raises(llm.BatchTimeout):
            llm.await_batch("job", lambda: "running", lambda _j: False)

    def test_an_already_finished_job_never_sleeps(self, monkeypatch):
        def boom(_s):
            raise AssertionError("slept on an already-finished batch")

        monkeypatch.setattr(llm.time, "sleep", boom)
        assert llm.await_batch("job", lambda: "done", lambda j: j == "done") == "done"


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

    def test_gemini_3_is_asked_for_a_level_not_a_budget(self, monkeypatch):
        """The field changed, and the transport that kept sending the old one
        got the model's DEFAULT — which is the top of the scale.

        This is the measured failure: VLM_PROVIDER=gemini on gemini-3.6-flash
        wrote 103 tokens of description out of a 4000-token budget and spent
        the rest reasoning, from a transport that believed thinking was off.
        """
        monkeypatch.setattr(llm, "_no_thinking_config", set())
        monkeypatch.setattr(llm, "_thinking_latched", {})
        monkeypatch.setattr(llm, "GEMINI_THINKING_LEVEL", "minimal")
        config = llm._gemini_config(model="gemini-3.6-flash", max_tokens=4000)
        assert config["thinking_config"] == {"thinking_level": "minimal"}
        # Sending BOTH fields is an error on these models.
        assert "thinking_budget" not in config["thinking_config"]

    def test_the_budget_env_cannot_silence_a_level_model(self, monkeypatch):
        """GEMINI_THINKING_BUDGET=off means "omit the field", which is exactly
        the wrong thing here — omission IS the maximum. A level model ignores
        it and gets a level, so the escape hatch cannot re-create the bug."""
        monkeypatch.setattr(llm, "_no_thinking_config", set())
        monkeypatch.setattr(llm, "_thinking_latched", {})
        monkeypatch.setattr(llm, "GEMINI_THINKING_BUDGET", None)
        monkeypatch.setattr(llm, "GEMINI_THINKING_LEVEL", "minimal")
        config = llm._gemini_config(model="gemini-3.6-flash", max_tokens=4000)
        assert config["thinking_config"] == {"thinking_level": "minimal"}

    def test_the_split_is_a_version_sniff_not_a_model_list(self, monkeypatch):
        """A model NAME expires on a schedule this repo does not control, and
        this file has already paid for one hard-coded list of them. Gemini 4
        has to work without a code change; every pre-3 model keeps the budget."""
        assert llm._takes_thinking_level("gemini-3.6-flash")
        assert llm._takes_thinking_level("models/gemini-3.1-pro-preview")
        assert llm._takes_thinking_level("gemini-4-flash")
        assert not llm._takes_thinking_level("gemini-2.5-flash")
        assert not llm._takes_thinking_level("gemini-1.5-pro")

    def test_the_ladder_tries_a_level_before_giving_up(self, monkeypatch):
        monkeypatch.setattr(llm, "_no_thinking_config", set())
        monkeypatch.setattr(llm, "_thinking_latched", {})
        monkeypatch.setattr(llm, "GEMINI_THINKING_LEVEL", "minimal")
        assert llm._thinking_ladder("gemini-3.6-flash") == [
            {"thinking_level": "minimal"},
            {"thinking_level": "low"},
            None,
        ]
        # A budget model has one rung and then omission, which there is OFF.
        monkeypatch.setattr(llm, "GEMINI_THINKING_BUDGET", 0)
        assert llm._thinking_ladder("gemini-2.5-flash") == [{"thinking_budget": 0}, None]

    def test_surrendering_on_a_level_model_is_reported_as_configuration(
        self, monkeypatch, caplog
    ):
        """Omission is a defeat here, not a fallback: it leaves the model
        thinking at its default for the rest of the run. Logged at ERROR for
        the same reason a retired model name is — it will never fix itself."""
        monkeypatch.setattr(llm, "_no_thinking_config", set())
        monkeypatch.setattr(llm, "_thinking_latched", {})
        with caplog.at_level("WARNING"):
            llm._latch_thinking("gemini-3.6-flash", None)
        assert "gemini-3.6-flash" in llm._no_thinking_config
        assert any(r.levelname == "ERROR" for r in caplog.records)
        assert "MAXIMUM" in caplog.text and "GEMINI_THINKING_LEVEL" in caplog.text

        caplog.clear()
        with caplog.at_level("WARNING"):
            llm._latch_thinking("gemini-2.5-flash", None)
        # A budget model really is off when the field is dropped — a warning.
        assert not any(r.levelname == "ERROR" for r in caplog.records)

    def test_automatic_function_calling_is_off_on_every_request(self, monkeypatch):
        """Nothing here declares a tool to Gemini, so AFC can never do anything
        — but the SDK routes each generate_content through its agentic wrapper
        and logs two lines, one of them at WARNING, on the way. In the run that
        caught the vision pass returning 98 characters, that warning sat
        directly above the line that mattered.

        The flag is checked against the SDK's own predicate rather than trusted
        as a spelling: a renamed field would leave the config looking correct
        and the noise still arriving.
        """
        monkeypatch.setattr(llm, "_no_thinking_config", set())
        config = llm._gemini_config(model="gemini-x", max_tokens=100)
        assert config["automatic_function_calling"] == {"disable": True}
        try:
            from google.genai import _extra_utils
        except ImportError:  # the SDK is optional for the rest of this suite
            return
        assert _extra_utils.should_disable_afc(config) is True

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

    def test_a_bare_invalid_argument_also_earns_the_retry(self, monkeypatch):
        """gemini-3.5-flash-lite rejects thinking_budget as a bare 400
        INVALID_ARGUMENT naming nothing. Matching only the polite wording left
        every SHEET_PROVIDER=gemini call failing into the rules ladder."""
        monkeypatch.setattr(llm, "_no_thinking_config", set())
        calls = []

        class _Models:
            def generate_content(self, *, model, contents, config):
                calls.append(config)
                if "thinking_config" in config:
                    raise RuntimeError(
                        "400 INVALID_ARGUMENT. {'error': {'code': 400, 'message': "
                        "'Request contains an invalid argument.', 'status': "
                        "'INVALID_ARGUMENT'}}"
                    )
                return type("R", (), {"text": "ok", "usage_metadata": None, "candidates": []})()

        monkeypatch.setattr(llm, "gemini_client", lambda: type("C", (), {"models": _Models()})())
        monkeypatch.setattr(usage, "record", lambda *a, **k: None)

        reply = llm._complete_gemini(
            "sys", "user", model="gemini-3.5-flash-lite", max_tokens=120,
            kind="classification", project_id=None, json_only=True,
        )
        assert reply.text == "ok"
        # Three calls, not two: omission is the LAST rung. On a Gemini 3 model
        # dropping the field asks for the model's default, which is the top of
        # the scale — so a real setting is tried before surrendering to it.
        assert len(calls) == 3
        assert calls[1]["thinking_config"] == {"thinking_level": "low"}
        assert "thinking_config" not in calls[2]
        assert "gemini-3.5-flash-lite" in llm._no_thinking_config

    def test_a_failed_retry_reports_the_original_error_and_does_not_latch(self, monkeypatch):
        """An invalid argument that was NOT the thinking budget costs one extra
        call and nothing else: the model keeps its normal request shape, and
        the error the caller logs is the real one."""
        monkeypatch.setattr(llm, "_no_thinking_config", set())
        calls = []

        class _Models:
            def generate_content(self, *, model, contents, config):
                calls.append(config)
                raise RuntimeError("400 INVALID_ARGUMENT: contents must not be empty")

        monkeypatch.setattr(llm, "gemini_client", lambda: type("C", (), {"models": _Models()})())

        import pytest

        with pytest.raises(RuntimeError, match="contents must not be empty"):
            llm._complete_gemini(
                "sys", "", model="gemini-x", max_tokens=120,
                kind="classification", project_id=None, json_only=True,
            )
        assert len(calls) == 2
        assert "gemini-x" not in llm._no_thinking_config  # not latched on a failed retry

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


class TestBatchThinkingRecovery:
    """A batch cannot learn from an exception: it is ACCEPTED, runs for
    minutes, and then reports per-entry errors. On gemini-3.1-pro-preview every
    entry came back `code=3 Request contains an invalid argument`, the caller
    saw an empty result, and the portion failed with "no page summaries could
    be generated" — naming nothing a user could act on."""

    @staticmethod
    def _job(entries):
        return types.SimpleNamespace(
            name="batches/x",
            state="JOB_STATE_SUCCEEDED",
            dest=types.SimpleNamespace(inlined_responses=entries),
        )

    @staticmethod
    def _ok(custom_id, text="{}"):
        return types.SimpleNamespace(
            error=None,
            metadata={"custom_id": custom_id},
            response=types.SimpleNamespace(text=text, usage_metadata=None),
        )

    @staticmethod
    def _rejected():
        return types.SimpleNamespace(
            error="details=None code=3 message='Request contains an invalid argument.'",
            metadata=None,
            response=None,
        )

    def _client(self, monkeypatch, reply_for):
        """reply_for(config) -> list of entries. Records every submitted config."""
        submitted = []

        class _Batches:
            def create(self, *, model, src, config):
                submitted.append(src[0]["config"])
                self._entries = reply_for(src[0]["config"], len(src))
                return TestBatchThinkingRecovery._job(self._entries)

            def get(self, *, name):
                return TestBatchThinkingRecovery._job(self._entries)

        monkeypatch.setattr(
            llm, "gemini_client", lambda: type("C", (), {"batches": _Batches()})()
        )
        monkeypatch.setattr(usage, "record", lambda *a, **k: None)
        return submitted

    def test_a_wholly_rejected_batch_is_resubmitted_without_thinking(self, monkeypatch):
        """A model that takes a BUDGET recovers by dropping the field, because
        there the field is the only thing asking for thinking."""
        monkeypatch.setattr(llm, "_no_thinking_config", set())
        monkeypatch.setattr(llm, "_thinking_latched", {})

        def reply_for(config, count):
            if "thinking_config" in config:
                return [self._rejected() for _ in range(count)]
            return [self._ok("page-1"), self._ok("page-2")]

        submitted = self._client(monkeypatch, reply_for)

        results = llm._batch_gemini(
            {"page-1": "a", "page-2": "b"}, system="sys", model="gemini-2.5-pro",
            max_tokens=2000, kind="summary", project_id=None, json_only=True,
        )

        assert results == {"page-1": "{}", "page-2": "{}"}
        assert len(submitted) == 2 and "thinking_config" not in submitted[1]
        # Latched, so the section and portion tiers skip straight to what works.
        assert "gemini-2.5-pro" in llm._no_thinking_config

    def test_a_rejected_batch_on_a_level_model_steps_up_rather_than_off(self, monkeypatch):
        """The resubmit for a Gemini 3 model is the next LEVEL, never omission.

        Omitting asks for the model's default, which is the most thinking it
        offers — and thinking is billed from the same max_output_tokens as the
        JSON, so "recovering" that way returns a batch of truncated summaries
        instead of an error anyone can act on. The likely cause is the floor
        itself: `minimal` is not accepted by every model in the family.
        """
        monkeypatch.setattr(llm, "_no_thinking_config", set())
        monkeypatch.setattr(llm, "_thinking_latched", {})

        def reply_for(config, count):
            if config.get("thinking_config") == {"thinking_level": "minimal"}:
                return [self._rejected() for _ in range(count)]
            return [self._ok("page-1"), self._ok("page-2")]

        submitted = self._client(monkeypatch, reply_for)

        results = llm._batch_gemini(
            {"page-1": "a", "page-2": "b"}, system="sys", model="gemini-3.1-pro-preview",
            max_tokens=2000, kind="summary", project_id=None, json_only=True,
        )

        assert results == {"page-1": "{}", "page-2": "{}"}
        assert len(submitted) == 2
        assert submitted[1]["thinking_config"] == {"thinking_level": "low"}
        # Latched to what worked, not to "no thinking field".
        assert llm._thinking_latched["gemini-3.1-pro-preview"] == {"thinking_level": "low"}
        assert "gemini-3.1-pro-preview" not in llm._no_thinking_config

    def test_a_partly_successful_batch_is_never_resubmitted(self, monkeypatch):
        """Resubmitting would pay for the entries that already succeeded."""
        monkeypatch.setattr(llm, "_no_thinking_config", set())
        submitted = self._client(
            monkeypatch, lambda config, count: [self._ok("page-1"), self._rejected()]
        )

        results = llm._batch_gemini(
            {"page-1": "a", "page-2": "b"}, system="sys", model="gemini-x",
            max_tokens=2000, kind="summary", project_id=None, json_only=True,
        )

        assert results == {"page-1": "{}"}
        assert len(submitted) == 1
        assert "gemini-x" not in llm._no_thinking_config

    def test_an_empty_batch_result_is_not_blamed_on_thinking(self, monkeypatch):
        """Nothing came back at all — a different failure, and resubmitting it
        without the budget would just wait through it twice."""
        monkeypatch.setattr(llm, "_no_thinking_config", set())
        submitted = self._client(monkeypatch, lambda config, count: [])

        assert llm._batch_gemini(
            {"page-1": "a"}, system="sys", model="gemini-x", max_tokens=2000,
            kind="summary", project_id=None, json_only=True,
        ) == {}
        assert len(submitted) == 1

    def test_a_model_already_latched_submits_the_working_shape_first(self, monkeypatch):
        monkeypatch.setattr(llm, "_no_thinking_config", {"gemini-3.1-pro-preview"})
        submitted = self._client(monkeypatch, lambda config, count: [self._ok("page-1")])

        llm._batch_gemini(
            {"page-1": "a"}, system="sys", model="gemini-3.1-pro-preview",
            max_tokens=2000, kind="summary", project_id=None, json_only=True,
        )
        assert len(submitted) == 1 and "thinking_config" not in submitted[0]


class TestThinkingBudgetSetting:
    def test_off_omits_the_field_entirely(self, monkeypatch):
        """0 and "omitted" are different requests: a model that cannot turn
        thinking off rejects the field at any value."""
        monkeypatch.setattr(llm, "_no_thinking_config", set())
        monkeypatch.setattr(llm, "GEMINI_THINKING_BUDGET", llm._parse_thinking_budget("off"))
        assert "thinking_config" not in llm._gemini_config(model="gemini-x", max_tokens=100)

    def test_numbers_and_junk(self):
        assert llm._parse_thinking_budget("512") == 512
        assert llm._parse_thinking_budget("-1") == -1
        assert llm._parse_thinking_budget("0") == 0
        assert llm._parse_thinking_budget(None) == 0
        assert llm._parse_thinking_budget("none") is None
        assert llm._parse_thinking_budget("banana") == 0  # falls back to off, not crash


class TestARetiredModel:
    """Google removes a retired model FOR NEW KEYS first, so the same commit
    works for whoever set the deployment up and 404s for whoever makes a key
    next month. Every caller here falls back rather than stopping, which means
    the run finishes and looks fine — the log line is the only thing standing
    between that and a 400-page scrape classified by the rules ladder."""

    NOT_FOUND = (
        "404 NOT_FOUND. This model models/gemini-2.5-flash is no longer available "
        "to new users. Please update your code to use models/gemini-3.6-flash."
    )

    def _fails(self, monkeypatch, message):
        monkeypatch.setattr(llm, "_missing_model_reported", set())

        def boom(*args, **kwargs):
            raise RuntimeError(message)

        monkeypatch.setattr(llm, "_complete_gemini", boom)

    def _call(self, kind="classification"):
        return llm.complete(
            "sys", "user", provider="gemini", claude_model="claude-x",
            gemini_model="gemini-2.5-flash", max_tokens=100, kind=kind,
        )

    def test_it_is_reported_at_error_naming_the_model_and_the_stage(self, monkeypatch, caplog):
        self._fails(monkeypatch, self.NOT_FOUND)
        with caplog.at_level("ERROR"):
            assert self._call() is None
        errors = [r for r in caplog.records if r.levelname == "ERROR"]
        assert len(errors) == 1
        said = errors[0].getMessage()
        assert "gemini-2.5-flash" in said and "classification" in said
        # The operator's next move is an env var, so the line has to say that
        # rather than leaving "call failed" to be read as a blip.
        assert "configuration" in said.lower()

    def test_it_is_said_once_per_model_and_stage_not_once_per_page(self, monkeypatch, caplog):
        self._fails(monkeypatch, self.NOT_FOUND)
        with caplog.at_level("ERROR"):
            for _ in range(5):
                self._call()
        assert len([r for r in caplog.records if r.levelname == "ERROR"]) == 1

    def test_a_different_stage_on_the_same_dead_model_still_gets_told(self, monkeypatch, caplog):
        self._fails(monkeypatch, self.NOT_FOUND)
        with caplog.at_level("ERROR"):
            self._call(kind="classification")
            self._call(kind="vlm")
        assert len([r for r in caplog.records if r.levelname == "ERROR"]) == 2

    def test_an_ordinary_failure_stays_a_warning(self, monkeypatch, caplog):
        """A rate limit or a timeout IS transient and the fallback is the right
        answer for it — promoting those to ERROR would bury this one again."""
        self._fails(monkeypatch, "429 RESOURCE_EXHAUSTED: quota exceeded")
        with caplog.at_level("DEBUG"):
            assert self._call() is None
        assert not [r for r in caplog.records if r.levelname == "ERROR"]
        assert [r for r in caplog.records if r.levelname == "WARNING"]


class TestClaudeThinking:
    """Sonnet 5 runs ADAPTIVE thinking when `thinking` is omitted, where every
    earlier model ran none. So code that had never sent the field started
    reasoning on the day the model id changed — and thinking bills from
    max_tokens while `display` defaults to "omitted", so the reply arrives as
    thinking blocks carrying no text. The vision pass posted a drawing at
    max_tokens=4000, got 200 OK, joined zero text blocks into "", and logged
    "description was 0 chars" — which reads like a refusal. A whole project
    ingested with no descriptions at all.

    GEMINI_THINKING_BUDGET already encoded this decision for the other
    provider. This side had simply never had to make it."""

    class _Block:
        def __init__(self, type_, text=""):
            self.type = type_
            self.text = text

    class _Response:
        def __init__(self, blocks, stop_reason="end_turn"):
            self.content = blocks
            self.stop_reason = stop_reason
            self.usage = types.SimpleNamespace()

    def _client(self, monkeypatch, responder):
        sent = []

        def create(**kwargs):
            sent.append(kwargs)
            return responder(kwargs, len(sent))

        monkeypatch.setattr(llm, "_no_thinking_param", set())
        monkeypatch.setattr(usage, "record_message", lambda *a, **k: None)
        monkeypatch.setattr(
            llm,
            "anthropic_client",
            lambda: types.SimpleNamespace(messages=types.SimpleNamespace(create=create)),
        )
        return sent

    def _ok(self):
        return self._Response([self._Block("text", "a description")])

    def _call(self, model="claude-sonnet-5"):
        return llm._complete_claude(
            "sys", "user", model=model, max_tokens=4000, kind="vlm",
            project_id=None, cache_system=False,
        )

    def test_thinking_is_disabled_by_default(self, monkeypatch):
        monkeypatch.setattr(llm, "_CLAUDE_THINKING", "off")
        sent = self._client(monkeypatch, lambda kwargs, n: self._ok())
        assert self._call().text == "a description"
        assert sent[0]["thinking"] == {"type": "disabled"}

    def test_it_can_be_turned_back_on(self, monkeypatch):
        """Omitting the field is how you get the model's own default — which is
        adaptive on Sonnet 5. The escape hatch has to omit, not send a value."""
        monkeypatch.setattr(llm, "_CLAUDE_THINKING", "adaptive")
        sent = self._client(monkeypatch, lambda kwargs, n: self._ok())
        self._call()
        assert "thinking" not in sent[0]

    def test_a_model_that_rejects_the_field_is_retried_without_it_and_latched(
        self, monkeypatch, caplog
    ):
        """Older tiers take budget_tokens and may not accept "disabled" at all.
        One failed call per model, not one per page of a 400-page project."""
        monkeypatch.setattr(llm, "_CLAUDE_THINKING", "off")

        def responder(kwargs, n):
            if "thinking" in kwargs:
                raise RuntimeError("thinking is not supported for this model")
            return self._ok()

        sent = self._client(monkeypatch, responder)
        with caplog.at_level("WARNING"):
            assert self._call(model="claude-haiku-4-5").text == "a description"
        assert len(sent) == 2 and "thinking" not in sent[1]
        assert "claude-haiku-4-5" in llm._no_thinking_param

        sent.clear()
        self._call(model="claude-haiku-4-5")
        assert len(sent) == 1 and "thinking" not in sent[0]

    def test_an_unrelated_failure_is_not_retried(self, monkeypatch):
        """The field's value is a constant, so a thinking complaint is the only
        thing it can have caused. A 429 must not buy a second call."""
        monkeypatch.setattr(llm, "_CLAUDE_THINKING", "off")

        def responder(kwargs, n):
            raise RuntimeError("429 rate_limit_error")

        sent = self._client(monkeypatch, responder)
        with pytest.raises(RuntimeError):
            self._call()
        assert len(sent) == 1 and not llm._no_thinking_param

    def test_a_reply_of_pure_thinking_says_what_it_was_made_of(self, monkeypatch, caplog):
        """The measured failure. An empty string is what the caller sees either
        way — so the log has to say whether the model said nothing or spent the
        whole budget before it could."""
        monkeypatch.setattr(llm, "_CLAUDE_THINKING", "adaptive")
        self._client(
            monkeypatch,
            lambda kwargs, n: self._Response(
                [self._Block("thinking")], stop_reason="max_tokens"
            ),
        )
        with caplog.at_level("WARNING"):
            reply = self._call()
        assert reply.text == ""
        assert "returned no text" in caplog.text
        assert "1x thinking" in caplog.text
        assert "max_tokens" in caplog.text

    def test_a_reply_with_text_says_nothing(self, monkeypatch, caplog):
        monkeypatch.setattr(llm, "_CLAUDE_THINKING", "off")
        self._client(monkeypatch, lambda kwargs, n: self._ok())
        with caplog.at_level("WARNING"):
            self._call()
        assert "returned no text" not in caplog.text


class TestMediaResolution:
    """How many pixels of an image the model actually reads.

    The measured failure: VLM_MAX_EDGE=5000 rendered a 42x30in sheet at 119
    DPI, logged 119 DPI, and was read at 73 — Gemini scales an image into
    3072x3072 and then tokenizes it to a fixed per-part budget. The run scored
    the column tag at 14% and read as evidence that resolution is not the
    limit. It was evidence that the experiment had not run.
    """

    def test_an_image_part_carries_the_resolution_on_a_level_model(self, monkeypatch):
        monkeypatch.setattr(llm, "_no_media_resolution", set())
        monkeypatch.setattr(llm, "GEMINI_MEDIA_RESOLUTION", "ultra_high")
        parts = llm._user_content_gemini("describe", [b"png"], model="gemini-3.6-flash")
        assert parts[0]["media_resolution"] == {"level": "MEDIA_RESOLUTION_ULTRA_HIGH"}
        assert parts[0]["inline_data"]["data"] == b"png"
        assert parts[-1] == "describe"

    def test_ultra_high_is_the_default_because_only_it_changes_what_is_read(self):
        """HIGH is what an unspecified field already means, so defaulting there
        would ship a no-op named after a fix. ULTRA_HIGH is the only rung above
        the provider's own default, and it exists only per-part."""
        assert llm._MEDIA_LEVELS[-1] == "ultra_high"
        assert llm.GEMINI_MEDIA_RESOLUTION in llm._MEDIA_LEVELS

    def test_a_pre_3_model_is_sent_no_field(self, monkeypatch):
        """Same version sniff as the thinking level, for the same reason: a
        list of model names expires on a schedule this repo does not control."""
        monkeypatch.setattr(llm, "_no_media_resolution", set())
        parts = llm._user_content_gemini("describe", [b"png"], model="gemini-2.5-flash")
        assert "media_resolution" not in parts[0]
        assert llm._takes_media_resolution("gemini-4-flash")

    def test_a_call_with_no_image_sends_the_string_it_always_sent(self):
        """A cache breakpoint is a prefix match. Reshaping the user turn for
        every text-only call site would have cost them all their cached prefix
        on the day this shipped."""
        assert llm._user_content_gemini("hello", None, model="gemini-3.6-flash") == "hello"
        assert llm._user_content_gemini("hello", [], model="gemini-3.6-flash") == "hello"

    def test_a_refusal_is_latched_so_the_400_is_paid_once(self, monkeypatch):
        monkeypatch.setattr(llm, "_no_media_resolution", set())
        llm._latch_no_media("gemini-3.6-flash", "unsupported")
        assert not llm._takes_media_resolution("gemini-3.6-flash")
        parts = llm._user_content_gemini("describe", [b"png"], model="gemini-3.6-flash")
        assert "media_resolution" not in parts[0]

    def test_the_refusal_matcher_must_name_the_field(self):
        """Deliberately narrower than the thinking matcher, which had to widen
        to a bare INVALID_ARGUMENT. There the fallback is another thinking
        setting; here it is reading the sheet at the provider's default, so a
        match on an unrelated 400 would silently undo the only lever this pass
        has left short of cropping."""
        assert llm._is_media_refusal("media_resolution is not supported")
        assert llm._is_media_refusal("Unknown field: media resolution")
        assert not llm._is_media_refusal("400 INVALID_ARGUMENT")
        assert not llm._is_media_refusal("429 rate limit exceeded")
