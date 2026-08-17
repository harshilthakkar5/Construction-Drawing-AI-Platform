"""Sheet-number extraction backends: Claude or Gemini, chosen by env.

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
"""

from __future__ import annotations

import os

import logutil

log = logutil.get("sheetllm")

CLAUDE_MODEL = os.environ.get("CLASSIFIER_MODEL", "claude-haiku-4-5-20251001")
# Configurable because model names move faster than this repo does; a name the
# API rejects simply falls back to the rules path, and only this var changes.
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

# The answer is a dozen tokens of JSON; the cap exists to bound a runaway.
MAX_OUTPUT_TOKENS = 120


def provider() -> str:
    """Active provider slug. Read per call, not cached at import, so tests and
    a restart-free config change both take effect."""
    name = os.environ.get("SHEET_PROVIDER", "claude").strip().lower()
    return name if name in ("claude", "gemini") else "claude"


def model_name() -> str:
    return GEMINI_MODEL if provider() == "gemini" else CLAUDE_MODEL


# --- Claude ---------------------------------------------------------------

_anthropic = None
_anthropic_unavailable = False


def _anthropic_client():
    global _anthropic, _anthropic_unavailable
    if _anthropic is not None or _anthropic_unavailable:
        return _anthropic
    if not os.environ.get("ANTHROPIC_API_KEY"):
        log.warning("ANTHROPIC_API_KEY not set — Claude sheet extraction disabled")
        _anthropic_unavailable = True
        return None
    try:
        import anthropic

        _anthropic = anthropic.Anthropic(base_url=os.environ.get("ANTHROPIC_BASE_URL") or None)
    except Exception as exc:
        log.warning("anthropic SDK unavailable: %s", exc)
        _anthropic_unavailable = True
    return _anthropic


def _complete_claude(system: str, user: str, project_id: str | None) -> str | None:
    client = _anthropic_client()
    if client is None:
        return None
    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=MAX_OUTPUT_TOKENS,
        # The instructions are identical for every page — cache them.
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user}],
    )
    import usage

    usage.record_message(project_id, "classification", CLAUDE_MODEL, response.usage)
    return response.content[0].text


# --- Gemini ---------------------------------------------------------------

_gemini = None
_gemini_unavailable = False


def _gemini_client():
    global _gemini, _gemini_unavailable
    if _gemini is not None or _gemini_unavailable:
        return _gemini
    if not os.environ.get("GEMINI_API_KEY"):
        log.warning("GEMINI_API_KEY not set — Gemini sheet extraction disabled")
        _gemini_unavailable = True
        return None
    try:
        from google import genai

        _gemini = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    except Exception as exc:
        log.warning("google-genai SDK unavailable: %s", exc)
        _gemini_unavailable = True
    return _gemini


def _complete_gemini(system: str, user: str, project_id: str | None) -> str | None:
    client = _gemini_client()
    if client is None:
        return None
    from google.genai import types

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=user,
        config=types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=MAX_OUTPUT_TOKENS,
            temperature=0,
            # Ask for JSON directly — the shared parser is strict, and this
            # removes the "here is your JSON:" preamble that would fail it.
            response_mime_type="application/json",
        ),
    )

    import usage

    meta = getattr(response, "usage_metadata", None)
    usage.record(
        project_id,
        "classification",
        GEMINI_MODEL,
        input_tokens=getattr(meta, "prompt_token_count", 0) or 0,
        output_tokens=getattr(meta, "candidates_token_count", 0) or 0,
    )
    return response.text


# --- Dispatch -------------------------------------------------------------


def complete_json(system: str, user: str, project_id: str | None = None) -> str | None:
    """Ask the active provider for the sheet-number JSON.

    Returns the raw response text, or None when the provider is unavailable or
    the call failed — the caller then falls back to pattern matching rather
    than leaving the page unclassified.
    """
    which = provider()
    try:
        if which == "gemini":
            return _complete_gemini(system, user, project_id)
        return _complete_claude(system, user, project_id)
    except Exception as exc:
        log.warning("%s sheet-number extraction failed: %s", which, exc)
        return None
