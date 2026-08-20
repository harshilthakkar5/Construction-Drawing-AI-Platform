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

    def test_batching_is_anthropic_only(self, monkeypatch):
        monkeypatch.setenv("SUMMARY_PROVIDER", "gemini")
        assert not summarize.batch_supported()
        monkeypatch.setenv("SUMMARY_PROVIDER", "claude")
        assert summarize.batch_supported()

    def test_an_unavailable_provider_yields_no_summary_rather_than_an_error(
        self, monkeypatch
    ):
        """_call_direct returning empty text is what the strict parser then
        rejects, so the page is skipped like any other unusable answer."""
        monkeypatch.setattr(llm, "complete", lambda *a, **k: None)
        assert summarize._call_direct("prompt") == ("", None)
        assert summarize.parse_summary_json("", {"chunk-1"}) is None
