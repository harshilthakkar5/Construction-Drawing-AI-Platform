"""Embedding transport: provider switch, request packing, response parsing.

The invariant under test is the one the module exists for — all three
providers are asked to embed the same texts and return one vector per input,
in the caller's order. A vector landing on the wrong chunk is the failure that
matters here: it does not raise anywhere, it just makes every citation for
that chunk point at the wrong sheet.
"""

import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import embedllm  # noqa: E402


# --- Fake transport -------------------------------------------------------


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.headers = {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeClient:
    """Stands in for httpx.Client; records every request it is given."""

    def __init__(self, responder):
        self.responder = responder
        self.requests: list[dict] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, headers=None, json=None):
        self.requests.append({"url": url, "headers": headers or {}, "body": json or {}})
        return self.responder(json or {})


@pytest.fixture
def recorded_usage(monkeypatch):
    """embedllm imports `usage` lazily inside _record — replace the module so
    nothing reaches Postgres and the rows can be asserted on."""
    rows: list[dict] = []
    monkeypatch.setitem(
        sys.modules,
        "usage",
        types.SimpleNamespace(
            record=lambda project_id, kind, model, input_tokens=0, **rest: rows.append(
                {
                    "project_id": project_id,
                    "kind": kind,
                    "model": model,
                    "input_tokens": input_tokens,
                }
            )
        ),
    )
    return rows


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in (
        "EMBEDDING_PROVIDER",
        "EMBEDDING_MODEL",
        "EMBEDDING_DIM",
        "EMBED_BATCH_SIZE",
        "VOYAGE_BATCH_SIZE",
        "EMBED_MAX_BATCH_TOKENS",
        "EMBED_BATCH_DELAY",
        "VOYAGE_BATCH_DELAY",
        "EMBED_SEND_DIMENSION",
        "EMBED_USE_BATCH",
        "EMBED_BATCH_MIN",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VOYAGE_API_KEY", "voyage-key")
    monkeypatch.setenv("COHERE_API_KEY", "cohere-key")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")


def _install_client(monkeypatch, responder) -> FakeClient:
    client = FakeClient(responder)
    monkeypatch.setattr(embedllm.httpx, "Client", lambda **kwargs: client)
    return client


def _voyage_responder(body):
    # Deliberately returned OUT of order, with the index field Voyage sends:
    # sorting by it is what keeps vectors on their own chunks.
    items = list(enumerate(body["input"]))
    return FakeResponse(
        {
            "data": [
                {"index": index, "embedding": [float(index), 0.0]}
                for index, _ in reversed(items)
            ],
            "usage": {"total_tokens": 11 * len(items)},
        }
    )


def _cohere_responder(body):
    return FakeResponse(
        {
            "embeddings": {
                "float": [[float(i), 1.0] for i in range(len(body["texts"]))]
            },
            "meta": {"billed_units": {"input_tokens": 7 * len(body["texts"])}},
        }
    )


def _gemini_responder(body):
    return FakeResponse(
        {"embeddings": [{"values": [3.0, 4.0]} for _ in body["requests"]]}
    )


# --- Provider resolution --------------------------------------------------


def test_provider_defaults_to_voyage_and_ignores_typos(monkeypatch):
    assert embedllm.provider() == "voyage"
    monkeypatch.setenv("EMBEDDING_PROVIDER", "COHERE")  # case-insensitive
    assert embedllm.provider() == "cohere"
    monkeypatch.setenv("EMBEDDING_PROVIDER", "voyge")
    assert embedllm.provider() == "voyage"  # a typo must not take indexing offline


def test_model_default_per_provider_and_override(monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "gemini")
    assert embedllm.model() == "gemini-embedding-001"
    monkeypatch.setenv("EMBEDDING_MODEL", "gemini-embedding-002")
    assert embedllm.model() == "gemini-embedding-002"


def test_available_follows_the_active_provider(monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "cohere")
    monkeypatch.delenv("COHERE_API_KEY")
    assert embedllm.available() is False
    monkeypatch.setenv("EMBEDDING_PROVIDER", "voyage")
    assert embedllm.available() is True


# --- Packing --------------------------------------------------------------


def test_pack_respects_the_input_ceiling(monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "cohere")  # 96 inputs per request
    groups = embedllm.pack(["x"] * 200, "cohere")
    assert [len(g) for g in groups] == [96, 96, 8]
    # every index exactly once, in order
    assert [i for g in groups for i in g] == list(range(200))


def test_pack_splits_on_the_token_budget(monkeypatch):
    monkeypatch.setenv("EMBED_MAX_BATCH_TOKENS", "100")
    texts = ["a" * 240] * 4  # ~60 tokens each
    groups = embedllm.pack(texts, "voyage")
    assert [len(g) for g in groups] == [1, 1, 1, 1]


def test_pack_keeps_an_oversized_text_rather_than_dropping_it():
    groups = embedllm.pack(["a" * 4_000_000, "small"], "voyage")
    assert [i for g in groups for i in g] == [0, 1]


def test_batch_size_honours_the_legacy_voyage_variable(monkeypatch):
    monkeypatch.setenv("VOYAGE_BATCH_SIZE", "32")
    assert embedllm.batch_size("voyage") == 32
    # ...but never above the provider's own ceiling
    monkeypatch.setenv("EMBED_BATCH_SIZE", "500")
    assert embedllm.batch_size("cohere") == 96


# --- Request shape --------------------------------------------------------


def test_voyage_request_omits_the_dimension_by_default(monkeypatch):
    body = embedllm._voyage_request(["a"], "document", "voyage-3")
    assert body == {"input": ["a"], "model": "voyage-3", "input_type": "document"}
    # voyage-3.5 and friends accept it; the switch is explicit.
    monkeypatch.setenv("EMBED_SEND_DIMENSION", "true")
    monkeypatch.setenv("EMBEDDING_DIM", "512")
    assert embedllm._voyage_request(["a"], "document", "voyage-3.5")["output_dimension"] == 512


def test_cohere_and_gemini_requests_carry_the_dimension_and_task_type():
    cohere = embedllm._cohere_request(["a"], "search_query", "embed-v4.0")
    assert cohere["input_type"] == "search_query"
    assert cohere["embedding_types"] == ["float"]
    assert cohere["output_dimension"] == 1024

    gemini = embedllm._gemini_request(["a", "b"], "RETRIEVAL_DOCUMENT", "gemini-embedding-001")
    assert len(gemini["requests"]) == 2
    assert gemini["requests"][0]["model"] == "models/gemini-embedding-001"
    assert gemini["requests"][0]["taskType"] == "RETRIEVAL_DOCUMENT"
    assert gemini["requests"][0]["outputDimensionality"] == 1024


def test_query_and_document_are_different_requests(monkeypatch, recorded_usage):
    client = _install_client(monkeypatch, _voyage_responder)
    embedllm.embed(["a"], "document")
    embedllm.embed(["a"], "query")
    assert [r["body"]["input_type"] for r in client.requests] == ["document", "query"]


def test_unknown_input_type_is_rejected():
    with pytest.raises(ValueError):
        embedllm.embed(["a"], "search_document")


# --- Round trips ----------------------------------------------------------


def test_voyage_vectors_come_back_in_request_order(monkeypatch, recorded_usage):
    monkeypatch.setenv("EMBED_BATCH_SIZE", "2")
    client = _install_client(monkeypatch, _voyage_responder)

    vectors = embedllm.embed(["a", "b", "c"], "document", project_id="proj")

    assert len(client.requests) == 2  # 2 + 1
    assert vectors == [[0.0, 0.0], [1.0, 0.0], [0.0, 0.0]]
    assert [row["model"] for row in recorded_usage] == ["voyage-3", "voyage-3"]
    assert sum(row["input_tokens"] for row in recorded_usage) == 33
    assert {row["project_id"] for row in recorded_usage} == {"proj"}


def test_cohere_reads_the_float_key(monkeypatch, recorded_usage):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "cohere")
    _install_client(monkeypatch, _cohere_responder)

    assert embedllm.embed(["a", "b"], "document") == [[0.0, 1.0], [1.0, 1.0]]
    assert recorded_usage[0]["model"] == "embed-v4.0"
    assert recorded_usage[0]["input_tokens"] == 14


