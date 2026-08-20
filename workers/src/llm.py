"""Provider-neutral model transport: Claude or Gemini, one switch per call site.

Every place this app asks a model for something — reading a sheet number,
writing a summary — routes through `complete()`. The provider is a TRANSPORT
detail and nothing more: both are sent the same instructions and the same
untrusted document text, and both are parsed by the same strict parser on the
way back. Swapping one for the other therefore changes WHO answers and nothing
about what the answer is allowed to be.

That invariant is why this module returns raw text rather than anything
structured. Validation belongs to the caller (`classify.parse_sheet_response`,
`summarize.parse_summary_json`), so a provider swap can never widen what the
rest of the pipeline will accept.

Each call site owns its own env switch, because they have genuinely different
economics — sheet reads are a cheap per-page classification, summaries are the
expensive reasoning step:

    SHEET_PROVIDER    (workers/src/sheetllm.py)  CLASSIFIER_MODEL / GEMINI_MODEL
    SUMMARY_PROVIDER  (workers/src/summarize.py) SUMMARY_MODEL / SUMMARY_GEMINI_MODEL
    CHAT_PROVIDER     (apps/api/src/llm.ts)      CHAT_MODEL / CHAT_GEMINI_MODEL

A provider with no API key, or whose SDK is missing, returns None here rather
than raising, so a missing key degrades to the caller's fallback (the rules
ladder, a skipped summary) instead of failing a job.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import logutil

log = logutil.get("llm")

PROVIDERS = ("claude", "gemini")


def resolve(env_var: str, default: str = "claude") -> str:
    """Active provider for one call site. Read per call, not cached at import,
    so a config change takes effect without a restart — and so tests can flip
    it. An unrecognised value falls back to the default rather than raising: a
    typo in an env var must not take a pipeline stage offline."""
    name = (os.environ.get(env_var) or default).strip().lower()
    return name if name in PROVIDERS else default


@dataclass
class Reply:
    text: str
    # Normalized across providers: "max_tokens" whenever the answer was cut off
    # by the output cap. Callers act on that (retry shorter, with more room)
    # rather than re-sending a request that will truncate identically.
    stop_reason: str | None = None


# --- Claude ---------------------------------------------------------------

_anthropic = None
_anthropic_unavailable = False


def anthropic_client():
    global _anthropic, _anthropic_unavailable
    if _anthropic is not None or _anthropic_unavailable:
        return _anthropic
    if not os.environ.get("ANTHROPIC_API_KEY"):
        log.warning("ANTHROPIC_API_KEY not set — Claude calls disabled")
        _anthropic_unavailable = True
        return None
    try:
        import anthropic

        _anthropic = anthropic.Anthropic(
            base_url=os.environ.get("ANTHROPIC_BASE_URL") or None,
            # A large project makes one call per page; the SDK retries 429s with
            # backoff automatically. More headroom than the default 2 so a
            # rate-limited burst doesn't fail the job.
            max_retries=int(os.environ.get("ANTHROPIC_MAX_RETRIES", "6")),
        )
    except Exception as exc:
        log.warning("anthropic SDK unavailable: %s", exc)
        _anthropic_unavailable = True
    return _anthropic


def _system_blocks(system: str | list[dict], cache: bool) -> list[dict]:
    """Normalize to Anthropic content blocks, adding the cache breakpoint.

    A caller that already built its own blocks (summaries put the role focus in
    a second block so the first keeps a byte-identical cache prefix) passes
    them through untouched.
    """
    if isinstance(system, list):
        return system
    block: dict = {"type": "text", "text": system}
    if cache:
        block["cache_control"] = {"type": "ephemeral"}
    return [block]


def _complete_claude(
    system, user, *, model, max_tokens, kind, project_id, cache_system
) -> Reply:
    client = anthropic_client()
    if client is None:
        return Reply(text="", stop_reason="unavailable")
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=_system_blocks(system, cache_system),
        messages=[{"role": "user", "content": user}],
    )
    import usage

    usage.record_message(project_id, kind, model, response.usage)
    text = "".join(b.text for b in response.content if b.type == "text")
    return Reply(text=text, stop_reason=getattr(response, "stop_reason", None))


# --- Gemini ---------------------------------------------------------------

_gemini = None
_gemini_unavailable = False


def gemini_client():
    global _gemini, _gemini_unavailable
    if _gemini is not None or _gemini_unavailable:
        return _gemini
    if not os.environ.get("GEMINI_API_KEY"):
        log.warning("GEMINI_API_KEY not set — Gemini calls disabled")
        _gemini_unavailable = True
        return None
    try:
        from google import genai

        _gemini = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    except Exception as exc:
        log.warning("google-genai SDK unavailable: %s", exc)
        _gemini_unavailable = True
    return _gemini


def _flatten_system(system: str | list[dict]) -> str:
    """Gemini takes one system_instruction string. Anthropic's per-block cache
    breakpoints have no equivalent, so the blocks are simply concatenated —
    the INSTRUCTIONS are identical either way, which is what the comparison
    between providers depends on. (Gemini caches implicitly on its own side.)
    """
    if isinstance(system, str):
        return system
    return "\n\n".join(str(b.get("text", "")) for b in system if b.get("text"))


def _complete_gemini(system, user, *, model, max_tokens, kind, project_id, json_only) -> Reply:
    client = gemini_client()
    if client is None:
        return Reply(text="", stop_reason="unavailable")
    from google.genai import types

    config = types.GenerateContentConfig(
        system_instruction=_flatten_system(system),
        max_output_tokens=max_tokens,
        temperature=0,
    )
    if json_only:
        # Ask for JSON directly — the callers' parsers are strict, and this
        # removes the "here is your JSON:" preamble that would fail them.
        config.response_mime_type = "application/json"

    response = client.models.generate_content(model=model, contents=user, config=config)

    import usage

    meta = getattr(response, "usage_metadata", None)
    usage.record(
        project_id,
        kind,
        model,
        input_tokens=getattr(meta, "prompt_token_count", 0) or 0,
        output_tokens=getattr(meta, "candidates_token_count", 0) or 0,
        cache_read_tokens=getattr(meta, "cached_content_token_count", 0) or 0,
    )
    return Reply(text=response.text or "", stop_reason=_gemini_stop_reason(response))


def _gemini_stop_reason(response) -> str | None:
    """Map Gemini's finish_reason onto the normalized vocabulary.

    Only truncation is normalized, because that is the one a caller acts on.
    Everything else keeps its own name — it ends up in a log line, not a
    branch.
    """
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return None
    reason = getattr(candidates[0], "finish_reason", None)
    if reason is None:
        return None
    name = getattr(reason, "name", None) or str(reason)
    return "max_tokens" if name.upper().endswith("MAX_TOKENS") else name.lower()


# --- Dispatch -------------------------------------------------------------


def complete(
    system: str | list[dict],
    user: str,
    *,
    provider: str,
    claude_model: str,
    gemini_model: str,
    max_tokens: int,
    kind: str,
    project_id: str | None = None,
    json_only: bool = False,
    cache_system: bool = True,
) -> Reply | None:
    """Ask the given provider for a completion.

    Returns None when the provider is unavailable (no key, missing SDK) or the
    call raised — the caller falls back rather than failing the job. A Reply
    with empty text is a provider that answered with nothing, which is a
    different thing and is left for the caller's parser to reject.
    """
    try:
        if provider == "gemini":
            reply = _complete_gemini(
                system,
                user,
                model=gemini_model,
                max_tokens=max_tokens,
                kind=kind,
                project_id=project_id,
                json_only=json_only,
            )
        else:
            reply = _complete_claude(
                system,
                user,
                model=claude_model,
                max_tokens=max_tokens,
                kind=kind,
                project_id=project_id,
                cache_system=cache_system,
            )
    except Exception as exc:
        log.warning("%s %s call failed: %s", provider, kind, exc)
        return None
    return None if reply.stop_reason == "unavailable" else reply


def model_for(provider: str, claude_model: str, gemini_model: str) -> str:
    return gemini_model if provider == "gemini" else claude_model


def available(provider: str) -> bool:
    """Whether the provider has a key configured. Checked before a run rather
    than per call, so an unconfigured provider skips the work instead of
    failing page by page."""
    key = "GEMINI_API_KEY" if provider == "gemini" else "ANTHROPIC_API_KEY"
    return bool(os.environ.get(key))
