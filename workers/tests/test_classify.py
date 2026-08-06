import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import classify  # noqa: E402
from classify import (  # noqa: E402
    build_portions,
    classify_by_filename,
    classify_by_rules,
    classify_pages,
    group_portions,
    fill_unresolved,
    parse_sheet_response,
    title_block_snippet,
)


class TestParseSheetResponse:
    """The model reports the sheet number; PREFIX_TO_DISCIPLINE maps it."""

    def test_valid_with_prefix(self):
        assert parse_sheet_response(
            '{"sheet_number": "S-003.0", "prefix": "S", "confidence": 0.95}'
        ) == ("S-003.0", "structural")

    def test_two_letter_prefix(self):
        assert parse_sheet_response(
            '{"sheet_number": "FP-2", "prefix": "FP", "confidence": 0.9}'
        ) == ("FP-2", "fire_protection")

    def test_prefix_derived_from_sheet_number_when_missing(self):
        assert parse_sheet_response(
            '{"sheet_number": "A17-11", "prefix": null, "confidence": 0.9}'
        ) == ("A17-11", "architectural")
        # Two-letter prefixes win when derived.
        assert parse_sheet_response(
            '{"sheet_number": "IT-101", "prefix": null, "confidence": 0.9}'
        ) == ("IT-101", "information_technology")

    def test_code_fences_tolerated(self):
        assert parse_sheet_response(
            '```json\n{"sheet_number": "M301", "prefix": "M", "confidence": 0.8}\n```'
        ) == ("M301", "mechanical")

    def test_low_confidence_rejected(self):
        assert (
            parse_sheet_response('{"sheet_number": "S-101", "prefix": "S", "confidence": 0.2}')
            is None
        )

    def test_no_sheet_number_rejected(self):
        assert (
            parse_sheet_response('{"sheet_number": null, "prefix": null, "confidence": 0.9}')
            is None
        )

    def test_unknown_prefix_rejected(self):
        assert (
            parse_sheet_response('{"sheet_number": "ZZ-1", "prefix": "ZZ", "confidence": 0.9}')
            is None
        )

    def test_garbage_rejected(self):
        assert parse_sheet_response("The sheet number is S-003.0") is None
        assert parse_sheet_response("") is None


class TestClassifyPagesUsesAI:
    def test_ai_extraction_wins_over_pattern_match(self, monkeypatch):
        # Page text where the pattern matcher would be misled, but the model
        # reports the real sheet number.
        pages = [(1, "NC License No. F-1105\nS-003.0\nGENERAL NOTES")]
        monkeypatch.setenv("SHEET_EXTRACTION", "ai")
        monkeypatch.setattr(
            classify, "extract_sheet_by_ai", lambda text, fname, redis: ("S-003.0", "structural")
        )
        assert classify_pages(pages) == ["structural"]

    def test_falls_back_to_rules_when_ai_unavailable(self, monkeypatch):
        pages = [(1, "DRAWING\nS-101\nNOTES")]
        monkeypatch.setenv("SHEET_EXTRACTION", "ai")
        monkeypatch.setattr(classify, "extract_sheet_by_ai", lambda text, fname, redis: None)
        assert classify_pages(pages) == ["structural"]

    def test_rules_mode_skips_ai(self, monkeypatch):
        called = []
        monkeypatch.setenv("SHEET_EXTRACTION", "rules")
        monkeypatch.setattr(
            classify,
            "extract_sheet_by_ai",
            lambda text, fname, redis: called.append(1) or ("X-1", "other"),
        )
        assert classify_pages([(1, "SHEET S-101")]) == ["structural"]
        assert called == []


class TestClassifyByFilename:
    def test_leading_sheet_number(self):
        assert classify_by_filename("A00-01 - GENERAL NOTES, SYMBOLS.pdf") == "architectural"
        assert classify_by_filename("A17-11 EQUIPMENT PLANS.pdf") == "architectural"
        assert classify_by_filename("S-201 FOUNDATION.pdf") == "structural"
        assert classify_by_filename("M301.pdf") == "mechanical"
        assert classify_by_filename("FA-1 FIRE ALARM.pdf") == "fire_alarm"

    def test_lowercase_filename(self):
        assert classify_by_filename("e1.1 lighting.pdf") == "electrical"

    def test_no_leading_sheet_number(self):
        assert classify_by_filename("EQUIPMENT PLANS - STUDENT BENCH TYPES.pdf") is None
        assert classify_by_filename("cover sheet.pdf") is None
        assert classify_by_filename(None) is None


