"""What `embeddings.py` refuses to pay a provider for.

Three layers, each tested here: revision reuse (Phase 5) copies an unchanged
chunk's vector straight out of Qdrant; in-run dedup embeds one distinct string
once however many chunks repeat it; and the Redis cache carries that dedup
across runs. Plus the guard that stops a provider switch from quietly mixing
two embedding spaces in one collection."""

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import embeddings  # noqa: E402


def _chunk(chunk_id: str, text: str) -> dict:
    from hashing import text_hash

    return {
        "chunk_id": chunk_id,
        "text": text,
        "text_hash": text_hash(text),
        "page_number": 1,
        "combined_page": 1,
        "portion_id": None,
        "discipline": None,
        "project_id": "proj",
        "document_id": "doc-new",
    }


def test_text_hash_is_stable_and_distinct():
    from hashing import text_hash

    assert text_hash("beam W12x26") == text_hash("beam W12x26")
    assert text_hash("beam W12x26") != text_hash("beam W12x30")


def test_reuse_skips_the_provider_for_unchanged_text(monkeypatch):
    unchanged = _chunk("new-1", "GENERAL NOTES: all concrete 4000 psi")
    changed = _chunk("new-2", "REVISED: all concrete 5000 psi")

    # Previous revision has an embedded chunk with the unchanged text.
    fake_db = types.SimpleNamespace(
        matching_embedded_chunks=lambda old_doc, hashes: (
            {unchanged["text_hash"]: "old-1"} if unchanged["text_hash"] in hashes else {}
        )
    )
    monkeypatch.setitem(sys.modules, "db", fake_db)

    upserted: list[str] = []
    embedded_texts: list[str] = []
    monkeypatch.setattr(embeddings, "ensure_collection", lambda: None)
    monkeypatch.setattr(embeddings, "fetch_vectors", lambda ids: {"old-1": [0.1, 0.2]})
    monkeypatch.setattr(
        embeddings,
        "upsert_chunks",
        lambda chunks, vectors: upserted.extend(c["chunk_id"] for c in chunks),
    )

    def fake_embed(texts, input_type="document", project_id=None):
        embedded_texts.extend(texts)
        return [[0.9, 0.9] for _ in texts]

    monkeypatch.setattr(embeddings, "embed_texts", fake_embed)
    monkeypatch.setenv("VOYAGE_API_KEY", "test-key")

    result = embeddings.embed_document_chunks(
        [unchanged, changed], previous_document_id="doc-old"
    )

    assert sorted(result) == ["new-1", "new-2"]
    assert embedded_texts == [changed["text"]]  # only the changed chunk hit the provider
    assert sorted(upserted) == ["new-1", "new-2"]


def test_no_previous_revision_embeds_everything(monkeypatch):
    chunk = _chunk("new-1", "some text")
    calls: list[str] = []
    monkeypatch.setattr(embeddings, "ensure_collection", lambda: None)
    monkeypatch.setattr(embeddings, "upsert_chunks", lambda chunks, vectors: None)
    monkeypatch.setattr(
        embeddings, "embed_texts", lambda texts, input_type="document", project_id=None: (calls.extend(texts), [[0.0]] * len(texts))[1]
    )
    monkeypatch.setenv("VOYAGE_API_KEY", "test-key")

    assert embeddings.embed_document_chunks([chunk]) == ["new-1"]
    assert calls == ["some text"]


def test_reuse_falls_back_to_the_provider_when_old_points_gone(monkeypatch):
    """Retried job after the old revision's points were already deleted."""
    chunk = _chunk("new-1", "unchanged text")
    fake_db = types.SimpleNamespace(
        matching_embedded_chunks=lambda old_doc, hashes: {chunk["text_hash"]: "old-1"}
    )
    monkeypatch.setitem(sys.modules, "db", fake_db)
    monkeypatch.setattr(embeddings, "ensure_collection", lambda: None)
    monkeypatch.setattr(embeddings, "fetch_vectors", lambda ids: {})  # points deleted
    monkeypatch.setattr(embeddings, "upsert_chunks", lambda chunks, vectors: None)
    embedded: list[str] = []
    monkeypatch.setattr(
        embeddings, "embed_texts", lambda texts, input_type="document", project_id=None: (embedded.extend(texts), [[0.0]] * len(texts))[1]
    )
    monkeypatch.setenv("VOYAGE_API_KEY", "test-key")

    assert embeddings.embed_document_chunks([chunk], previous_document_id="doc-old") == ["new-1"]
    assert embedded == ["unchanged text"]


# --- Dedup + Redis vector cache -------------------------------------------


