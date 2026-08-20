"""Sheet-number extraction backend: Claude or Gemini, chosen by env.

The provider is a TRANSPORT detail and nothing more. Both are asked for the
same JSON object, `classify.parse_sheet_response` validates it, and
`PREFIX_TO_DISCIPLINE` maps the reported prefix to a discipline. Swapping
providers therefore cannot change how a discipline is decided — only who reads
the sheet number off the scraped title block. That invariant is the reason this
module returns raw text rather than a discipline.

    SHEET_PROVIDER=claude   (default)  CLASSIFIER_MODEL / ANTHROPIC_API_KEY
    SHEET_PROVIDER=gemini              GEMINI_MODEL     / GEMINI_API_KEY

Either way, a provider that is unavailable or errors returns None and the
caller falls back to pattern matching, so detection keeps working with no keys
at all.

The two transports themselves live in workers/src/llm.py, shared with the
summarizer — this module is the sheet-reading call site's own switch and model
defaults.
"""

from __future__ import annotations

import os

import llm

CLAUDE_MODEL = os.environ.get("CLASSIFIER_MODEL", "claude-haiku-4-5-20251001")
# Configurable because model names move faster than this repo does; a name the
# API rejects simply falls back to the rules path, and only this var changes.
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

# One page's answer is a dozen tokens of JSON; the cap exists to bound a
# runaway. A batched read overrides it per call — sizing it for one entry while
# asking for twenty-five truncates the array, and a truncated array reads as
# "the model had no answer for the last twenty pages".
MAX_OUTPUT_TOKENS = 120


def provider() -> str:
    return llm.resolve("SHEET_PROVIDER")


def model_name() -> str:
    return llm.model_for(provider(), CLAUDE_MODEL, GEMINI_MODEL)


def complete_json(
    system: str,
    user: str,
    project_id: str | None = None,
    max_tokens: int | None = None,
) -> str | None:
    """Ask the active provider for the sheet-number JSON.

    Returns the raw response text, or None when the provider is unavailable or
    the call failed — the caller then falls back to pattern matching rather
    than leaving the page unclassified.
    """
    reply = llm.complete(
        system,
        user,
        provider=provider(),
        claude_model=CLAUDE_MODEL,
        gemini_model=GEMINI_MODEL,
        max_tokens=MAX_OUTPUT_TOKENS,
        kind="classification",
        project_id=project_id,
        # The instructions are identical for every page — cache them.
        cache_system=True,
        json_only=True,
    )
    return reply.text if reply is not None else None