class TestStrongTokenPreference:
    def test_real_sheet_number_beats_content_callouts(self):
        # A page full of equipment labels (TYPE E2, C1 …) but its real sheet
        # number is A17-11 → Architectural, not Electrical/Civil.
        text = (
            "TYPE E2 PLAN\nTYPE C1 FRONT\nTYPE D2 BACK\n"
            "EQUIPMENT PLANS - STUDENT BENCH TYPES\nA17-11"
        )
        assert classify_by_rules(text) == "architectural"

    def test_weak_only_still_classifies(self):
        # No strong token — fall back to the last weak token.
        assert classify_by_rules("SEE TYPE C1 AND E2") == "electrical"

    def test_sheet_number_on_own_line_beats_license_number(self):
        # Real title block: the sheet number S-003.0 sits alone on its line,
        # while "NC License No. F-1105" is the engineer's license — it must not
        # be read as a Fire Protection sheet.
        text = (
            "EARLY STRUCTURAL & UNDERGROUND UTILITY PACKAGE\n"
            "S-003.0\n"
            "GENERAL NOTES\n"
            "Carolina Golf Club\n"
            "NC License No. F-1105\n"
            "formerly KingGuinn Associates\n"
        )
        assert classify_by_rules(text) == "structural"

    def test_other_title_block_numbers_are_disqualified(self):
        for noise in [
            "Job Number: E-2201",
            "TEL 704 597 1340  FAX P-1365",
            "PERMIT NO. M-4410",
            "SUITE E-200",
        ]:
            text = f"DRAWING\nS-101\nNOTES\n{noise}\n"
            assert classify_by_rules(text) == "structural", noise


class TestClassifyByRules:
    def test_prefix_table(self):
        cases = {
            "G02-02": "general",
            "A-101": "architectural",
            "S201": "structural",
            "C-100": "civil",
            "L2.01": "landscape",
            "I-110": "interiors",
            "M301": "mechanical",
            "H102": "hvac",
            "P-3": "plumbing",
            "E1.1": "electrical",
            "F-201": "fire_protection",
            "FP-2": "fire_protection",
            "FA-1": "fire_alarm",
            "T-100": "telecommunications",
            "IT-101": "information_technology",
            "AV-2": "audio_visual",
            "X-9": "other",
        }
        for token, expected in cases.items():
            assert classify_by_rules(f"TITLE BLOCK SHEET {token}") == expected, token

    def test_longer_prefixes_beat_single_letters(self):
        # Two-letter prefixes must not be read as single-letter + number.
        assert classify_by_rules("SHEET FP-101") == "fire_protection"
        assert classify_by_rules("SHEET FA-101") == "fire_alarm"
        assert classify_by_rules("SHEET IT-101") == "information_technology"
        assert classify_by_rules("SHEET AV-101") == "audio_visual"
        # ...and the single-letter forms still map correctly.
        assert classify_by_rules("SHEET F-101") == "fire_protection"
        assert classify_by_rules("SHEET I-101") == "interiors"
        assert classify_by_rules("SHEET A-101") == "architectural"

    def test_last_token_wins_title_block_at_end(self):
        # Notes referencing other sheets appear before the title block
        text = "SEE DETAIL ON A-501 FOR TYPICAL\n...\nSHEET NUMBER\nS-201"
        assert classify_by_rules(text) == "structural"

    def test_no_match(self):
        assert classify_by_rules("GENERAL NOTES WITHOUT SHEET NUMBER") is None
        assert classify_by_rules("") is None
        assert classify_by_rules(None) is None

    def test_lowercase_prose_does_not_match(self):
        assert classify_by_rules("see page 4 for more, a 12 mm bolt") is None


class TestTitleBlockSnippet:
    def test_returns_tail(self):
        text = "x" * 2000 + "TITLE"
        snippet = title_block_snippet(text, limit=100)
        assert snippet.endswith("TITLE")
        assert len(snippet) <= 100


class TestFillUnresolved:
    def test_inherit_forward(self):
        assert fill_unresolved(["structural", None, None, "electrical"]) == [
            "structural",
            "structural",
            "structural",
            "electrical",
        ]

    def test_leading_inherits_backward(self):
        assert fill_unresolved([None, None, "civil"]) == ["civil", "civil", "civil"]

    def test_all_unresolved_becomes_unclassified(self):
        assert fill_unresolved([None, None]) == ["unclassified", "unclassified"]


class TestBuildPortions:
    def test_contiguous_runs(self):
        pages = [
            (1, "SHEET A-101"),
            (2, "SHEET A-102"),
            (3, "SHEET S-201"),
            (4, "SHEET S-202"),
            (5, "SHEET E-301"),
        ]
        result = build_portions(pages)
        assert [(p["name"], p["startPage"], p["endPage"]) for p in result] == [
            ("Architectural", 1, 2),
            ("Structural", 3, 4),
            ("Electrical", 5, 5),
        ]

    def test_non_contiguous_discipline_collapses_to_one_portion(self):
        # Interleaved sheets → ONE portion per discipline (no "(2)" suffixes),
        # ordered by first appearance; the span covers all the discipline's pages.
        pages = [
            (1, "SHEET A-101"),
            (2, "SHEET S-201"),
            (3, "SHEET A-201"),
        ]
        result = build_portions(pages)
        assert [(p["name"], p["discipline"], p["startPage"], p["endPage"]) for p in result] == [
            ("Architectural", "architectural", 1, 3),
            ("Structural", "structural", 2, 2),
        ]

    def test_unclassified_page_inherits_previous_run(self):
        pages = [
            (1, "SHEET A-101"),
            (2, "no sheet token here"),
            (3, "SHEET S-201"),
        ]
        result = build_portions(pages)
        assert [(p["name"], p["startPage"], p["endPage"]) for p in result] == [
            ("Architectural", 1, 2),
            ("Structural", 3, 3),
        ]

    def test_empty(self):
        assert build_portions([]) == []


