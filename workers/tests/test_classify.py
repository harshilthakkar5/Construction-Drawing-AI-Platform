import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from classify import (  # noqa: E402
    build_portions,
    classify_by_rules,
    fill_unresolved,
    parse_haiku_response,
    title_block_snippet,
)


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


class TestParseHaikuResponse:
    def test_valid(self):
        assert (
            parse_haiku_response('{"discipline": "structural", "confidence": 0.9}')
            == "structural"
        )

    def test_low_confidence_rejected(self):
        assert parse_haiku_response('{"discipline": "structural", "confidence": 0.3}') is None

    def test_invalid_discipline_rejected(self):
        assert parse_haiku_response('{"discipline": "nuclear", "confidence": 0.9}') is None
        assert parse_haiku_response('{"discipline": "unclassified", "confidence": 0.9}') is None

    def test_garbage_rejected(self):
        assert parse_haiku_response("The discipline is structural.") is None
        assert parse_haiku_response("") is None
        assert parse_haiku_response('["structural"]') is None
        assert parse_haiku_response('{"discipline": "structural"}') is None


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
