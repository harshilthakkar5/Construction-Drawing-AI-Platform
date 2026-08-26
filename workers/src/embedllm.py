"""Provider-neutral embedding transport: Voyage, Cohere or Gemini.

The mirror of `llm.py` for the retrieval side, and it holds the same
invariant: the provider is a TRANSPORT detail. All three are asked to embed
exactly the same chunk text with the same document/query distinction, and all
three return a plain list of vectors of `dimensions()` floats — so the rest of
the pipeline (Qdrant upsert, chunk→page→bbox citation mapping) cannot tell
which one ran, and the three are directly comparable.

    EMBEDDING_PROVIDER = voyage (default) | cohere | gemini
    EMBEDDING_MODEL    overrides the per-provider default
    EMBEDDING_DIM      vector width — MUST match the Qdrant collection

What is NOT interchangeable is the vectors themselves. Two providers embed
into different spaces, and so do two models of the same provider: mixing them
in one collection silently destroys retrieval quality, because a cosine
distance between spaces is noise. Switching therefore means a full re-index
(see EMBEDDING_DIM / QDRANT_COLLECTION in .env.example), which is why the
default stays `voyage` — an existing deployment must not change spaces
because it pulled a new commit.

Cost control lives here too, in two layers that stack:

  1. Request batching (always on). Many inputs per HTTP call — round trips,
     not tokens, are what a 1000-page project spends its wall clock and its
     rate limit on. Each provider has its own ceiling on inputs and tokens
     per request, so the packer is per-provider.
  2. The async Batch API (EMBED_USE_BATCH=true), billed at 50%. Only Gemini
     offers one for embeddings today; Voyage has no batch endpoint and
     Cohere's Embed Jobs API requires uploading a Dataset first and carries
     no discount, so for those two the switch is a no-op that logs once and
     keeps the synchronous path. `embed()` is the only entry point either
     way — the caller never branches on provider.

Deduplication and the cross-run Redis cache sit one level up, in
`embeddings.py`, because they are about chunk text rather than transport.
"""

from __future__ import annotations

import math
import os
import time

import httpx

import llm
import logutil

log = logutil.get("embedllm")

PROVIDERS = ("voyage", "cohere", "gemini")

# Per-provider defaults. Deliberately conservative:
#
#   voyage  voyage-3 stays the default so a deployment that pulls this commit
#           keeps embedding into the space its Qdrant collection already
#           holds. voyage-3.5 is better and cheaper — switching to it is a
#           re-index, not a config tweak.
#   cohere  embed-v4.0, the current multimodal model; 1024 is one of its
#           native Matryoshka widths, so no re-tuning against the existing
#           collection size.
#   gemini  gemini-embedding-001, whose native width is 3072 and which
#           truncates to 1024 via Matryoshka (see _normalize below).
DEFAULT_MODELS = {
    "voyage": "voyage-3",
    "cohere": "embed-v4.0",
    "gemini": "gemini-embedding-001",
}

KEY_ENV = {
    "voyage": "VOYAGE_API_KEY",
    "cohere": "COHERE_API_KEY",
    "gemini": "GEMINI_API_KEY",
}

DEFAULT_BASE_URLS = {
    "voyage": "https://api.voyageai.com",
    "cohere": "https://api.cohere.com",
    "gemini": "https://generativelanguage.googleapis.com",
}

# Inputs accepted in ONE request. Provider limits, not preferences: exceeding
# them is a 400, not a slower call.
MAX_INPUTS = {"voyage": 128, "cohere": 96, "gemini": 100}

# Token ceiling per request. A request can be under the input limit and still
# be rejected for total size — a page of schedules chunks into 800-token
# blocks, so 128 of them is ~100k tokens. Approximate (chars/4), and each is
# set below the published limit to leave room for that approximation.
MAX_BATCH_TOKENS = {"voyage": 100_000, "cohere": 90_000, "gemini": 18_000}

