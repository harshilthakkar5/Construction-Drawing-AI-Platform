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
import time

import httpx

import config
import logutil

log = logutil.get("embeddings")

VOYAGE_BASE_URL = os.environ.get("VOYAGE_BASE_URL", "https://api.voyageai.com")
VOYAGE_MODEL = os.environ.get("VOYAGE_MODEL", "voyage-3")
EMBEDDING_DIM = int(os.environ.get("EMBEDDING_DIM", "1024"))
COLLECTION = os.environ.get("QDRANT_COLLECTION", "chunks")
QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")
BATCH_SIZE = int(os.environ.get("VOYAGE_BATCH_SIZE", "128"))
# Free Voyage tier is heavily rate-limited (~3 requests/min). A small delay
# between batches keeps a large document under the limit instead of getting
# 429'd and failing the whole job. Raise VOYAGE_BATCH_DELAY on the free tier
# (e.g. 20) or set 0 on a paid tier.
BATCH_DELAY_SECONDS = float(os.environ.get("VOYAGE_BATCH_DELAY", "0"))
MAX_RETRIES = int(os.environ.get("VOYAGE_MAX_RETRIES", "6"))


def voyage_available() -> bool:
    return bool(os.environ.get("VOYAGE_API_KEY"))


def _post_with_retry(client: httpx.Client, api_key: str, batch: list[str], input_type: str):
    """One Voyage call with retry/backoff on 429 (rate limit) and 5xx. Honors
    the Retry-After header when present; otherwise exponential backoff."""
    for attempt in range(MAX_RETRIES + 1):
        response = client.post(
            f"{VOYAGE_BASE_URL}/v1/embeddings",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"input": batch, "model": VOYAGE_MODEL, "input_type": input_type},
        )
        if response.status_code == 429 or response.status_code >= 500:
            if attempt == MAX_RETRIES:
                response.raise_for_status()
            retry_after = response.headers.get("retry-after")
            wait = float(retry_after) if retry_after else min(60.0, 2.0 * (2**attempt))
            log.warning(
                "Voyage %s (attempt %d/%d) — backing off %.1fs",
                response.status_code,
                attempt + 1,
                MAX_RETRIES,
                wait,
            )
            time.sleep(wait)
            continue
        response.raise_for_status()
        return response
    raise RuntimeError("unreachable")


def embed_texts(texts: list[str], input_type: str = "document") -> list[list[float]]:
    """Batched Voyage embedding call with rate-limit backoff.
    input_type: 'document' | 'query'."""
    api_key = os.environ["VOYAGE_API_KEY"]
    vectors: list[list[float]] = []
    batch_count = (len(texts) + BATCH_SIZE - 1) // BATCH_SIZE
    with httpx.Client(timeout=120) as client:
        for i, start in enumerate(range(0, len(texts), BATCH_SIZE)):
            batch = texts[start : start + BATCH_SIZE]
            if i > 0 and BATCH_DELAY_SECONDS > 0:
                time.sleep(BATCH_DELAY_SECONDS)  # throttle to stay under the tier limit
            log.debug("embedding batch %d/%d (%d texts)", i + 1, batch_count, len(batch))
            response = _post_with_retry(client, api_key, batch, input_type)
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


def fetch_vectors(point_ids: list[str]) -> dict[str, list[float]]:
    """Retrieve stored vectors by point ID (revision embedding reuse)."""
    if not point_ids:
        return {}
    out: dict[str, list[float]] = {}
    with httpx.Client(timeout=120) as client:
        for start in range(0, len(point_ids), 256):
            response = client.post(
                f"{QDRANT_URL}/collections/{COLLECTION}/points",
                json={
                    "ids": point_ids[start : start + 256],
                    "with_vector": True,
                    "with_payload": False,
                },
            )
            response.raise_for_status()
            for point in response.json().get("result", []):
                if point.get("vector") is not None:
                    out[str(point["id"])] = point["vector"]
    return out


def delete_document_points(document_id: str) -> None:
    """Remove a superseded revision's points so retrieval never returns it."""
    with httpx.Client(timeout=120) as client:
        response = client.post(
            f"{QDRANT_URL}/collections/{COLLECTION}/points/delete?wait=true",
            json={"filter": {"must": [{"key": "document_id", "match": {"value": document_id}}]}},
        )
        if response.status_code != 404:  # 404 = collection never created
            response.raise_for_status()


def reuse_chunk_vectors(pairs: list[tuple[dict, str]]) -> list[str]:
    """Copy vectors from a previous revision's points to new chunk IDs
    (unchanged text ⇒ identical embedding — no Voyage call). pairs:
    (new chunk dict, old embedded chunk id). Returns new chunk IDs upserted."""
    if not pairs:
        return []
    ensure_collection()
    vectors_by_old_id = fetch_vectors([old_id for _, old_id in pairs])
    reusable = [(chunk, vectors_by_old_id[old_id]) for chunk, old_id in pairs if old_id in vectors_by_old_id]
    if not reusable:
        return []
    upsert_chunks([c for c, _ in reusable], [v for _, v in reusable])
    return [c["chunk_id"] for c, _ in reusable]


def embed_document_chunks(chunks: list[dict], previous_document_id: str | None = None) -> list[str]:
    """Embed + upsert; returns the chunk IDs now present in Qdrant.

    Revision reuse (FR-4 / non-functional rule "reuse embeddings for unchanged
    revisions"): when the document replaces a previous revision, chunks whose
    textHash matches an embedded chunk of that revision copy its vector out of
    Qdrant instead of calling Voyage.
    """
    if not chunks:
        return []

    done: list[str] = []
    to_embed = chunks
    if previous_document_id is not None:
        import db  # late import to keep this module testable without psycopg

        hashes = [c["text_hash"] for c in chunks if c.get("text_hash")]
        matches = db.matching_embedded_chunks(previous_document_id, hashes)
        pairs = [(c, matches[c["text_hash"]]) for c in chunks if c.get("text_hash") in matches]
        reused = set(reuse_chunk_vectors(pairs))
        if reused:
            log.info("reused %d vectors from the previous revision (no Voyage call)", len(reused))
        done.extend(reused)
        to_embed = [c for c in chunks if c["chunk_id"] not in reused]

    if not to_embed:
        return done
    if not voyage_available():
        log.warning("VOYAGE_API_KEY not set — skipping %d chunks (they stay unindexed until a retry)", len(to_embed))
        return done
    ensure_collection()
    vectors = embed_texts([c["text"] for c in to_embed], input_type="document")
    upsert_chunks(to_embed, vectors)
    return done + [c["chunk_id"] for c in to_embed]
