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
import time
from dataclasses import dataclass

import logutil

log = logutil.get("llm")

PROVIDERS = ("claude", "gemini")

# --- Batch polling ---------------------------------------------------------

# Both vendors accept a batch and finish it "within 24h", usually in minutes.
# A worker job cannot sit on a thread for a day, so the wait is bounded and a
# timeout raises: the job then fails visibly and BullMQ retries, instead of the
# caller silently writing a summary with most of its pages missing.
BATCH_TIMEOUT_SECONDS = float(os.environ.get("BATCH_TIMEOUT_SECONDS", "3600"))
# Backs off from the first interval to the second: a small batch is often ready
# in seconds, and a big one should not be polled 1800 times.
BATCH_POLL_SECONDS = float(os.environ.get("BATCH_POLL_SECONDS", "5"))
BATCH_POLL_MAX_SECONDS = float(os.environ.get("BATCH_POLL_MAX_SECONDS", "60"))


class BatchTimeout(RuntimeError):
    """A batch did not finish inside BATCH_TIMEOUT_SECONDS."""


def _await_batch(label: str, refresh, finished) -> object:
    """Poll `refresh()` until `finished(job)`, with backoff and a timeout."""
    deadline = time.monotonic() + BATCH_TIMEOUT_SECONDS
    delay = BATCH_POLL_SECONDS
    job = refresh()
    while not finished(job):
        if time.monotonic() >= deadline:
            raise BatchTimeout(
                f"{label} did not finish within {BATCH_TIMEOUT_SECONDS:.0f}s"
            )
        time.sleep(min(delay, max(0.0, deadline - time.monotonic())))
        delay = min(delay * 1.5, BATCH_POLL_MAX_SECONDS)
        job = refresh()
    return job


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

# Thinking models spend output tokens on internal reasoning BEFORE writing the
# answer, and that reasoning counts against max_output_tokens — the SDK's own
# accounting is total = prompt + candidates + tool_use + thoughts. So a budget
# sized for the JSON buys thinking AND JSON, and the JSON is what gets cut off:
# on gemini-3.1-pro-preview every summary call at 2000 tokens came back
# truncated, page tier and rollup alike.
#
# Default 0 (off), because none of this app's calls are reasoning tasks. Reading
# a sheet number out of a title block and packing extracted facts into a fixed
# JSON shape are extraction, not deliberation — the thinking is pure cost and
# pure truncation risk. Set a positive budget to re-enable it, or -1 to let the
# model decide.
GEMINI_THINKING_BUDGET = int(os.environ.get("GEMINI_THINKING_BUDGET", "0"))

# Models that reject the setting outright (some tiers cannot turn thinking off).
# Latched per model after the first refusal so the failed call is paid once, not
# on every page of a 400-page project.
_no_thinking_config: set[str] = set()

# Substrings that mark "this model will not accept a thinking budget" rather
# than a transient failure. Matched case-insensitively against the error text.
_THINKING_REFUSALS = ("thinking", "thought")


def _thinking_config(model: str) -> dict | None:
    """Thinking settings for this model, or None to omit the field entirely."""
    if model in _no_thinking_config:
        return None
    return {"thinking_budget": GEMINI_THINKING_BUDGET}


