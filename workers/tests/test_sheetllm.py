"""Provider switching for sheet-number extraction.

The point of these is the invariant: swapping Claude for Gemini changes WHO
reads the sheet number off the scraped title block, and nothing else. The JSON
contract, the parser and the prefix→discipline table are shared, so the two
providers must be directly comparable — and their cached answers must never be
served for one another.
"""

import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import classify  # noqa: E402
import llm  # noqa: E402
import sheetllm  # noqa: E402


class TestProviderSelection:
    def test_defaults_to_claude(self, monkeypatch):
        monkeypatch.delenv("SHEET_PROVIDER", raising=False)
        assert sheetllm.provider() == "claude"

    def test_gemini_selected_by_env(self, monkeypatch):
        monkeypatch.setenv("SHEET_PROVIDER", "gemini")
        assert sheetllm.provider() == "gemini"

    def test_case_and_whitespace_tolerated(self, monkeypatch):
        monkeypatch.setenv("SHEET_PROVIDER", "  GEMINI ")
        assert sheetllm.provider() == "gemini"

    def test_unknown_provider_falls_back_rather_than_crashing(self, monkeypatch):
        """A typo in an env var must not take sheet reading offline."""
        monkeypatch.setenv("SHEET_PROVIDER", "gpt4")
        assert sheetllm.provider() == "claude"

    def test_model_name_follows_the_provider(self, monkeypatch):
        monkeypatch.setenv("SHEET_PROVIDER", "gemini")
        assert sheetllm.model_name() == sheetllm.GEMINI_MODEL
        monkeypatch.setenv("SHEET_PROVIDER", "claude")
        assert sheetllm.model_name() == sheetllm.CLAUDE_MODEL

    def test_missing_key_disables_that_provider_quietly(self, monkeypatch):
        """No key means fall through to the rules ladder, not an exception."""
        monkeypatch.setenv("SHEET_PROVIDER", "gemini")
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        module = importlib.reload(sheetllm)
        assert module.complete_json("system", "user") is None


class TestSharedContract:
    """Whatever answers, the SAME parser and table decide the discipline.

    These turn the rules-first pre-pass OFF on purpose: it resolves an
    unambiguous box like "S-003.0" without any provider, which is the point of
    it — but it would mean these tests never reached the provider they claim to
    be comparing.
    """

    RESPONSE = '{"sheet_number": "S-003.0", "prefix": "S", "confidence": 0.95}'

    def _extract(self, monkeypatch, provider):
        monkeypatch.setenv("SHEET_PROVIDER", provider)
        monkeypatch.setenv("SHEET_EXTRACTION", "ai")
        monkeypatch.setenv("SHEET_RULES_FIRST", "false")
        monkeypatch.setattr(
            classify.sheetllm,
            "complete_json",
            lambda system, user, project=None, max_tokens=None: self.RESPONSE,
        )
        return classify.classify_region_text("S-003.0 SPECIAL INSPECTIONS", None, None)

    def test_both_providers_produce_the_same_discipline(self, monkeypatch):
        assert self._extract(monkeypatch, "claude") == self._extract(monkeypatch, "gemini")

    def test_the_table_decides_not_the_model(self, monkeypatch):
        sheet, discipline, source = self._extract(monkeypatch, "gemini")
        assert (sheet, discipline, source) == ("S-003.0", "structural", "region_ai")

    def test_a_model_naming_a_discipline_is_ignored(self, monkeypatch):
        """Guards the core invariant: only the sheet number is taken from the
        model. A response that tries to pick the discipline itself is parsed
        for its prefix and nothing else."""
        monkeypatch.setenv("SHEET_PROVIDER", "gemini")
        monkeypatch.setenv("SHEET_EXTRACTION", "ai")
        monkeypatch.setenv("SHEET_RULES_FIRST", "false")
        monkeypatch.setattr(
            classify.sheetllm,
            "complete_json",
            lambda system, user, project=None, max_tokens=None: (
                '{"sheet_number": "M301", "prefix": "M", "confidence": 0.9,'
                ' "discipline": "electrical"}'
            ),
        )
        assert classify.classify_region_text("M301", None, None)[1] == "mechanical"

    def test_provider_failure_falls_back_to_rules(self, monkeypatch):
        monkeypatch.setenv("SHEET_PROVIDER", "gemini")
        monkeypatch.setenv("SHEET_EXTRACTION", "ai")
        monkeypatch.setenv("SHEET_RULES_FIRST", "false")
        monkeypatch.setattr(
            classify.sheetllm,
            "complete_json",
            lambda system, user, project=None, max_tokens=None: None,
        )
        sheet, discipline, source = classify.classify_region_text("E1.1 POWER PLAN", None, None)
        assert discipline == "electrical"
        assert source == "region_rules"


class TestCacheIsolation:
    """Cached answers must not cross providers, or a comparison between them
    would silently be a comparison of one with itself."""

    class _Redis:
        def __init__(self):
            self.store: dict[str, bytes] = {}

        def get(self, key):
            return self.store.get(key)

        def set(self, key, value, ex=None):
            self.store[key] = value.encode() if isinstance(value, str) else value

    def test_each_provider_gets_its_own_key(self, monkeypatch):
        redis = self._Redis()
        monkeypatch.setattr(
            classify.sheetllm,
            "complete_json",
            lambda system, user, project=None, max_tokens=None: (
                '{"sheet_number": "A-101", "prefix": "A", "confidence": 0.9}'
            ),
        )

        monkeypatch.setenv("SHEET_PROVIDER", "claude")
        classify.extract_sheet_from_region("A-101", None, redis)
        monkeypatch.setenv("SHEET_PROVIDER", "gemini")
        classify.extract_sheet_from_region("A-101", None, redis)

        keys = sorted(redis.store)
        assert len(keys) == 2, keys
        assert any(":claude:" in k for k in keys)
        assert any(":gemini:" in k for k in keys)

    def test_a_cached_answer_is_reused_for_the_same_provider(self, monkeypatch):
        redis = self._Redis()
        calls = {"n": 0}

        def once(system, user, project=None, max_tokens=None):
            calls["n"] += 1
            return '{"sheet_number": "A-101", "prefix": "A", "confidence": 0.9}'

        monkeypatch.setenv("SHEET_PROVIDER", "claude")
        monkeypatch.setattr(classify.sheetllm, "complete_json", once)
        classify.extract_sheet_from_region("A-101", None, redis)
        classify.extract_sheet_from_region("A-101", None, redis)
        assert calls["n"] == 1


class TestOutputCap:
    """A batched read asks for many answers in one array, so it must be able to
    raise the cap. Sizing a 25-page batch at one page's cap truncates the JSON
    mid-array, and a truncated array parses as "no answer" for the tail —
    silently sending those pages back to the rules ladder."""

    def _cap(self, monkeypatch, **kwargs):
        seen = {}

        def capture(system, user, **kw):
            seen.update(kw)
            return llm.Reply(text="{}")

        monkeypatch.setattr(llm, "complete", capture)
        sheetllm.complete_json("system", "user", **kwargs)
        return seen["max_tokens"]

    def test_a_single_page_read_uses_the_default_cap(self, monkeypatch):
        assert self._cap(monkeypatch) == sheetllm.MAX_OUTPUT_TOKENS

    def test_a_batched_read_can_raise_it(self, monkeypatch):
        wanted = classify._batch_max_tokens(25)
        assert wanted > sheetllm.MAX_OUTPUT_TOKENS
        assert self._cap(monkeypatch, max_tokens=wanted) == wanted