def test_gemini_vectors_are_normalized(monkeypatch, recorded_usage):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "gemini")
    _install_client(monkeypatch, _gemini_responder)

    # (3, 4) has length 5 — Matryoshka truncations come back unnormalized.
    assert embedllm.embed(["a"], "document") == [[0.6, 0.8]]
    # No token count from the Developer API: the chars/4 estimate is recorded.
    assert recorded_usage[0]["input_tokens"] == 1


def test_a_short_provider_reply_raises_instead_of_misaligning(monkeypatch, recorded_usage):
    """One vector short would shift every later vector onto the wrong chunk."""
    _install_client(
        monkeypatch,
        lambda body: FakeResponse({"data": [{"index": 0, "embedding": [1.0]}], "usage": {}}),
    )
    with pytest.raises(RuntimeError, match="1 vectors for 2 inputs"):
        embedllm.embed(["a", "b"], "document")


def test_empty_input_never_calls_the_provider(monkeypatch):
    _install_client(monkeypatch, lambda body: pytest.fail("should not be called"))
    assert embedllm.embed([], "document") == []


def test_rate_limits_are_retried_then_succeed(monkeypatch, recorded_usage):
    monkeypatch.setattr(embedllm.time, "sleep", lambda seconds: None)
    calls = {"n": 0}

    def responder(body):
        calls["n"] += 1
        if calls["n"] == 1:
            return FakeResponse({}, status_code=429)
        return _voyage_responder(body)

    _install_client(monkeypatch, responder)
    assert embedllm.embed(["a"], "document") == [[0.0, 0.0]]
    assert calls["n"] == 2