class FakeRedis:
    """Enough of a Redis for the vector cache: mget, and a set pipeline."""

    def __init__(self, stored: dict | None = None):
        self.stored: dict[str, bytes] = dict(stored or {})
        self.writes: list[str] = []

    def mget(self, keys):
        return [self.stored.get(key) for key in keys]

    def pipeline(self):
        outer = self

        class Pipe:
            def __init__(self):
                self.pending: list[tuple[str, bytes]] = []

            def set(self, key, value, ex=None):
                self.pending.append((key, value))

            def execute(self):
                for key, value in self.pending:
                    outer.stored[key] = value
                    outer.writes.append(key)

        return Pipe()


def _vector_bytes(values):
    from array import array

    return array("f", values).tobytes()


def test_repeated_text_is_embedded_once(monkeypatch):
    """A sheet set repeats its general notes on hundreds of pages."""
    monkeypatch.setattr(embeddings, "_get_redis", lambda: None)
    sent: list[list[str]] = []

    def fake_embed(texts, input_type, project_id=None):
        sent.append(texts)
        return [[float(len(t)), 0.0] for t in texts]

    monkeypatch.setattr(embeddings.embedllm, "embed", fake_embed)

    notes = "GENERAL NOTES: all concrete 4000 psi"
    vectors = embeddings.embed_texts([notes, "beam schedule", notes, notes])

    assert sent == [[notes, "beam schedule"]]  # the provider saw each string once
    assert vectors[0] == vectors[2] == vectors[3]  # ...and every chunk still got one
    assert vectors[1] == [13.0, 0.0]


def test_cached_vectors_never_reach_the_provider(monkeypatch):
    monkeypatch.setenv("EMBEDDING_DIM", "2")
    cache = FakeRedis()
    monkeypatch.setattr(embeddings, "_get_redis", lambda: cache)
    cache.stored[embeddings._cache_key("cached text", "document")] = _vector_bytes([1.0, 2.0])

    sent: list[list[str]] = []
    monkeypatch.setattr(
        embeddings.embedllm,
        "embed",
        lambda texts, input_type, project_id=None: (sent.append(texts), [[9.0, 9.0]] * len(texts))[1],
    )

    vectors = embeddings.embed_texts(["cached text", "new text"])

    assert sent == [["new text"]]
    assert vectors == [[1.0, 2.0], [9.0, 9.0]]
    # the miss is written back for the next run
    assert cache.writes == [embeddings._cache_key("new text", "document")]


def test_a_cached_vector_of_the_wrong_width_is_discarded(monkeypatch):
    """A stale EMBEDDING_DIM (or a provider switch) must not reach Qdrant."""
    monkeypatch.setenv("EMBEDDING_DIM", "4")
    cache = FakeRedis()
    monkeypatch.setattr(embeddings, "_get_redis", lambda: cache)
    cache.stored[embeddings._cache_key("text", "document")] = _vector_bytes([1.0, 2.0])

    sent: list[list[str]] = []
    monkeypatch.setattr(
        embeddings.embedllm,
        "embed",
        lambda texts, input_type, project_id=None: (sent.append(texts), [[0.0] * 4])[1],
    )

    assert embeddings.embed_texts(["text"]) == [[0.0, 0.0, 0.0, 0.0]]
    assert sent == [["text"]]  # re-embedded rather than served at the wrong width


def test_the_cache_key_separates_providers_and_models(monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "voyage")
    voyage_key = embeddings._cache_key("same text", "document")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "cohere")
    assert embeddings._cache_key("same text", "document") != voyage_key
    monkeypatch.setenv("EMBEDDING_MODEL", "embed-english-v3.0")
    assert len({voyage_key, embeddings._cache_key("same text", "document")}) == 2


def test_query_and_document_vectors_are_cached_apart(monkeypatch):
    assert embeddings._cache_key("t", "document") != embeddings._cache_key("t", "query")


# --- Collection guard -----------------------------------------------------


class FakeQdrant:
    def __init__(self, existing_size):
        self.existing_size = existing_size

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url):
        if url.endswith("/exists"):
            return types.SimpleNamespace(json=lambda: {"result": {"exists": True}})
        return types.SimpleNamespace(
            json=lambda: {
                "result": {"config": {"params": {"vectors": {"size": self.existing_size}}}}
            }
        )


def test_a_width_mismatch_fails_before_anything_is_upserted(monkeypatch):
    monkeypatch.setenv("EMBEDDING_DIM", "3072")
    monkeypatch.setattr(embeddings.httpx, "Client", lambda **kw: FakeQdrant(1024))

    import pytest

    with pytest.raises(RuntimeError, match="1024-dimension vectors"):
        embeddings.ensure_collection()


def test_a_matching_width_passes(monkeypatch):
    monkeypatch.setenv("EMBEDDING_DIM", "1024")
    monkeypatch.setattr(embeddings.httpx, "Client", lambda **kw: FakeQdrant(1024))
    embeddings.ensure_collection()
