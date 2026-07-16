"""Voyage AI embeddings + Qdrant upserts.

Chunks are embedded in batches (Voyage takes up to 128 inputs per call) and
upserted into one Qdrant collection partitioned by project_id payload —
PostgreSQL stays the source of truth for references; Qdrant holds vectors
plus the filterable payload {project_id, document_id, page, portion,
discipline}. Point ID == chunk ID.

Without VOYAGE_API_KEY the embed step is skipped (chunks keep a NULL
embeddingId and are picked up by a later retry once a key exists).
"""

from __future__ import annotations

import os

import httpx

import config

VOYAGE_BASE_URL = os.environ.get("VOYAGE_BASE_URL", "https://api.voyageai.com")
VOYAGE_MODEL = os.environ.get("VOYAGE_MODEL", "voyage-3")
EMBEDDING_DIM = int(os.environ.get("EMBEDDING_DIM", "1024"))
COLLECTION = os.environ.get("QDRANT_COLLECTION", "chunks")
QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")
BATCH_SIZE = 128


def voyage_available() -> bool:
    return bool(os.environ.get("VOYAGE_API_KEY"))


def embed_texts(texts: list[str], input_type: str = "document") -> list[list[float]]:
    """Batched Voyage embedding call. input_type: 'document' | 'query'."""
    api_key = os.environ["VOYAGE_API_KEY"]
    vectors: list[list[float]] = []
    with httpx.Client(timeout=120) as client:
        for start in range(0, len(texts), BATCH_SIZE):
            batch = texts[start : start + BATCH_SIZE]
            response = client.post(
                f"{VOYAGE_BASE_URL}/v1/embeddings",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"input": batch, "model": VOYAGE_MODEL, "input_type": input_type},
            )
            response.raise_for_status()
            data = response.json()["data"]
            vectors.extend(item["embedding"] for item in sorted(data, key=lambda d: d["index"]))
    return vectors


def _chunk_payload(chunk: dict) -> dict:
    return {
        "project_id": chunk["project_id"],
        "document_id": chunk["document_id"],
        "page": chunk["combined_page"],
        "page_number": chunk["page_number"],
        "portion": chunk["portion_id"],
        "discipline": chunk["discipline"],
    }


def ensure_collection() -> None:
    with httpx.Client(timeout=30) as client:
        exists = client.get(f"{QDRANT_URL}/collections/{COLLECTION}/exists").json()
        if exists.get("result", {}).get("exists"):
            return
        client.put(
            f"{QDRANT_URL}/collections/{COLLECTION}",
            json={"vectors": {"size": EMBEDDING_DIM, "distance": "Cosine"}},
        ).raise_for_status()
        # payload indexes for the filters the chat API uses
        for field, schema in (("project_id", "keyword"), ("portion", "keyword")):
            client.put(
                f"{QDRANT_URL}/collections/{COLLECTION}/index",
                json={"field_name": field, "field_schema": schema},
            )


def upsert_chunks(chunks: list[dict], vectors: list[list[float]]) -> None:
    points = [
        {"id": chunk["chunk_id"], "vector": vector, "payload": _chunk_payload(chunk)}
        for chunk, vector in zip(chunks, vectors)
    ]
    with httpx.Client(timeout=120) as client:
        for start in range(0, len(points), 256):
            client.put(
                f"{QDRANT_URL}/collections/{COLLECTION}/points?wait=true",
                json={"points": points[start : start + 256]},
            ).raise_for_status()


def refresh_payloads(chunks: list[dict]) -> None:
    """Re-write payloads for already-embedded chunks after portion rebuilds
    shift portion/discipline/combined page (vectors untouched)."""
    with httpx.Client(timeout=120) as client:
        for chunk in chunks:
            client.post(
                f"{QDRANT_URL}/collections/{COLLECTION}/points/payload?wait=true",
                json={"payload": _chunk_payload(chunk), "points": [chunk["chunk_id"]]},
            ).raise_for_status()


def embed_document_chunks(chunks: list[dict]) -> list[str]:
    """Embed + upsert; returns the chunk IDs now present in Qdrant."""
    if not chunks:
        return []
    if not voyage_available():
        print(f"[embeddings] VOYAGE_API_KEY not set — skipping {len(chunks)} chunks")
        return []
    ensure_collection()
    vectors = embed_texts([c["text"] for c in chunks], input_type="document")
    upsert_chunks(chunks, vectors)
    return [c["chunk_id"] for c in chunks]
