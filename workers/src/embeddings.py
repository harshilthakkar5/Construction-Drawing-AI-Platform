"""Embeddings + Qdrant upserts.

The vectors come from whichever provider `EMBEDDING_PROVIDER` selects
(`embedllm.py` — voyage | cohere | gemini); this module owns everything above
that transport: what is worth embedding at all, and where the result goes.

Chunks are upserted into one Qdrant collection partitioned by the project_id
payload — PostgreSQL stays the source of truth for references; Qdrant holds
vectors plus the filterable payload {project_id, document_id, page, portion,
discipline}. Point ID == chunk ID.

Three layers keep the bill down, cheapest first:

  1. Revision reuse (`reuse_chunk_vectors`) — a chunk whose text is unchanged
     from the revision it replaces copies its vector straight out of Qdrant.
  2. In-run dedup — a sheet set repeats its general notes on hundreds of
     pages, and identical text has an identical vector by definition. Each
     distinct string is embedded once per call, however many chunks hold it.
  3. A Redis vector cache keyed on (provider, model, dimensions, text) — the
     same dedup across runs, which is what makes a re-index after a portion
     rebuild nearly free. Off with EMBED_CACHE_ENABLED=false.

Without the active provider's API key the embed step is skipped (chunks keep a
NULL embeddingId and are picked up by a later retry once a key exists).
"""

from __future__ import annotations

import os
from array import array

import httpx
import redis as redis_lib

import config
import embedllm
import hashing
import logutil

log = logutil.get("embeddings")

COLLECTION = os.environ.get("QDRANT_COLLECTION", "chunks")
QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")

# A vector is ~4 KB at 1024 dimensions, so the cache is bounded by a TTL
# rather than left to grow: two weeks covers the re-index that follows a
# portion rebuild or a chunker change, which is what it exists for.
CACHE_TTL_SECONDS = int(os.environ.get("EMBED_CACHE_TTL_SECONDS", str(14 * 24 * 3600)))


def provider_available() -> bool:
    return embedllm.available()


# --- Vector cache ---------------------------------------------------------

_redis = None


def _cache_enabled() -> bool:
    return (os.environ.get("EMBED_CACHE_ENABLED") or "true").strip().lower() not in (
        "false",
        "0",
        "no",
        "off",
    )


def _get_redis():
    """Shared handle for the vector cache; None when unavailable — embedding
    still runs, it just re-pays for text it has already embedded."""
    global _redis
    # Checked before the connection latch, not inside it, so flipping the flag
    # takes effect without a restart (and so a test can turn it on).
    if not _cache_enabled():
        return None
    if _redis is None:
        try:
            _redis = redis_lib.Redis.from_url(config.REDIS_URL)
            _redis.ping()
        except Exception as exc:
            log.warning("Redis embedding cache unavailable: %s", exc)
            _redis = False
    return _redis or None


def _cache_key(text: str, input_type: str) -> str:
    # Provider, model and width are all part of the key: two models embed into
    # different spaces, so serving one model's vector for another's request
    # would poison the collection in a way no error surfaces.
    return (
        f"cache:embedding:{embedllm.provider()}:{embedllm.model()}:"
        f"{embedllm.dimensions()}:{input_type}:{hashing.text_hash(text)}"
    )


def _cached_vectors(texts: list[str], input_type: str) -> dict[str, list[float]]:
    """Cached vectors for the distinct texts given. Missing keys simply do not
    appear; any cache failure degrades to an empty dict."""
    conn = _get_redis()
    if conn is None or not texts:
        return {}
    # A 1000-page project has tens of thousands of distinct chunks; one MGET
    # with every key is a multi-megabyte command and a long stall on a shared
    # Redis. Ask in slices instead.
    stored: list = []
    try:
        for start in range(0, len(texts), 512):
            window = texts[start : start + 512]
            stored.extend(conn.mget([_cache_key(text, input_type) for text in window]))
    except Exception as exc:
        log.warning("embedding cache read failed: %s", exc)
        return {}

    width = embedllm.dimensions()
    hits: dict[str, list[float]] = {}
    for text, raw in zip(texts, stored):
        if not raw:
            continue
        values = array("f")
        try:
            values.frombytes(raw)
        except ValueError:
            continue
        # A cached vector of the wrong width is a stale EMBEDDING_DIM. Drop it
        # rather than upserting something Qdrant will reject.
        if len(values) == width:
            hits[text] = list(values)
    return hits


