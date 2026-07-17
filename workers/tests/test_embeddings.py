"""Revision embedding reuse (Phase 5): unchanged chunk text copies the
previous revision's vector out of Qdrant instead of calling Voyage."""

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


def test_reuse_skips_voyage_for_unchanged_text(monkeypatch):
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

    def fake_embed(texts, input_type="document"):
        embedded_texts.extend(texts)
        return [[0.9, 0.9] for _ in texts]

    monkeypatch.setattr(embeddings, "embed_texts", fake_embed)
    monkeypatch.setenv("VOYAGE_API_KEY", "test-key")

    result = embeddings.embed_document_chunks(
        [unchanged, changed], previous_document_id="doc-old"
    )

    assert sorted(result) == ["new-1", "new-2"]
    assert embedded_texts == [changed["text"]]  # only the changed chunk hit Voyage
    assert sorted(upserted) == ["new-1", "new-2"]


def test_no_previous_revision_embeds_everything(monkeypatch):
    chunk = _chunk("new-1", "some text")
    calls: list[str] = []
    monkeypatch.setattr(embeddings, "ensure_collection", lambda: None)
    monkeypatch.setattr(embeddings, "upsert_chunks", lambda chunks, vectors: None)
    monkeypatch.setattr(
        embeddings, "embed_texts", lambda texts, input_type="document": (calls.extend(texts), [[0.0]] * len(texts))[1]
    )
    monkeypatch.setenv("VOYAGE_API_KEY", "test-key")

    assert embeddings.embed_document_chunks([chunk]) == ["new-1"]
    assert calls == ["some text"]


def test_reuse_falls_back_to_voyage_when_old_points_gone(monkeypatch):
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
        embeddings, "embed_texts", lambda texts, input_type="document": (embedded.extend(texts), [[0.0]] * len(texts))[1]
    )
    monkeypatch.setenv("VOYAGE_API_KEY", "test-key")

    assert embeddings.embed_document_chunks([chunk], previous_document_id="doc-old") == ["new-1"]
    assert embedded == ["unchanged text"]