class TestGroupPortionsPageCount:
    """pageCount is what the category cards show, and it is NOT the span when
    disciplines interleave."""

    def test_page_count_counts_pages_not_span(self):
        pages = [(1, ""), (2, ""), (3, ""), (4, "")]
        disciplines = ["architectural", "structural", "architectural", "structural"]
        result = group_portions(pages, disciplines)
        by_name = {p["name"]: p for p in result}
        assert by_name["Architectural"]["pageCount"] == 2
        assert (by_name["Architectural"]["startPage"], by_name["Architectural"]["endPage"]) == (1, 3)
        assert by_name["Structural"]["pageCount"] == 2
        assert (by_name["Structural"]["startPage"], by_name["Structural"]["endPage"]) == (2, 4)

    def test_contiguous_page_count_matches_span(self):
        pages = [(1, ""), (2, ""), (3, "")]
        result = group_portions(pages, ["civil"] * 3)
        assert result[0]["pageCount"] == 3
        assert (result[0]["startPage"], result[0]["endPage"]) == (1, 3)


class TestClassifyRegionText:
    """Region path: the scraped box is the ONLY input. With no API key the
    rules ladder still has to resolve a discipline, so offline runs work."""

    def test_rules_fallback_reads_the_scraped_sheet_number(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setattr(classify, "_client", None)
        monkeypatch.setattr(classify, "_client_unavailable", True)
        sheet, discipline, source = classify.classify_region_text("S-003.0", None, None)
        assert (sheet, discipline, source) == ("S-003.0", "structural", "region_rules")

    def test_falls_back_to_the_filename_when_the_box_is_empty(self, monkeypatch):
        monkeypatch.setattr(classify, "_client_unavailable", True)
        assert classify.classify_region_text("", "E1.1 POWER PLAN.pdf", None) == (
            None,
            "electrical",
            "filename",
        )

    def test_unreadable_box_and_filename_resolve_to_nothing(self, monkeypatch):
        monkeypatch.setattr(classify, "_client_unavailable", True)
        assert classify.classify_region_text("SCALE 1/4\" = 1'-0\"", "plans.pdf", None) == (
            None,
            None,
            None,
        )

    def test_ai_result_wins_and_is_reported_as_region_ai(self, monkeypatch):
        monkeypatch.setenv("SHEET_EXTRACTION", "ai")
        monkeypatch.setattr(
            classify, "extract_sheet_from_region", lambda *a, **k: ("FP-2", "fire_protection")
        )
        assert classify.classify_region_text("FP-2 FIRE PROTECTION", None, None) == (
            "FP-2",
            "fire_protection",
            "region_ai",
        )

    def test_rules_mode_skips_the_model_entirely(self, monkeypatch):
        monkeypatch.setenv("SHEET_EXTRACTION", "rules")

        def _boom(*args, **kwargs):
            raise AssertionError("the model must not be called with SHEET_EXTRACTION=rules")

        monkeypatch.setattr(classify, "extract_sheet_from_region", _boom)
        assert classify.classify_region_text("M301", None, None)[1] == "mechanical"


class TestRegionCache:
    """The region cache key is the hash of the scraped string — hundreds of
    sheets share a box layout, so a cached 'no answer' must stay negative."""

    class _FakeRedis:
        def __init__(self, value):
            self.value = value
            self.sets = []

        def get(self, key):
            return self.value

        def set(self, key, value, ex=None):
            self.sets.append((key, value))

    def test_cache_hit_returns_without_calling_the_model(self, monkeypatch):
        monkeypatch.setattr(
            classify, "_get_client", lambda: (_ for _ in ()).throw(AssertionError("called"))
        )
        cached = classify.extract_sheet_from_region("S-003.0", None, self._FakeRedis(b"S-003.0|structural"))
        assert cached == ("S-003.0", "structural")

    def test_cached_empty_string_means_no_answer(self, monkeypatch):
        monkeypatch.setattr(
            classify, "_get_client", lambda: (_ for _ in ()).throw(AssertionError("called"))
        )
        assert classify.extract_sheet_from_region("nothing here", None, self._FakeRedis(b"")) is None

    def test_blank_region_never_reaches_the_model(self):
        assert classify.extract_sheet_from_region("   ", None, None) is None
