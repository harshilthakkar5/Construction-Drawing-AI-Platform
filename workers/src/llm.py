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

Embeddings have their own transport (`embedllm.py`, EMBEDDING_PROVIDER) because
their providers are a different set — but they share this module's Gemini
client and its batch poller (`await_batch`, `gemini_state`,
GEMINI_TERMINAL_STATES), so a batch waits the same bounded way whatever it
holds.
"""

from __future__ import annotations

import base64
import os
import re
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


def await_batch(label: str, refresh, finished) -> object:
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
    # What this ONE call cost, as the provider reported it. usage_events holds
    # the same numbers per project and kind; these exist so a caller can
    # attribute spend to the unit of work it is doing (one RFI scan) rather
    # than to a project's whole history. `output_tokens` INCLUDES the
    # reasoning, because that is how both vendors bill it; `thinking_tokens`
    # is the part of it that was reasoning, or None where the provider does
    # not say (Anthropic reports one output figure).
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int | None = None
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    # The thinking setting that was actually SENT, after any refusal ladder —
    # "thinking_level=low" where "minimal" was asked for and refused. What was
    # configured and what ran are different facts, and only this one explains
    # a bill.
    thinking: str | None = None
    # True when a STAGE setting was asked for and the model would not take
    # it, so something else ran. Comparing the two strings cannot tell: a
    # 2048-token budget IS what "low" means on Haiku, and says nothing like it.
    thinking_adjusted: bool = False


# --- Per-stage thinking ------------------------------------------------------

# One vocabulary for both vendors, so a stage's switch reads the same whichever
# provider it points at. `off` is the floor the model allows, not a promise of
# zero: Gemini 3 has no "none" level (minimal is the bottom rung) and Claude
# Opus 5.5 refuses to disable thinking at all.
THINKING_SETTINGS = ("off", "minimal", "low", "medium", "high")


def stage_thinking(env_var: str) -> str | None:
    """A stage's own thinking setting (e.g. RFI_THINKING), or None to leave the
    transport's global defaults (CLAUDE_THINKING / GEMINI_THINKING_LEVEL) in
    charge. Read per call, like `resolve`, so tests and a config change need
    no restart. A typo is refused to None with a warning rather than guessed
    at: an unrecognised value must not quietly buy the top of the scale."""
    raw = (os.environ.get(env_var) or "").strip().lower()
    if not raw:
        return None
    if raw in ("none", "false", "0", "disabled"):
        return "off"
    if raw not in THINKING_SETTINGS:
        log.warning(
            "%s=%r is not one of %s — using the global thinking defaults",
            env_var, raw, ", ".join(THINKING_SETTINGS),
        )
        return None
    return raw


def describe_thinking(thinking: dict | None, output_config: dict | None = None) -> str:
    """A thinking config as one short, loggable, storable string."""
    if thinking is None:
        return "omitted"
    if "thinking_level" in thinking:
        return f"thinking_level={thinking['thinking_level']}"
    if "thinking_budget" in thinking:
        return f"thinking_budget={thinking['thinking_budget']}"
    kind = thinking.get("type", "?")
    if kind == "enabled":
        return f"budget_tokens={thinking.get('budget_tokens')}"
    if kind == "adaptive" and output_config and output_config.get("effort"):
        return f"adaptive, effort={output_config['effort']}"
    return str(kind)


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


# Claude thinking, off by default — the same decision GEMINI_THINKING_BUDGET
# already encodes for the other provider, which this side simply never made.
#
# It matters because the default MOVED. Sonnet 5 runs ADAPTIVE thinking when
# `thinking` is omitted, where earlier models ran none, so code that never sent
# the field started reasoning the day the model id changed. Thinking bills from
# max_tokens and `display` defaults to "omitted", so the reply comes back as
# thinking blocks carrying no text: the vision pass posted an ARCH E1 drawing
# with VLM_MAX_TOKENS=4000, got 200 OK, and `_complete_claude` joined zero text
# blocks into "". The log said "description was 0 chars", which reads like a
# refusal or a broken image, and the project stored no descriptions at all.
#
# None of this app's Claude calls are reasoning tasks — reading a sheet number
# out of a title block, packing facts into fixed JSON, describing what a
# drawing shows — so the thinking is pure cost and pure truncation risk, word
# for word the argument already written for Gemini above.
_CLAUDE_THINKING = (os.environ.get("CLAUDE_THINKING") or "off").strip().lower()

# Models that reject the `thinking` field itself rather than its value: older
# tiers (Haiku 4.5 and back) take budget_tokens and may not accept "disabled".
# Latched after one refusal, exactly like _no_thinking_config on the Gemini
# side, so a 400-page project pays for the discovery once.
_no_thinking_param: set[str] = set()


def _claude_thinking(model: str) -> dict | None:
    """The `thinking` field for this model, or None to omit it entirely."""
    if model in _no_thinking_param or _CLAUDE_THINKING not in ("off", "false", "0"):
        return None
    return {"type": "disabled"}


_CLAUDE_VERSION = re.compile(r"^claude-[a-z]+-(\d+)(?:-(\d+))?")

# Budgets for the models that still take one. 1024 is the API's minimum.
_CLAUDE_BUDGETS = {"minimal": 1024, "low": 2048, "medium": 4096, "high": 8192}


def _claude_takes_effort(model: str) -> bool:
    """Whether this model is steered by adaptive thinking + effort rather than
    `budget_tokens`. From 4.6 on it is effort — and from 4.7 on a budget is a
    400 — while Haiku 4.5 and older still take a budget. A version sniff, for
    the reason `_takes_thinking_level` gives: a list of names expires. A name
    in the old `claude-3-5-sonnet` shape is budget; one that parses as nothing
    is assumed current."""
    if re.match(r"^claude-\d", model or ""):
        return False
    found = _CLAUDE_VERSION.match(model or "")
    if not found:
        return True
    major = int(found.group(1))
    minor = int(found.group(2)) if found.group(2) and len(found.group(2)) <= 2 else 0
    return major > 4 or (major == 4 and minor >= 6)


def _claude_stage_thinking(model: str, setting: str) -> tuple[dict, dict | None, int]:
    """(thinking, output_config, extra max_tokens) for an explicit stage setting.

    A budget is spent from max_tokens before the answer is written, so the
    caller's max_tokens — sized for the JSON — grows by the budget, or turning
    thinking on would truncate the very reply it was meant to improve.
    """
    if setting == "off":
        return {"type": "disabled"}, None, 0
    if _claude_takes_effort(model):
        return {"type": "adaptive"}, {"effort": "low" if setting == "minimal" else setting}, 0
    budget = _CLAUDE_BUDGETS[setting]
    return {"type": "enabled", "budget_tokens": budget}, None, budget


def _describe_blocks(content) -> str:
    """What the reply was made of. Reached only when it held no text, and that
    is the whole point: a response of one thinking block and a response the
    model genuinely left empty are the same empty string to every caller."""
    kinds: dict[str, int] = {}
    for block in content or []:
        kind = getattr(block, "type", "?")
        kinds[kind] = kinds.get(kind, 0) + 1
    return ", ".join(f"{n}x {kind}" for kind, n in kinds.items()) or "no blocks"


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


def _user_content_claude(user: str, images: list[bytes] | None) -> str | list[dict]:
    """A plain string when there are no images, so every existing caller's
    request is byte-identical to what it sent before — a prompt cache is a
    prefix match, and reshaping the user turn for callers that never pass an
    image would cost every one of them their cached prefix.

    Images go BEFORE the text. Anthropic's guidance for a single image is to
    put it first and ask the question after it, and the describe pass reads
    better that way too: the instruction lands with the drawing already in
    view rather than in front of an empty frame.
    """
    if not images:
        return user
    blocks: list[dict] = [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": base64.b64encode(png).decode("ascii"),
            },
        }
        for png in images
    ]
    blocks.append({"type": "text", "text": user})
    return blocks


def _complete_claude(
    system, user, *, model, max_tokens, kind, project_id, cache_system, images=None,
    thinking_setting: str | None = None,
) -> Reply:
    client = anthropic_client()
    if client is None:
        return Reply(text="", stop_reason="unavailable")

    def call(thinking: dict | None, output_config: dict | None, extra: int):
        request = dict(
            model=model,
            max_tokens=max_tokens + extra,
            system=_system_blocks(system, cache_system),
            messages=[{"role": "user", "content": _user_content_claude(user, images)}],
        )
        if thinking is not None:
            request["thinking"] = thinking
        if output_config is not None:
            request["output_config"] = output_config
        return client.messages.create(**request)

    if thinking_setting is None:
        thinking, output_config, extra = _claude_thinking(model), None, 0
    else:
        thinking, output_config, extra = _claude_stage_thinking(model, thinking_setting)
    adjusted = False
    try:
        response = call(thinking, output_config, extra)
    except Exception as exc:
        # Only the field itself can be at fault here: its value is a constant.
        # Drop it, and latch the model so the rest of the project skips straight
        # to the shape that works — mirroring the Gemini path, which learns the
        # opposite lesson the same way.
        if thinking is None or not _is_thinking_refusal(exc):
            raise
        if thinking_setting is None:
            log.warning("%s rejected thinking=%s (%s) — omitting it from now on", model, thinking, exc)
            _no_thinking_param.add(model)
            thinking, output_config = None, None
        else:
            # An explicit stage setting is not latched: it is one stage's
            # choice, and another stage calling this model may ask for
            # something the model accepts. The one refusal worth expecting is
            # Opus 5.5 declining "disabled", where the nearest thing it allows
            # is its default thinking at the lowest effort.
            output_config = (
                {"effort": "low"}
                if thinking_setting in ("off", "minimal") and _claude_takes_effort(model)
                else output_config
            )
            log.warning(
                "%s rejected thinking=%s (%s) — retrying with it omitted%s",
                model, thinking, exc,
                f" and effort={output_config['effort']}" if output_config else "",
            )
            thinking = None
            adjusted = True
        response = call(thinking, output_config, 0)

    import usage

    usage.record_message(project_id, kind, model, response.usage)
    text = "".join(b.text for b in response.content if b.type == "text")
    if not text:
        # Without this the caller sees "" and guesses. A reply made of thinking
        # blocks and a reply the model left empty are the same empty string.
        log.warning(
            "%s returned no text (stop_reason=%s, thinking=%s): the reply was %s",
            model,
            getattr(response, "stop_reason", None),
            thinking,
            _describe_blocks(response.content),
        )
    meta = response.usage
    return Reply(
        text=text,
        stop_reason=getattr(response, "stop_reason", None),
        model=model,
        input_tokens=getattr(meta, "input_tokens", 0) or 0,
        output_tokens=getattr(meta, "output_tokens", 0) or 0,
        cache_read_tokens=getattr(meta, "cache_read_input_tokens", 0) or 0,
        cache_write_tokens=getattr(meta, "cache_creation_input_tokens", 0) or 0,
        thinking=describe_thinking(thinking, output_config),
        thinking_adjusted=adjusted,
    )


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
def _parse_thinking_budget(raw: str | None) -> int | None:
    """None means OMIT the field, not "budget of zero" — and the two are
    different requests. It applies to models that take a BUDGET; from Gemini 3
    on, the control is a LEVEL (see GEMINI_THINKING_LEVEL) and omitting the
    field is the opposite of off."""
    value = (raw or "0").strip().lower()
    if value in ("", "off", "none", "omit"):
        return None
    try:
        return int(value)
    except ValueError:
        log.warning(
            "GEMINI_THINKING_BUDGET=%r is not a number or 'off' — disabling thinking", raw
        )
        return 0


GEMINI_THINKING_BUDGET = _parse_thinking_budget(os.environ.get("GEMINI_THINKING_BUDGET"))

# From Gemini 3 on, `thinking_budget` is not the control any more: the field is
# `thinking_level`, and sending BOTH is an error. What makes this expensive
# rather than obvious is the default. An unspecified thinking_level is the TOP
# of the scale, so the old escape hatch — drop the field the model rejected and
# carry on — did not disable thinking, it asked for the most of it. That is how
# VLM_PROVIDER=gemini on gemini-3.6-flash spent 3900 of a 4000-token budget
# reasoning and wrote 103 tokens of description: one sheet's geometry, from a
# transport that believed it had turned thinking off, reported by a warning
# whose own advice (GEMINI_THINKING_BUDGET=off) made it worse.
#
# It is the Sonnet 5 lesson from the other vendor, and the same shape: a
# transport that does not send the CURRENT field gets the model's default, and
# the default moved.
_GEMINI_VERSION = re.compile(r"gemini-(\d+)")

# Floor first. `minimal` is as close to off as Gemini 3 offers — the docs are
# explicit that it does not guarantee zero thinking — and some models do not
# accept it at all, so a refusal steps UP the ladder rather than off the end.
_THINKING_LEVELS = ("minimal", "low", "medium", "high")

GEMINI_THINKING_LEVEL = (os.environ.get("GEMINI_THINKING_LEVEL") or "minimal").strip().lower()
if GEMINI_THINKING_LEVEL not in _THINKING_LEVELS:
    log.warning(
        "GEMINI_THINKING_LEVEL=%r is not one of %s — using %r",
        GEMINI_THINKING_LEVEL, ", ".join(_THINKING_LEVELS), _THINKING_LEVELS[0],
    )
    GEMINI_THINKING_LEVEL = _THINKING_LEVELS[0]


def _takes_thinking_level(model: str) -> bool:
    """Whether this model is asked for a level rather than a budget.

    A version sniff, because the alternative is a list of model names and this
    repo has already paid for one of those: a name expires on a schedule
    nothing here controls. Gemini 4 will match it without a code change; a
    name that parses as neither keeps the older field, which is where every
    pre-3 model lives.
    """
    return _gemini_major(model) >= 3


def _gemini_major(model: str) -> int:
    """This model's generation number, or 0 for a name that does not parse.

    0 rather than None because every caller asks the same question — "is this
    at least generation N?" — and a name nothing recognises should answer no
    to all of them.
    """
    found = _GEMINI_VERSION.search(model or "")
    return int(found.group(1)) if found else 0


# Models that rejected a thinking setting. The value is what to send INSTEAD —
# None meaning "omit the field" — latched after one refusal so the failed call
# is paid once, not on every page of a 400-page project.
_thinking_latched: dict[str, dict | None] = {}

# Kept as the omit-latch under its old name: several call sites and tests read
# it, and "this model is called with no thinking field" is still exactly what
# membership means.
_no_thinking_config: set[str] = set()

# Substrings that mark "this model will not accept a thinking budget" rather
# than a transient failure. Matched case-insensitively against the error text.
_THINKING_REFUSALS = ("thinking", "thought")


# An explicit stage setting (RFI_THINKING) as Gemini fields. `off` on a level
# model is `minimal`, the bottom rung — Gemini 3 has no zero. A budget model
# takes a number, and 0 is genuinely off where the model allows it.
_GEMINI_STAGE_BUDGETS = {"off": 0, "minimal": 512, "low": 1024, "medium": 4096, "high": 8192}
# Output headroom an explicit setting adds, because Gemini spends its reasoning
# from max_output_tokens BEFORE the answer: a caller sizes max_tokens for its
# JSON, and asking for more thinking inside the same cap truncates the JSON.
_GEMINI_STAGE_HEADROOM = {"off": 0, "minimal": 0, "low": 1024, "medium": 4096, "high": 8192}

# What a model accepted for a given STAGE setting, keyed (model, setting). Kept
# apart from _thinking_latched on purpose: one stage asking for `high` and
# being stepped somewhere must not change what every other stage sends.
_stage_thinking_latched: dict[tuple[str, str], dict | None] = {}


def _stage_thinking_intended(model: str, setting: str) -> dict:
    """What a stage setting means on this model, before any refusal."""
    if _takes_thinking_level(model):
        return {"thinking_level": "minimal" if setting == "off" else setting}
    return {"thinking_budget": _GEMINI_STAGE_BUDGETS[setting]}


def _thinking_config(model: str, setting: str | None = None) -> dict | None:
    """Thinking settings for this model, or None to omit the field entirely.

    `setting` is a stage's own choice (see `stage_thinking`); None means the
    global GEMINI_THINKING_LEVEL / GEMINI_THINKING_BUDGET decide."""
    if setting is not None:
        if (model, setting) in _stage_thinking_latched:
            return _stage_thinking_latched[(model, setting)]
        return _stage_thinking_intended(model, setting)
    if model in _no_thinking_config:
        return None
    if model in _thinking_latched:
        return _thinking_latched[model]
    if _takes_thinking_level(model):
        return {"thinking_level": GEMINI_THINKING_LEVEL}
    if GEMINI_THINKING_BUDGET is None:
        return None
    return {"thinking_budget": GEMINI_THINKING_BUDGET}


def _thinking_ladder(model: str, setting: str | None = None) -> list[dict | None]:
    """What to try, in order, when a model refuses the thinking setting.

    Omission is LAST and, on a level-taking model, is a defeat rather than a
    fallback — it means thinking at the model's default, which is the top of
    the scale. So a refused level steps up one rung first: a model that will
    not take `minimal` may well take `low`, and `low` still leaves most of the
    budget for the answer.
    """
    first = _thinking_config(model, setting)
    if first is None:
        return [None]
    if "thinking_level" not in first:
        return [first, None]
    rungs = list(_THINKING_LEVELS)
    index = rungs.index(first["thinking_level"])
    nxt = rungs[index + 1] if index + 1 < len(rungs) else None
    return [first, {"thinking_level": nxt}, None] if nxt else [first, None]


def _latch_thinking(model: str, thinking: dict | None, setting: str | None = None) -> None:
    """Remember what this model actually accepted, and say what it costs."""
    if setting is not None:
        _stage_thinking_latched[(model, setting)] = thinking
        log.warning(
            "%s refused the stage's thinking setting %r — it will be sent %s for it from now on",
            model, setting, describe_thinking(thinking),
        )
        return
    if thinking is not None:
        _thinking_latched[model] = thinking
        log.warning("%s will be called with thinking=%s from now on", model, thinking)
        return
    _no_thinking_config.add(model)
    if not _takes_thinking_level(model):
        log.warning("%s will be called without a thinking budget from now on", model)
        return
    log.error(
        "%s refused every thinking setting this transport knows, so the field is now "
        "OMITTED for it — and on a Gemini 3 model that is not 'off', it is the MAXIMUM: "
        "an unspecified thinking_level defaults to the top of the scale. That reasoning "
        "is billed from the SAME max_output_tokens as the answer, so expect short "
        "descriptions and truncated JSON for the rest of this run. This is "
        "configuration, not weather: set GEMINI_THINKING_LEVEL to a level this model "
        "accepts, or point the stage at a model that takes one.",
        model,
    )


def _is_thinking_refusal(exc: object) -> bool:
    """Whether this error is worth one retry without the thinking budget.

    Some models say so plainly ("thinking_config is not supported"). The
    Gemini 3 family does not: it replaced thinking_budget with a different
    field and rejects the old one as a bare 400 INVALID_ARGUMENT — "Request
    contains an invalid argument", naming nothing. Matching only the polite
    wording left SHEET_PROVIDER=gemini failing every call on those models and
    falling silently down to the rules ladder, which looks like a working
    scrape with worse results.

    So an invalid-argument error also earns the retry. It is safe to be
    generous here because the retry only DROPS an optional field, is bounded
    to one attempt, and — unlike before — only latches the model when it
    actually succeeds. An unrelated 400 therefore costs one extra call and
    still surfaces its original error.
    """
    text = str(exc).lower()
    if any(word in text for word in _THINKING_REFUSALS):
        return True
    return "invalid_argument" in text or "invalid argument" in text


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
    *, model, max_tokens, system=None, json_only=False, thinking: bool | dict = True
) -> dict:
    """One request-config builder for the single and batched paths, so the two
    cannot drift into asking the same model for different things.

    A plain dict rather than types.GenerateContentConfig: the SDK coerces it at
    both call sites, and it keeps this module importable — and unit-testable —
    without google-genai installed, which is the same reason every other SDK
    import here is deferred into the function that needs it.
    """
    config: dict = {
        "max_output_tokens": max_tokens,
        "temperature": 0,
        # Automatic Function Calling OFF. This app declares no tools to any
        # Gemini call, so AFC has nothing it could ever do — but the SDK still
        # routes every generate_content through its agentic wrapper and logs
        #
        #   INFO  AFC is enabled with max remote calls: 10.
        #   WARN  Direct use of automatic function calling (AFC) in
        #         Models.generate_content is not recommended...
        #
        # on the way. Two lines of vendor noise per call, at WARNING, in the
        # log where this pipeline's own warnings have to be spotted: in the run
        # that found the vision pass returning 98 characters, that AFC warning
        # sat directly above the line that mattered. Disabling it also stops the
        # SDK entering a retry loop built for a feature nothing here uses.
        "automatic_function_calling": {"disable": True},
    }
    if system is not None:
        config["system_instruction"] = _flatten_system(system)
    if json_only:
        # Ask for JSON directly — the callers' parsers are strict, and this
        # removes the "here is your JSON:" preamble that would fail them.
        config["response_mime_type"] = "application/json"
    if thinking:
        # True asks for whatever this model should get; a dict is a specific
        # rung the caller is retrying at. False omits the field — which is off
        # on a budget model and the MAXIMUM on a level one, so nothing chooses
        # it lightly.
        chosen = thinking if isinstance(thinking, dict) else _thinking_config(model)
        if chosen is not None:
            config["thinking_config"] = chosen
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


# How many pixels of an image the model actually reads.
#
# This is the third time the same lesson has been paid for here, and the most
# expensive, because nothing in the transport or the logs contradicted the
# belief. `vlm.render` prints the DPI it rendered at, and a run that set
# VLM_MAX_EDGE=5000 to test whether the column tag is resolution-bound logged
# "119 DPI" and scored the tag at 14% — worse than the 72 DPI run it was meant
# to beat. The image was never read at 119 DPI:
#
#   * Gemini scales an image down to fit 3072x3072 before anything else. A
#     42x30in sheet is 3024pt, so that cap IS 73 DPI on this drawing, whatever
#     is sent. (Claude's ceiling, 2576px, is 61 DPI on the same sheet — the two
#     providers differ by 12 DPI, not by "one of them tiles without a wall",
#     which is what the comment in vlm.py used to say.)
#   * From Gemini 3 on, the image is then tokenized to a FIXED BUDGET set by
#     `media_resolution`, defaulting to HIGH — 1120 tokens for an image. More
#     pixels do not buy more of those tokens; they are resampled into the same
#     budget.
#
# So VLM_MAX_EDGE above 3072 costs render time and nothing else, and the
# resolution experiment this repo has run twice has never actually varied the
# resolution the model reads at. ULTRA_HIGH is the one control that does, and
# it exists only PER PART: `GenerateContentConfig.media_resolution` stops at
# HIGH, which is why this is attached to the image part rather than the config.
#
# The default is the top of the scale because this transport's only image
# caller is the vision pass, whose entire job is resolving fine text on a
# large sheet — a member size with a fraction on the end, which is the one
# thing measured as unreadable. Lower it if the token cost matters more than
# the reading does.
_MEDIA_LEVELS = ("low", "medium", "high", "ultra_high")

GEMINI_MEDIA_RESOLUTION = (
    os.environ.get("GEMINI_MEDIA_RESOLUTION") or "ultra_high"
).strip().lower()
if GEMINI_MEDIA_RESOLUTION not in _MEDIA_LEVELS:
    log.warning(
        "GEMINI_MEDIA_RESOLUTION=%r is not one of %s — using %r",
        GEMINI_MEDIA_RESOLUTION, ", ".join(_MEDIA_LEVELS), _MEDIA_LEVELS[-1],
    )
    GEMINI_MEDIA_RESOLUTION = _MEDIA_LEVELS[-1]

# Models that rejected the field, latched after one refusal so a 400 is paid
# once per model rather than once per page.
_no_media_resolution: set[str] = set()

_MEDIA_REFUSALS = ("media_resolution", "media resolution")


def _takes_media_resolution(model: str) -> bool:
    """Whether this model is asked for a per-part media resolution.

    The same version sniff as the thinking level, for the same reason: a list
    of model names expires on a schedule this repo does not control. ULTRA_HIGH
    arrived with Gemini 3; a pre-3 model tokenizes an image its own way and is
    sent no field at all.
    """
    return _takes_thinking_level(model) and model not in _no_media_resolution


def _media_resolution_part(model: str) -> dict | None:
    """The `media_resolution` to attach to an image part, or None to omit it."""
    if not _takes_media_resolution(model):
        return None
    return {"level": f"MEDIA_RESOLUTION_{GEMINI_MEDIA_RESOLUTION.upper()}"}


def _is_media_refusal(exc: object) -> bool:
    """Whether this error is worth one retry without the media resolution.

    Deliberately narrow. The thinking-refusal matcher had to be widened to a
    bare INVALID_ARGUMENT because Gemini 3 rejects the OLD thinking field
    without naming it, and that width is affordable there — the fallback is
    another thinking setting. Here the fallback is reading the sheet at the
    provider's default, so a match on an unrelated 400 would silently undo the
    only resolution lever this pass has. It must name the field.
    """
    return any(mark in str(exc).lower() for mark in _MEDIA_REFUSALS)


def _latch_no_media(model: str, exc: object) -> None:
    _no_media_resolution.add(model)
    log.warning(
        "%s rejected media_resolution=%s (%s) — images are sent to it without the field "
        "from now on, which means the provider's default budget for them. On a 42x30in "
        "sheet that is roughly 73 DPI, where a footing mark reads and a member size with "
        "a fraction does not.",
        model, GEMINI_MEDIA_RESOLUTION, exc,
    )


def _user_content_gemini(user: str, images: list[bytes] | None, *, model: str = ""):
    """Gemini takes `contents` as a string or a list of parts. Same rule as the
    Claude side: no images means the exact string the caller passed, so the
    request this module has always sent is unchanged.

    An inline_data dict rather than a `types.Part` object at module scope —
    the SDK coerces it, and it keeps llm.py importable without google-genai,
    which is why every other SDK import here is deferred too.

    `media_resolution` rides on the PART, not on the config, because that is
    the only place ULTRA_HIGH exists. See the block above it for what the field
    costs and what omitting it costs.
    """
    if not images:
        return user
    media = _media_resolution_part(model)
    parts = []
    for png in images:
        part = {"inline_data": {"mime_type": "image/png", "data": png}}
        if media:
            part["media_resolution"] = media
        parts.append(part)
    return parts + [user]


def _complete_gemini(
    system, user, *, model, max_tokens, kind, project_id, json_only, images=None,
    thinking_setting: str | None = None,
) -> Reply:
    client = gemini_client()
    if client is None:
        return Reply(text="", stop_reason="unavailable")
    if thinking_setting is not None:
        max_tokens += _GEMINI_STAGE_HEADROOM[thinking_setting]

    def send(thinking: bool | dict, *, media_model: str):
        return client.models.generate_content(
            model=model,
            contents=_user_content_gemini(user, images, model=media_model),
            config=_gemini_config(
                model=model,
                max_tokens=max_tokens,
                system=system,
                json_only=json_only,
                thinking=thinking,
            ),
        )

    def call(thinking: bool | dict):
        # The media-resolution retry is nested INSIDE one rung of the thinking
        # ladder rather than being a second ladder beside it: the two settings
        # are independent, and a model that refuses the image field should not
        # also lose the thinking level that was working.
        try:
            return send(thinking, media_model=model)
        except Exception as exc:
            if not images or model in _no_media_resolution or not _is_media_refusal(exc):
                raise
            _latch_no_media(model, exc)
            return send(thinking, media_model="")

    # Walk the ladder rather than falling straight off it. Dropping the field
    # used to be the whole fallback, and on a level-taking model that asks for
    # MORE thinking than the setting it replaced — the failure this transport
    # is supposed to prevent, reached by the code that prevents it.
    ladder = _thinking_ladder(model, thinking_setting)
    first_error: Exception | None = None
    response = None
    sent: dict | None = None
    for step, thinking in enumerate(ladder):
        try:
            response = call(thinking if thinking is not None else False)
        except Exception as exc:
            # Report the ORIGINAL failure if this turns out not to be about
            # thinking at all — the later ones are symptoms of the same cause —
            # and do NOT latch, or one unrelated 400 would change how this
            # model is called for the rest of the process's life.
            first_error = first_error or exc
            if step == len(ladder) - 1 or not _is_thinking_refusal(exc):
                raise first_error from None
            log.warning(
                "%s rejected thinking=%s (%s) — retrying with %s",
                model, thinking, exc, ladder[step + 1] or "the field omitted",
            )
            continue
        # It worked: latch it so the rest of the project's pages skip straight
        # to the shape that works.
        if step:
            _latch_thinking(model, thinking, thinking_setting)
        sent = thinking
        break

    meta = getattr(response, "usage_metadata", None)
    _record_gemini_usage(meta, kind=kind, model=model, project_id=project_id)
    cached = getattr(meta, "cached_content_token_count", 0) or 0
    thoughts = getattr(meta, "thoughts_token_count", 0) or 0
    return Reply(
        text=response.text or "",
        stop_reason=_gemini_stop_reason(response),
        model=model,
        # The same arithmetic _record_gemini_usage writes, so a scan's total
        # and the dashboard's agree to the token.
        input_tokens=max(0, (getattr(meta, "prompt_token_count", 0) or 0) - cached),
        output_tokens=(getattr(meta, "candidates_token_count", 0) or 0) + thoughts,
        thinking_tokens=thoughts,
        cache_read_tokens=cached,
        thinking=describe_thinking(sent),
        # Against the setting's own mapping, not the ladder's first rung: a
        # latched step skips the refused rung entirely on later calls, and it
        # is still not what was asked for.
        thinking_adjusted=(
            thinking_setting is not None and sent != _stage_thinking_intended(model, thinking_setting)
        ),
    )


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
    await_batch(
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
GEMINI_TERMINAL_STATES = {
    "JOB_STATE_SUCCEEDED",
    "JOB_STATE_PARTIALLY_SUCCEEDED",
    "JOB_STATE_FAILED",
    "JOB_STATE_CANCELLED",
    "JOB_STATE_EXPIRED",
}


def gemini_state(job) -> str:
    state = getattr(job, "state", None)
    return (getattr(state, "name", None) or str(state or "")).upper()


def _batch_gemini(
    prompts: dict[str, str], *, system, model, max_tokens, kind, project_id, json_only
) -> dict[str, str]:
    """Run the batch, and re-run it once without the thinking budget if that is
    what the whole batch was rejected for.

    The single-call path learns this from the exception it catches. A batch has
    no exception to catch: it is ACCEPTED, runs for minutes, and then reports
    per-entry errors — so on gemini-3.1-pro-preview every entry came back
    `code=3 Request contains an invalid argument`, the caller saw an empty
    result, and the portion failed with "no page summaries could be generated",
    naming nothing a user could act on.

    The re-run is bounded to one attempt and only fires when NOTHING came back,
    so it never costs a batch that partly worked; a model it rescues is latched
    so the rest of the project's tiers skip straight to the shape that works.
    """
    results, rejected = _run_gemini_batch(
        prompts, system=system, model=model, max_tokens=max_tokens,
        kind=kind, project_id=project_id, json_only=json_only, thinking=True,
    )
    if results or not rejected or model in _no_thinking_config:
        return results

    # One rung, not off the end: a batch is expensive enough that the retry
    # should be the setting most likely to WORK, and on a level-taking model
    # omitting the field asks for the most thinking rather than none.
    ladder = _thinking_ladder(model)
    retry = ladder[1] if len(ladder) > 1 else None
    log.warning(
        "gemini batch: all %d entries were rejected as invalid arguments — "
        "resubmitting once with %s",
        len(prompts),
        retry or "the thinking field omitted",
    )
    results, _ = _run_gemini_batch(
        prompts, system=system, model=model, max_tokens=max_tokens,
        kind=kind, project_id=project_id, json_only=json_only,
        thinking=retry if retry is not None else False,
    )
    if results:
        _latch_thinking(model, retry)
    else:
        log.error(
            "gemini batch: %s rejected every entry with and without a thinking "
            "setting — check SUMMARY_GEMINI_MODEL/GEMINI_MODEL names a model your "
            "key can call, and set GEMINI_THINKING_LEVEL (Gemini 3 and later) or "
            "GEMINI_THINKING_BUDGET (earlier) to a value it accepts",
            model,
        )
    return results


def _run_gemini_batch(
    prompts: dict[str, str],
    *,
    system,
    model,
    max_tokens,
    kind,
    project_id,
    json_only,
    thinking: bool,
) -> tuple[dict[str, str], bool]:
    """One submit-and-collect pass. Returns (results, every_entry_was_rejected)."""
    client = gemini_client()
    if client is None:
        return {}, False

    # Same builder as the single-call path: a batched page and a retried page
    # must be asked for exactly the same thing, or the retry silently changes
    # the answer's shape.
    request_config = _gemini_config(
        model=model,
        max_tokens=max_tokens,
        system=system,
        json_only=json_only,
        thinking=thinking,
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
    job = await_batch(
        f"gemini batch {job.name}",
        lambda: client.batches.get(name=job.name),
        lambda current: gemini_state(current) in GEMINI_TERMINAL_STATES,
    )

    state = gemini_state(job)
    responses = getattr(getattr(job, "dest", None), "inlined_responses", None) or []
    if state != "JOB_STATE_SUCCEEDED":
        log.warning(
            "gemini batch %s ended %s — using the %d entries that returned",
            getattr(job, "name", "?"),
            state,
            len(responses),
        )

    results: dict[str, str] = {}
    rejected = 0
    for index, entry in enumerate(responses):
        error = getattr(entry, "error", None)
        if error is not None:
            # Only the first few, or a 400-page batch logs 400 identical lines.
            if rejected < 3:
                log.warning("batch entry %d failed (%s)", index, error)
            if _is_thinking_refusal(error):
                rejected += 1
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

    if rejected:
        log.warning(
            "gemini batch: %d/%d entries rejected as invalid arguments",
            rejected,
            len(responses),
        )
    # "Every entry was rejected for its request shape" is the only case the
    # caller re-runs on — a batch that merely returned nothing (empty, timed
    # out, cancelled) has no reason to be resubmitted differently.
    return results, bool(responses) and rejected == len(responses)


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


# A model name the provider does not recognise is not a transient failure. It
# will fail identically on the next page and the four hundred after it, and
# every caller here is built to fall back rather than stop — so the run
# FINISHES, with the rules ladder standing in for the sheet reader or with no
# description on any page, and the only trace is one warning per call buried in
# a log that has thousands.
#
# It is also not an exotic case. Google retires a model by removing it FOR NEW
# KEYS first ("models/gemini-2.5-flash is no longer available to new users"),
# so a default that is correct for whoever configured the deployment becomes a
# 404 for whoever creates a key next month, with no commit in between. Say it
# once per model and stage, at ERROR, and say that the fix is an env var:
# nothing in this module can recover from it.
_MISSING_MODEL_SIGNS = ("not_found", "not found", "no longer available")
_missing_model_reported: set[tuple[str, str]] = set()


def _note_missing_model(provider: str, model: str, kind: str, exc: object) -> None:
    text = str(exc).lower()
    if not any(sign in text for sign in _MISSING_MODEL_SIGNS):
        return
    if (model, kind) in _missing_model_reported:
        return
    _missing_model_reported.add((model, kind))
    log.error(
        "%s does not recognise the model %r. EVERY %s call will fail the same way, "
        "and this stage will spend the rest of the run falling back instead of "
        "stopping — so the job will look like it worked. This is configuration, "
        "not a transient error: point the %s stage at a model your API key can "
        "reach. The provider said: %s",
        provider,
        model,
        kind,
        kind,
        exc,
    )


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
    images: list[bytes] | None = None,
    thinking: str | None = None,
) -> Reply | None:
    """Ask the given provider for a completion.

    `thinking` is the call site's own setting from THINKING_SETTINGS (read it
    with `stage_thinking("RFI_THINKING")`); None leaves the global
    CLAUDE_THINKING / GEMINI_THINKING_LEVEL defaults in charge, which is what
    every call site sent before the parameter existed.

    `images` are PNG bytes shown to the model alongside `user`. Every caller
    that passes none sends exactly the request it sent before this parameter
    existed — see `_user_content_claude`. A provider that cannot see (or a
    model on that provider that cannot) is the caller's problem to check, not
    this transport's: it ships what it is given.

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
                images=images,
                thinking_setting=thinking,
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
                images=images,
                thinking_setting=thinking,
            )
    except Exception as exc:
        _note_missing_model(provider, model_for(provider, claude_model, gemini_model), kind, exc)
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