# Both directions of a retrieval index. Every provider distinguishes them, and
# the distinction is not cosmetic: embedding a question as if it were a
# document measurably costs recall, which is why this is a required argument
# rather than a default.
INPUT_TYPES = {
    "voyage": {"document": "document", "query": "query"},
    "cohere": {"document": "search_document", "query": "search_query"},
    "gemini": {"document": "RETRIEVAL_DOCUMENT", "query": "RETRIEVAL_QUERY"},
}


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, "") or default))
    except ValueError:
        return default


def provider() -> str:
    """The active provider, read per call rather than cached at import — a
    config change takes effect without a restart, and tests can flip it. An
    unrecognised value falls back to voyage rather than raising: a typo must
    not take indexing offline."""
    name = (os.environ.get("EMBEDDING_PROVIDER") or "voyage").strip().lower()
    if name not in PROVIDERS:
        log.warning("unknown EMBEDDING_PROVIDER %r — falling back to voyage", name)
        return "voyage"
    return name


def model(name: str | None = None) -> str:
    return os.environ.get("EMBEDDING_MODEL") or DEFAULT_MODELS[name or provider()]


def dimensions() -> int:
    """Vector width. Must equal the Qdrant collection's — `ensure_collection`
    checks it rather than letting Qdrant reject the upsert cryptically."""
    return _env_int("EMBEDDING_DIM", 1024)


def available(name: str | None = None) -> bool:
    return bool(os.environ.get(KEY_ENV[name or provider()]))


def base_url(name: str) -> str:
    # Per-provider override so an offline E2E run can point any of the three
    # at a stub (as ANTHROPIC_BASE_URL / VOYAGE_BASE_URL already do).
    return os.environ.get(f"{name.upper()}_BASE_URL") or DEFAULT_BASE_URLS[name]


def batch_size(name: str | None = None) -> int:
    """Inputs per request. VOYAGE_BATCH_SIZE is still honoured so an existing
    deployment's tuning survives the rename."""
    name = name or provider()
    default = MAX_INPUTS[name]
    configured = os.environ.get("EMBED_BATCH_SIZE") or os.environ.get("VOYAGE_BATCH_SIZE")
    if not configured:
        return default
    try:
        return min(default, max(1, int(configured)))
    except ValueError:
        return default


def batch_delay_seconds() -> float:
    """Pause between requests. The free Voyage tier is ~3 requests/min, and a
    400-page document walks straight into it; a delay keeps a large job under
    the limit instead of burning its retries."""
    raw = os.environ.get("EMBED_BATCH_DELAY") or os.environ.get("VOYAGE_BATCH_DELAY") or "0"
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 0.0


def max_retries() -> int:
    return _env_int("EMBED_MAX_RETRIES", _env_int("VOYAGE_MAX_RETRIES", 6))


def sends_dimension(name: str | None = None) -> bool:
    """Whether to ask the provider for a specific vector width.

    Cohere and Gemini take the parameter on every current model. Voyage does
    NOT on voyage-3 (its width is fixed at 1024 and the field is rejected),
    only on the Matryoshka models — voyage-3.5, voyage-3-large, voyage-code-3.
    So the default is off for Voyage, and EMBED_SEND_DIMENSION=true turns it
    on when you move to one of those. Getting this wrong is loud either way:
    a rejected parameter is a 400, and a width that disagrees with the
    collection is caught by `embeddings.ensure_collection`.
    """
    override = os.environ.get("EMBED_SEND_DIMENSION")
    if override:
        return override.strip().lower() not in ("false", "0", "no", "off")
    return (name or provider()) != "voyage"


def use_batch() -> bool:
    """Whether to run bulk document embedding through the async Batch API."""
    return (os.environ.get("EMBED_USE_BATCH") or "false").strip().lower() == "true"


def batch_minimum() -> int:
    """Below this many texts, the async batch is not worth its latency: a
    batch is billed at half but lands in minutes rather than milliseconds, and
    stage 5/6 of the pipeline waits on it. Half of a two-second call is not
    worth a five-minute wait."""
    return _env_int("EMBED_BATCH_MIN", 200)


