"""The reading guide (glossary + worked examples) added to review prompts."""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import rfi_review  # noqa: E402

CASES = (Path(__file__).resolve().parents[2] / "benchmarks" / "rfi_eval_cases.json").read_text()
# sheet numbers (S7.42, A3.01, S-501), marks (GB-9, PC4, C-30), dimensions (2'-2", 1'-10")
TOKEN = re.compile(r"""\b[A-Z]{1,3}-?\d+(?:\.\d+)?[A-Z]?\b|\d+'-\d+(?:\s?\d+/\d+)?"?""")


def test_off_by_default_leaves_every_prompt_unchanged(monkeypatch):
    monkeypatch.setattr(rfi_review, "REVIEW_GUIDE", False)
    for prompt in (rfi_review.discovery_system(), rfi_review.reasoning_system(["G01"]), rfi_review.verification_system()):
        assert "<glossary>" not in prompt and "<worked_examples>" not in prompt


def test_on_gives_reading_the_glossary_and_judging_the_examples(monkeypatch):
    monkeypatch.setattr(rfi_review, "REVIEW_GUIDE", True)
    discovery = rfi_review.discovery_system()
    assert "<glossary>" in discovery and "<worked_examples>" not in discovery
    for prompt in (rfi_review.reasoning_system(["G01"]), rfi_review.verification_system()):
        assert "<glossary>" in prompt and "<worked_examples>" in prompt


def test_review_guide_never_seeds_the_answer_key():
    tokens = set(TOKEN.findall(rfi_review.GLOSSARY + rfi_review.EXAMPLES))
    assert {"S7.42", "GB-9", "2'-2\""} <= tokens  # the pattern really reads the examples
    seeded = sorted(t for t in tokens if t in CASES)
    assert not seeded, f"guide repeats values from the RFI answer key: {seeded}"