# --- Async batch ----------------------------------------------------------


def test_batch_is_only_claimed_where_it_exists(monkeypatch):
    embedllm._warned_no_batch.clear()
    assert embedllm.batch_supported("gemini") is True
    assert embedllm.batch_supported("voyage") is False
    assert embedllm.batch_supported("cohere") is False


def test_small_runs_skip_the_async_batch(monkeypatch, recorded_usage):
    """Half price is not worth minutes of latency on a handful of chunks."""
    monkeypatch.setenv("EMBEDDING_PROVIDER", "gemini")
    monkeypatch.setenv("EMBED_USE_BATCH", "true")
    monkeypatch.setattr(
        embedllm, "_gemini_batch", lambda *a, **k: pytest.fail("batched a tiny run")
    )
    _install_client(monkeypatch, _gemini_responder)

    assert embedllm.embed(["a"], "document") == [[0.6, 0.8]]


def test_batch_runs_for_bulk_documents_and_bills_at_the_batch_rate(monkeypatch, recorded_usage):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "gemini")
    monkeypatch.setenv("EMBED_USE_BATCH", "true")
    monkeypatch.setenv("EMBED_BATCH_MIN", "2")

    texts = ["a", "b", "c"]
    entries = [
        types.SimpleNamespace(
            error=None,
            response=types.SimpleNamespace(
                embedding=types.SimpleNamespace(values=[3.0, 4.0]), token_count=5
            ),
        )
        for _ in texts
    ]
    job = types.SimpleNamespace(
        name="batches/x",
        state="JOB_STATE_SUCCEEDED",
        dest=types.SimpleNamespace(inlined_embed_content_responses=entries),
    )
    submitted: dict = {}
    fake_client = types.SimpleNamespace(
        batches=types.SimpleNamespace(
            create_embeddings=lambda **kwargs: submitted.update(kwargs) or job,
            get=lambda name: job,
        )
    )
    monkeypatch.setattr(embedllm.llm, "gemini_client", lambda: fake_client)
    _install_client(monkeypatch, lambda body: pytest.fail("batch path called HTTP"))

    vectors = embedllm.embed(texts, "document", project_id="proj")

    assert vectors == [[0.6, 0.8]] * 3
    assert submitted["src"]["inlined_requests"]["contents"] == texts
    assert submitted["src"]["inlined_requests"]["config"]["task_type"] == "RETRIEVAL_DOCUMENT"
    assert recorded_usage[0]["model"] == "gemini-embedding-001-batch"
    assert recorded_usage[0]["input_tokens"] == 15


def test_a_short_batch_falls_back_to_synchronous(monkeypatch, recorded_usage):
    """Positional matching is only safe when every request has a response."""
    monkeypatch.setenv("EMBEDDING_PROVIDER", "gemini")
    monkeypatch.setenv("EMBED_USE_BATCH", "true")
    monkeypatch.setenv("EMBED_BATCH_MIN", "2")

    job = types.SimpleNamespace(
        name="batches/x",
        state="JOB_STATE_PARTIALLY_SUCCEEDED",
        dest=types.SimpleNamespace(
            inlined_embed_content_responses=[
                types.SimpleNamespace(
                    error=None,
                    response=types.SimpleNamespace(
                        embedding=types.SimpleNamespace(values=[3.0, 4.0]), token_count=5
                    ),
                )
            ]
        ),
    )
    monkeypatch.setattr(
        embedllm.llm,
        "gemini_client",
        lambda: types.SimpleNamespace(
            batches=types.SimpleNamespace(
                create_embeddings=lambda **kwargs: job, get=lambda name: job
            )
        ),
    )
    client = _install_client(monkeypatch, _gemini_responder)

    assert embedllm.embed(["a", "b"], "document") == [[0.6, 0.8], [0.6, 0.8]]
    assert len(client.requests) == 1  # embedded synchronously instead


def test_queries_never_wait_on_a_batch(monkeypatch, recorded_usage):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "gemini")
    monkeypatch.setenv("EMBED_USE_BATCH", "true")
    monkeypatch.setenv("EMBED_BATCH_MIN", "1")
    monkeypatch.setattr(
        embedllm, "_gemini_batch", lambda *a, **k: pytest.fail("batched a query")
    )
    _install_client(monkeypatch, _gemini_responder)

    assert embedllm.embed(["question"], "query") == [[0.6, 0.8]]