def approx_tokens(text: str) -> int:
    """~4 characters per token. Only ever used to decide where to SPLIT a
    request, never to bill anything — the providers report their own counts
    and those are what `usage` records (except Gemini, which reports none;
    see _record)."""
    return max(1, len(text) // 4)


def pack(texts: list[str], name: str | None = None) -> list[list[int]]:
    """Group text indexes into requests that respect both provider ceilings.

    Returns index groups rather than the texts themselves so the caller can
    reassemble results in the original order — a provider is free to return
    them out of order (Voyage numbers them), and a dropped result must be
    detectable rather than silently shifting every vector after it onto the
    wrong chunk.
    """
    name = name or provider()
    max_inputs = batch_size(name)
    max_tokens = _env_int("EMBED_MAX_BATCH_TOKENS", MAX_BATCH_TOKENS[name])

    groups: list[list[int]] = []
    current: list[int] = []
    current_tokens = 0
    for index, text in enumerate(texts):
        tokens = approx_tokens(text)
        # A single text over the whole budget still gets its own request: the
        # provider will truncate or reject it, and either is better than
        # silently dropping the chunk here.
        if current and (len(current) >= max_inputs or current_tokens + tokens > max_tokens):
            groups.append(current)
            current, current_tokens = [], 0
        current.append(index)
        current_tokens += tokens
    if current:
        groups.append(current)
    return groups


def _record(project_id: str | None, model_name: str, tokens: int, *, texts: list[str]) -> None:
    """One usage row per request. Gemini's embed endpoints report no token
    count at all, so its rows carry the chars/4 estimate — flagged here rather
    than left for someone to discover in the spend dashboard."""
    if tokens <= 0:
        tokens = sum(approx_tokens(t) for t in texts)
    import usage

    usage.record(project_id, "embedding", model_name, input_tokens=tokens)


# --- HTTP transports ------------------------------------------------------


def _post_with_retry(client: httpx.Client, url: str, *, headers: dict, json: dict):
    """One request with retry/backoff on 429 (rate limit) and 5xx. Honors
    Retry-After when present; otherwise exponential backoff."""
    attempts = max_retries()
    for attempt in range(attempts + 1):
        response = client.post(url, headers=headers, json=json)
        if response.status_code == 429 or response.status_code >= 500:
            if attempt == attempts:
                response.raise_for_status()
            retry_after = response.headers.get("retry-after")
            wait = float(retry_after) if retry_after else min(60.0, 2.0 * (2**attempt))
            log.warning(
                "embedding %s (attempt %d/%d) — backing off %.1fs",
                response.status_code,
                attempt + 1,
                attempts,
                wait,
            )
            time.sleep(wait)
            continue
        response.raise_for_status()
        return response
    raise RuntimeError("unreachable")


def _voyage_request(texts: list[str], input_type: str, model_name: str) -> dict:
    body: dict = {"input": texts, "model": model_name, "input_type": input_type}
    if sends_dimension("voyage"):
        body["output_dimension"] = dimensions()
    return body


def _voyage(client, texts, input_type, model_name, project_id):
    response = _post_with_retry(
        client,
        f"{base_url('voyage')}/v1/embeddings",
        headers={"Authorization": f"Bearer {os.environ['VOYAGE_API_KEY']}"},
        json=_voyage_request(texts, INPUT_TYPES["voyage"][input_type], model_name),
    )
    body = response.json()
    # Voyage numbers its results; sort rather than trusting request order.
    vectors = [item["embedding"] for item in sorted(body["data"], key=lambda d: d["index"])]
    _record(project_id, model_name, (body.get("usage") or {}).get("total_tokens", 0), texts=texts)
    return vectors


def _cohere_request(texts: list[str], input_type: str, model_name: str) -> dict:
    body: dict = {
        "texts": texts,
        "model": model_name,
        "input_type": input_type,
        # v2 can return int8/binary too; float is what Qdrant holds here.
        "embedding_types": ["float"],
        # A chunk longer than the model's context is truncated from the end
        # rather than failing the whole request — the chunker caps chunks well
        # under it, so this only fires on a pathological table.
        "truncate": "END",
    }
    if sends_dimension("cohere"):
        body["output_dimension"] = dimensions()
    return body


def _cohere(client, texts, input_type, model_name, project_id):
    response = _post_with_retry(
        client,
        f"{base_url('cohere')}/v2/embed",
        headers={"Authorization": f"Bearer {os.environ['COHERE_API_KEY']}"},
        json=_cohere_request(texts, INPUT_TYPES["cohere"][input_type], model_name),
    )
    body = response.json()
    embeddings_field = body["embeddings"]
    # v2 keys the vectors by embedding type; v1 returned a bare list.
    vectors = (
        embeddings_field["float"] if isinstance(embeddings_field, dict) else embeddings_field
    )
    tokens = ((body.get("meta") or {}).get("billed_units") or {}).get("input_tokens", 0)
    _record(project_id, model_name, tokens, texts=texts)
    return vectors


def _normalize(vector: list[float]) -> list[float]:
    """L2-normalize. Gemini returns unit vectors only at its native 3072;
    every Matryoshka truncation below that comes back unnormalized. Qdrant's
    Cosine distance normalizes on its own, so this changes no ranking today —
    it exists so a collection created with Dot, or a vector compared outside
    Qdrant (the revision-reuse path reads them back raw), is not quietly
    wrong."""
    norm = math.sqrt(sum(value * value for value in vector))
    return [value / norm for value in vector] if norm else vector


def _gemini_request(texts: list[str], input_type: str, model_name: str) -> dict:
    request: dict = {
        "requests": [
            {
                "model": f"models/{model_name}",
                "content": {"parts": [{"text": text}]},
                "taskType": input_type,
            }
            for text in texts
        ]
    }
    if sends_dimension("gemini"):
        for entry in request["requests"]:
            entry["outputDimensionality"] = dimensions()
    return request


def _gemini(client, texts, input_type, model_name, project_id):
    response = _post_with_retry(
        client,
        f"{base_url('gemini')}/v1beta/models/{model_name}:batchEmbedContents",
        headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"]},
        json=_gemini_request(texts, INPUT_TYPES["gemini"][input_type], model_name),
    )
    body = response.json()
    vectors = [_normalize(item["values"]) for item in body["embeddings"]]
    # The Developer API reports no usage for embeddings — estimated.
    _record(project_id, model_name, 0, texts=texts)
    return vectors


_TRANSPORTS = {"voyage": _voyage, "cohere": _cohere, "gemini": _gemini}


# --- Async batch (50% billing) --------------------------------------------

# Only Gemini offers an async batch endpoint for embeddings. Recorded under a
# "<model>-batch" name so the spend dashboard prices it at the rate it was
# actually billed at instead of the synchronous one — see the matching entries
# in apps/api/src/usage.ts RATES.
BATCH_PROVIDERS = ("gemini",)


def batch_model_name(model_name: str) -> str:
    return f"{model_name}-batch"


def _gemini_batch(texts, input_type, model_name, project_id) -> list[list[float]]:
    """One async batch job for every text at once.

    The Gemini Developer API takes the requests inline, as ONE EmbedContentBatch
    holding a list of contents plus one shared config — there is no per-entry
    id to echo, so results are matched by position, which the API documents as
    preserved and which holds because a failed entry is still an entry.
    """
    client = llm.gemini_client()
    if client is None:
        return []

    config: dict = {"task_type": INPUT_TYPES["gemini"][input_type]}
    if sends_dimension("gemini"):
        config["output_dimensionality"] = dimensions()

    job = client.batches.create_embeddings(
        model=model_name,
        src={"inlined_requests": {"contents": list(texts), "config": config}},
        config={"display_name": "cdip-embedding"},
    )
    log.info("gemini embedding batch %s submitted (%d texts)", job.name, len(texts))
    job = llm.await_batch(
        f"gemini embedding batch {job.name}",
        lambda: client.batches.get(name=job.name),
        lambda current: llm.gemini_state(current) in llm.GEMINI_TERMINAL_STATES,
    )

    state = llm.gemini_state(job)
    responses = (
        getattr(getattr(job, "dest", None), "inlined_embed_content_responses", None) or []
    )
    if state != "JOB_STATE_SUCCEEDED" or len(responses) != len(texts):
        # A short or failed batch would silently shift every vector after the
        # gap onto the wrong chunk. Refuse it and let the caller re-run the
        # texts synchronously — paying full price beats corrupting retrieval.
        log.warning(
            "gemini embedding batch %s ended %s with %d/%d responses — "
            "falling back to synchronous embedding",
            getattr(job, "name", "?"),
            state,
            len(responses),
            len(texts),
        )
        return []

    vectors: list[list[float]] = []
    tokens = 0
    for index, entry in enumerate(responses):
        error = getattr(entry, "error", None)
        embedding = getattr(getattr(entry, "response", None), "embedding", None)
        values = getattr(embedding, "values", None)
        if error is not None or not values:
            log.warning("embedding batch entry %d failed (%s)", index, error)
            return []
        vectors.append(_normalize(list(values)))
        tokens += getattr(getattr(entry, "response", None), "token_count", 0) or 0

    _record(project_id, batch_model_name(model_name), tokens, texts=list(texts))
    return vectors


_warned_no_batch: set[str] = set()


def batch_supported(name: str | None = None) -> bool:
    """Whether EMBED_USE_BATCH can do anything for this provider.

    Voyage has no batch endpoint. Cohere's Embed Jobs API exists but embeds a
    previously-uploaded Dataset rather than a request payload, and carries no
    published discount — machinery and a storage dependency for no saving. For
    both, the switch stays a documented no-op rather than a silent one.
    """
    name = name or provider()
    if name in BATCH_PROVIDERS:
        return True
    if name not in _warned_no_batch:
        _warned_no_batch.add(name)
        log.info(
            "EMBED_USE_BATCH is set but %s has no async batch embedding API — "
            "using synchronous requests (still batched %d inputs per call)",
            name,
            batch_size(name),
        )
    return False


# --- Entry point ----------------------------------------------------------


def embed(
    texts: list[str], input_type: str, project_id: str | None = None
) -> list[list[float]]:
    """Embed texts with the active provider. Returns one vector per input, in
    order. input_type: 'document' | 'query'.

    Raises on a provider error rather than returning short: the caller pairs
    these with chunks positionally, so a partial result is a corrupted index.
    A batch that never finishes raises llm.BatchTimeout for the same reason it
    does for summaries — the job fails visibly and BullMQ retries, rather than
    a second full-price run silently paying for work already in flight.
    """
    if not texts:
        return []
    if input_type not in ("document", "query"):
        raise ValueError(f"unknown input_type {input_type!r}")

    name = provider()
    model_name = model(name)

    if input_type == "document" and use_batch() and len(texts) >= batch_minimum():
        if batch_supported(name):
            batched = _gemini_batch(texts, input_type, model_name, project_id)
            if len(batched) == len(texts):
                return batched
            # _gemini_batch already logged why; fall through to sync rather
            # than failing the document.

    transport = _TRANSPORTS[name]
    groups = pack(texts, name)
    delay = batch_delay_seconds()
    vectors: list[list[float]] = []
    with httpx.Client(timeout=float(os.environ.get("EMBED_TIMEOUT_SECONDS", "120"))) as client:
        for position, group in enumerate(groups):
            if position > 0 and delay > 0:
                time.sleep(delay)  # throttle to stay under the tier's rate limit
            batch = [texts[index] for index in group]
            log.debug(
                "embedding batch %d/%d (%d texts) via %s",
                position + 1,
                len(groups),
                len(batch),
                name,
            )
            result = transport(client, batch, input_type, model_name, project_id)
            if len(result) != len(batch):
                raise RuntimeError(
                    f"{name} returned {len(result)} vectors for {len(batch)} inputs"
                )
            vectors.extend(result)
    return vectors