def _is_thinking_refusal(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(word in text for word in _THINKING_REFUSALS)


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


def _gemini_config(
    *, model, max_tokens, system=None, json_only=False, thinking=True
) -> dict:
    """One request-config builder for the single and batched paths, so the two
    cannot drift into asking the same model for different things.

    A plain dict rather than types.GenerateContentConfig: the SDK coerces it at
    both call sites, and it keeps this module importable — and unit-testable —
    without google-genai installed, which is the same reason every other SDK
    import here is deferred into the function that needs it.
    """
    config: dict = {"max_output_tokens": max_tokens, "temperature": 0}
    if system is not None:
        config["system_instruction"] = _flatten_system(system)
    if json_only:
        # Ask for JSON directly — the callers' parsers are strict, and this
        # removes the "here is your JSON:" preamble that would fail them.
        config["response_mime_type"] = "application/json"
    if thinking:
        budget = _thinking_config(model)
        if budget is not None:
            config["thinking_config"] = budget
    return config


def _record_gemini_usage(meta, *, kind, model, project_id) -> None:
    """Thinking tokens are billed as output, so they are recorded as output.

    Counting only candidates_token_count under-reported every thinking model's
    spend — the reasoning is often the larger half of the bill.
    """
    import usage

    cached = getattr(meta, "cached_content_token_count", 0) or 0
    usage.record(
        project_id,
        kind,
        model,
        # prompt_token_count INCLUDES the cached tokens; the dashboard bills
        # cache reads separately, so subtract or a hit reads as dearer.
        input_tokens=max(0, (getattr(meta, "prompt_token_count", 0) or 0) - cached),
        output_tokens=(getattr(meta, "candidates_token_count", 0) or 0)
        + (getattr(meta, "thoughts_token_count", 0) or 0),
        cache_read_tokens=cached,
    )


def _complete_gemini(system, user, *, model, max_tokens, kind, project_id, json_only) -> Reply:
    client = gemini_client()
    if client is None:
        return Reply(text="", stop_reason="unavailable")

    def call(thinking: bool):
        return client.models.generate_content(
            model=model,
            contents=user,
            config=_gemini_config(
                model=model,
                max_tokens=max_tokens,
                system=system,
                json_only=json_only,
                thinking=thinking,
            ),
        )

    try:
        response = call(thinking=True)
    except Exception as exc:
        if not _is_thinking_refusal(exc) or model in _no_thinking_config:
            raise
        # This model will not take a thinking budget. Latch it so the rest of
        # the project's pages skip straight to the working shape.
        log.warning(
            "%s rejected the thinking budget (%s) — retrying without it, and "
            "omitting it for this model from now on",
            model,
            exc,
        )
        _no_thinking_config.add(model)
        response = call(thinking=False)

    _record_gemini_usage(
        getattr(response, "usage_metadata", None),
        kind=kind,
        model=model,
        project_id=project_id,
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


# --- Batch transports -----------------------------------------------------

# Both vendors bill batched calls at 50% and finish them asynchronously, so
# these are worth the extra machinery for bulk page summaries. The two APIs
# differ in shape but not in contract: give them {custom_id: prompt}, get back
# {custom_id: raw text}, with failed entries simply absent — the caller already
# treats a missing answer as a page it could not summarize.


def _batch_claude(
    prompts: dict[str, str], *, system, model, max_tokens, kind, project_id, cache_system
) -> dict[str, str]:
    client = anthropic_client()
    if client is None:
        return {}
    batch = client.messages.batches.create(
        requests=[
            {
                "custom_id": custom_id,
                "params": {
                    "model": model,
                    "max_tokens": max_tokens,
                    "system": _system_blocks(system, cache_system),
                    "messages": [{"role": "user", "content": prompt}],
                },
            }
            for custom_id, prompt in prompts.items()
        ]
    )
    log.info("anthropic batch %s submitted (%d requests)", batch.id, len(prompts))
    _await_batch(
        f"anthropic batch {batch.id}",
        lambda: client.messages.batches.retrieve(batch.id),
        lambda job: job.processing_status == "ended",
    )

    import usage

    results: dict[str, str] = {}
    for entry in client.messages.batches.results(batch.id):
        if entry.result.type != "succeeded":
            log.warning(
                "batch entry %s failed (%s)", entry.custom_id, entry.result.type
            )
            continue
        message = entry.result.message
        usage.record_message(project_id, kind, model, message.usage)
        results[entry.custom_id] = "".join(
            b.text for b in message.content if b.type == "text"
        )
    return results


# Terminal job states. PARTIALLY_SUCCEEDED is terminal AND has results worth
# reading — the entries that did succeed are still summaries the user paid for.
_GEMINI_DONE = {
    "JOB_STATE_SUCCEEDED",
    "JOB_STATE_PARTIALLY_SUCCEEDED",
    "JOB_STATE_FAILED",
    "JOB_STATE_CANCELLED",
    "JOB_STATE_EXPIRED",
}


def _gemini_state(job) -> str:
    state = getattr(job, "state", None)
    return (getattr(state, "name", None) or str(state or "")).upper()


def _batch_gemini(
    prompts: dict[str, str], *, system, model, max_tokens, kind, project_id, json_only
) -> dict[str, str]:
    client = gemini_client()
    if client is None:
        return {}

    # Same builder as the single-call path: a batched page and a retried page
    # must be asked for exactly the same thing, or the retry silently changes
    # the answer's shape.
    request_config = _gemini_config(
        model=model,
        max_tokens=max_tokens,
        system=system,
        json_only=json_only,
    )

    order = list(prompts)
    job = client.batches.create(
        model=model,
        # The Gemini Developer API takes the requests inline (no upload step) —
        # `metadata` is this API's equivalent of Anthropic's custom_id.
        src=[
            {
                "contents": [{"role": "user", "parts": [{"text": prompts[custom_id]}]}],
                "config": request_config,
                "metadata": {"custom_id": custom_id},
            }
            for custom_id in order
        ],
        config={"display_name": f"cdip-{kind}"},
    )
    log.info("gemini batch %s submitted (%d requests)", job.name, len(prompts))
    job = _await_batch(
        f"gemini batch {job.name}",
        lambda: client.batches.get(name=job.name),
        lambda current: _gemini_state(current) in _GEMINI_DONE,
    )

    state = _gemini_state(job)
    responses = getattr(getattr(job, "dest", None), "inlined_responses", None) or []
    if state != "JOB_STATE_SUCCEEDED":
        log.warning(
            "gemini batch %s ended %s — using the %d entries that returned",
            getattr(job, "name", "?"),
            state,
            len(responses),
        )

    results: dict[str, str] = {}
    for index, entry in enumerate(responses):
        if getattr(entry, "error", None) is not None:
            log.warning("batch entry %d failed (%s)", index, entry.error)
            continue
        response = getattr(entry, "response", None)
        if response is None:
            continue
        # Prefer the echoed id; fall back to position, which the API documents
        # as matching the request order. Positional matching is only safe
        # because a failed entry is still an entry, so indexes do not shift.
        custom_id = (getattr(entry, "metadata", None) or {}).get("custom_id")
        if custom_id is None:
            if index >= len(order):
                continue
            custom_id = order[index]
            log.debug("batch entry %d carried no custom_id — matched by position", index)

        _record_gemini_usage(
            getattr(response, "usage_metadata", None),
            kind=kind,
            model=model,
            project_id=project_id,
        )
        results[custom_id] = response.text or ""
    return results


def complete_batch(
    prompts: dict[str, str],
    *,
    system: str | list[dict],
    provider: str,
    claude_model: str,
    gemini_model: str,
    max_tokens: int,
    kind: str,
    project_id: str | None = None,
    json_only: bool = False,
    cache_system: bool = True,
) -> dict[str, str]:
    """Run many prompts as one batch. {custom_id: prompt} -> {custom_id: text}.

    Entries the provider could not complete are absent from the result rather
    than raising, so one bad page never costs the whole run. A batch that never
    finishes raises BatchTimeout — the caller must not quietly fall back to
    sequential calls there, or a slow batch would be paid for twice.

    Known limitation: the batch id is not persisted, so a BatchTimeout followed
    by a BullMQ retry SUBMITS A SECOND BATCH and pays for the work twice. That
    is why the timeout defaults to an hour rather than something tight —
    batches usually land in minutes, and the expensive case is timing out on
    one that was about to finish. Resuming a batch across retries needs the id
    stored on the portion row; until then, raise BATCH_TIMEOUT_SECONDS rather
    than lower it.
    """
    if provider == "gemini":
        return _batch_gemini(
            prompts,
            system=system,
            model=gemini_model,
            max_tokens=max_tokens,
            kind=kind,
            project_id=project_id,
            json_only=json_only,
        )
    return _batch_claude(
        prompts,
        system=system,
        model=claude_model,
        max_tokens=max_tokens,
        kind=kind,
        project_id=project_id,
        cache_system=cache_system,
    )


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