def _store_vectors(pairs: list[tuple[str, list[float]]], input_type: str) -> None:
    conn = _get_redis()
    if conn is None or not pairs:
        return
    try:
        for start in range(0, len(pairs), 512):
            pipe = conn.pipeline()
            for text, vector in pairs[start : start + 512]:
                # float32, as Qdrant stores them: a cache hit therefore comes
                # back rounded from what the provider returned. That difference
                # is well below what any cosine ranking resolves, and it halves
                # what the cache costs in Redis.
                pipe.set(
                    _cache_key(text, input_type),
                    array("f", vector).tobytes(),
                    ex=CACHE_TTL_SECONDS,
                )
            pipe.execute()
    except Exception as exc:  # caching must never fail a job
        log.warning("embedding cache write failed: %s", exc)


# --- Embedding ------------------------------------------------------------


def embed_texts(
    texts: list[str], input_type: str = "document", project_id: str | None = None
) -> list[list[float]]:
    """Vectors for `texts`, in order, from the active provider.

    Duplicates and cache hits never reach the provider; what is left is sent
    in provider-sized batches (and, with EMBED_USE_BATCH, through its async
    batch API at half price). input_type: 'document' | 'query'. project_id
    attributes the token spend.
    """
    if not texts:
        return []

    # Distinct texts, first-seen order — the order matters only for readable
    # logs, but a stable one makes a batch reproducible.
    distinct: list[str] = list(dict.fromkeys(texts))
    known = _cached_vectors(distinct, input_type)
    missing = [text for text in distinct if text not in known]

    if len(missing) < len(texts):
        log.info(
            "embedding %d texts: %d distinct, %d cached, %d to embed",
            len(texts),
            len(distinct),
            len(distinct) - len(missing),
            len(missing),
        )

    if missing:
        fresh = embedllm.embed(missing, input_type, project_id)
        pairs = list(zip(missing, fresh))
        _store_vectors(pairs, input_type)
        known.update(pairs)

    return [known[text] for text in texts]


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
    width = embedllm.dimensions()
    with httpx.Client(timeout=30) as client:
        exists = client.get(f"{QDRANT_URL}/collections/{COLLECTION}/exists").json()
        if exists.get("result", {}).get("exists"):
            _check_collection_width(client, width)
            return
        client.put(
            f"{QDRANT_URL}/collections/{COLLECTION}",
            json={"vectors": {"size": width, "distance": "Cosine"}},
        ).raise_for_status()
        log.info(
            "created Qdrant collection %s (%d dimensions, %s/%s)",
            COLLECTION,
            width,
            embedllm.provider(),
            embedllm.model(),
        )
        # payload indexes for the filters the chat API uses
        for field, schema in (("project_id", "keyword"), ("portion", "keyword")):
            client.put(
                f"{QDRANT_URL}/collections/{COLLECTION}/index",
                json={"field_name": field, "field_schema": schema},
            )


def _check_collection_width(client: httpx.Client, width: int) -> None:
    """Fail loudly when the collection was built for a different vector width.

    Switching EMBEDDING_PROVIDER or EMBEDDING_MODEL usually changes it, and
    Qdrant's own rejection names neither side. Note that a width MATCH proves
    nothing about the space: voyage-3 and embed-v4.0 are both 1024 and mixing
    them silently destroys retrieval, which is why switching means a re-index
    into a new QDRANT_COLLECTION rather than a config change.
    """
    try:
        info = client.get(f"{QDRANT_URL}/collections/{COLLECTION}").json()
        vectors = ((info.get("result") or {}).get("config") or {}).get("params", {}).get(
            "vectors", {}
        )
        existing = vectors.get("size") if isinstance(vectors, dict) else None
    except Exception as exc:  # never block indexing on the introspection call
        log.debug("could not read %s collection config: %s", COLLECTION, exc)
        return
    if isinstance(existing, int) and existing != width:
        raise RuntimeError(
            f"Qdrant collection {COLLECTION} holds {existing}-dimension vectors but "
            f"{embedllm.provider()}/{embedllm.model()} produces {width}. Point "
            "QDRANT_COLLECTION at a new collection and re-index "
            "(POST /projects/:id/documents/reindex), or restore EMBEDDING_DIM."
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
    (unchanged text ⇒ identical embedding — no provider call). pairs:
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
    Qdrant instead of calling the provider.
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
            log.info("reused %d vectors from the previous revision (no provider call)", len(reused))
        done.extend(reused)
        to_embed = [c for c in chunks if c["chunk_id"] not in reused]

    if not to_embed:
        return done
    if not provider_available():
        log.warning(
            "%s not set — skipping %d chunks (they stay unindexed until a retry)",
            embedllm.KEY_ENV[embedllm.provider()],
            len(to_embed),
        )
        return done
    ensure_collection()
    vectors = embed_texts(
        [c["text"] for c in to_embed],
        input_type="document",
        project_id=to_embed[0].get("project_id"),
    )
    upsert_chunks(to_embed, vectors)
    return done + [c["chunk_id"] for c in to_embed]
