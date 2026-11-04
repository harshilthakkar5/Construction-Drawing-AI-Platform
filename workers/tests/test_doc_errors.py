"""Failures become a kind the UI explains in plain words (doc_errors.py)."""
import os
import re

import pytest

import doc_errors
import processing

SHARED = os.path.join(os.path.dirname(__file__), "..", "..", "packages", "shared", "src", "index.ts")


@pytest.mark.parametrize(
    "exc, kind",
    [
        (RuntimeError("Your credit balance is too low to access the Anthropic API"), "ai_credits"),
        (RuntimeError("429 RESOURCE_EXHAUSTED: You exceeded your current quota"), "ai_credits"),
        (RuntimeError("Error code: 401 - invalid x-api-key"), "ai_key"),
        (RuntimeError("API key not valid. Please pass a valid API key."), "ai_key"),
        (RuntimeError("Error code: 529 overloaded_error"), "ai_busy"),
        (RuntimeError("rate limit exceeded, retry later"), "ai_busy"),
        (RuntimeError("An error occurred (NoSuchKey) when calling the GetObject operation"), "storage"),
        (RuntimeError("cannot open broken document"), "pdf_damaged"),
        (MemoryError(), "memory"),
        (OSError(28, "No space left on device"), "disk"),
        (ConnectionError("Connection refused"), "connection"),
        (ValueError("something odd"), "unknown"),
    ],
)
def test_classify(exc, kind):
    assert doc_errors.classify(exc)[0] == kind


def test_the_cause_is_read_too():
    try:
        try:
            raise RuntimeError("Your credit balance is too low")
        except RuntimeError as inner:
            raise ValueError("embedding failed") from inner
    except ValueError as outer:
        assert doc_errors.classify(outer)[0] == "ai_credits"


def test_detail_hides_keys_and_signed_urls():
    _, detail = doc_errors.classify(
        RuntimeError("bad key sk-ant-abcdefghijk at https://x/y?X-Amz-Signature=abc123&a=1")
    )
    assert "sk-ant" not in detail and "abc123" not in detail


def test_every_kind_has_words_in_the_shared_list():
    text = open(SHARED, encoding="utf-8").read()
    block = text[text.index("export const DOCUMENT_PROBLEMS"):text.index("} as const;\nexport type DocumentProblemKind")]
    shared = set(re.findall(r"^  (\w+): \{", block, re.M))
    assert set(doc_errors.KINDS) <= shared


def test_every_stage_written_is_in_the_shared_list():
    text = open(SHARED, encoding="utf-8").read()
    block = text[text.index("export const DOCUMENT_STAGES"):]
    block = block[:block.index("} as const;")]
    shared = set(re.findall(r"^  (\w+): \{", block, re.M))
    source = open(processing.__file__, encoding="utf-8").read()
    written = set(re.findall(r'progress\.stage\("(\w+)"', source))
    assert written and written <= shared


def test_page_progress_is_throttled_but_stage_changes_are_not(monkeypatch):
    writes = []
    monkeypatch.setattr(processing.db, "set_document_progress", lambda _id, data: writes.append(data))
    progress = processing.Progress("doc-1")
    progress.stage("extract", 1, 10, throttle=True)
    progress.stage("extract", 2, 10, throttle=True)  # within 2s: skipped
    progress.stage("numbering")                      # a new stage: always written
    assert [(w["stage"], w.get("done")) for w in writes] == [("extract", 1), ("numbering", None)]
