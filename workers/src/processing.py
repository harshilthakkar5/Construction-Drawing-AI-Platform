"""Per-document processing pipeline (FR-6..FR-9).

Streams one page at a time with PyMuPDF — memory stays flat regardless of
PDF size. Each page is uploaded and committed individually, so a failure at
page N preserves pages 1..N-1 and a retried job resumes where it left off.

Stages (each logged, errors logged with the failing stage/page):
  1/6 download   — fetch the original PDF from object storage
  2/6 extract    — per-page text/PNG/thumb (+ OCR fallback) + table
                   detection + chunking
  3/6 revisions  — supersede the replaced document (FR-4), if any
  4/6 numbering  — recompute combined page numbers (discipline detection
                   moved to the scrape-region job)
  5/6 embed      — embeddings into Qdrant (EMBEDDING_PROVIDER; reuse across revisions)
  6/6 finalize   — mark completed, invalidate caches
"""

import io
import os
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import fitz  # PyMuPDF
from PIL import Image

import cache
import chunker
import config
import db
import embeddings
import logutil
import ocr
import storage
import tables

log = logutil.get("pipeline")

# Progress heartbeat for very large PDFs: log every N pages.
PROGRESS_EVERY = 25


def _thumbnail_jpg(page) -> bytes:
    """Render the thumbnail straight from the page at thumbnail scale.

    It used to be produced by decoding the full-resolution page PNG and
    resizing it — ~18 megapixels of zlib decompression and a LANCZOS resize per
    page, to end up with a 200px image. Rendering the small one directly is a
    fraction of that: MuPDF rasterizes at the requested scale rather than
    downsampling from a big bitmap.
    """
    scale = config.THUMB_WIDTH / page.rect.width
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale))
    with Image.open(io.BytesIO(pix.tobytes("png"))) as img:
        out = io.BytesIO()
        img.convert("RGB").save(out, format="JPEG", quality=70)
        return out.getvalue()


def _process_page(project_id: str, document_id: str, pdf, index: int, offset: int) -> bool:
    """One page: extract → render → (OCR) → store → chunk. Returns True when
    the page needed OCR."""
    page_number = index + 1
    page = pdf.load_page(index)
    # strip_nul: PostgreSQL rejects NUL (0x00) bytes, which PDF extraction
    # can produce with unusual font encodings.
    text = chunker.strip_nul(page.get_text("text")).strip()

    zoom = fitz.Matrix(config.PAGE_RENDER_ZOOM, config.PAGE_RENDER_ZOOM)
    png = page.get_pixmap(matrix=zoom).tobytes("png")

    used_ocr = False
    if not text:  # FR-7: OCR only when the page has no text layer
        log.debug("page %d has no text layer — running OCR", page_number)
        text = chunker.strip_nul(ocr.ocr_png_bytes(png))
        used_ocr = True

    storage.put_bytes(
        storage.page_image_key(project_id, document_id, page_number), png, "image/png"
    )
    storage.put_bytes(
        storage.page_thumb_key(project_id, document_id, page_number),
        _thumbnail_jpg(page),
        "image/jpeg",
    )
    storage.put_bytes(
        storage.page_text_key(project_id, document_id, page_number),
        text.encode("utf-8"),
        "text/plain; charset=utf-8",
    )
    db.upsert_page(
        document_id,
        page_number,
        offset + page_number,
        storage.page_image_key(project_id, document_id, page_number),
        text,
        pdf_width=page.rect.width,
        pdf_height=page.rect.height,
    )

    # Hybrid chunking: schedules lifted out whole, then the remaining blocks
    # grouped spatially and packed into token windows — each with its own bbox.
    # The page size drives both the clustering threshold and the bbox area cap,
    # so a D-size sheet and a letter-size page are treated proportionally.
    page_chunks = chunker.chunk_page(
        page.get_text("blocks"),
        page_width=page.rect.width,
        page_height=page.rect.height,
        tables=tables.find_page_tables(page),
    )
    if not page_chunks and text:
        # OCR-only page: no positioned blocks, so one chunk spans the whole
        # page rect (coarse but truthful highlight).
        rect = page.rect
        page_chunks = [
            chunker.Chunk(
                text=text,
                bbox={"x": 0, "y": 0, "width": rect.width, "height": rect.height},
                token_count=chunker.estimate_tokens(text),
            )
        ]
    db.replace_page_chunks(document_id, page_number, page_chunks)
    log.debug("page %d done: %d chars, %d chunks", page_number, len(text), len(page_chunks))
    return used_ocr


def _extract_pages(
    project_id: str,
    document_id: str,
    pdf_path: str,
    indices: list[int],
    offset: int,
    page_count: int,
    doc_tag: str,
) -> tuple[int, int]:
    """Stage 2 across several pages at once. Returns (processed, ocr_count).

    A page is independent work — render, encode, upload, write its own rows —
    so the only reason it was serial is that the loop was. On a 400-page set
    that left the CPU idle during every upload and the network idle during
    every render.

    Each thread opens its OWN fitz.Document on the already-downloaded file:
    PyMuPDF is not thread-safe across a shared Document, and a lock around
    page loads would serialize the expensive part again. Opening is cheap
    (MuPDF parses lazily) and the file is local by this point.

    Failure semantics are unchanged: pages commit individually, so a crash
    still leaves earlier pages saved and a retry resumes. The first exception
    is re-raised after the pool drains, rather than being swallowed.
    """
    if not indices:
        return 0, 0

    workers = max(1, min(config.PAGE_CONCURRENCY, len(indices)))
    local = threading.local()
    counters = {"processed": 0, "ocr": 0}
    lock = threading.Lock()

    def handle(index: int) -> None:
        pdf = getattr(local, "pdf", None)
        if pdf is None:
            pdf = local.pdf = fitz.open(pdf_path)
        used_ocr = _process_page(project_id, document_id, pdf, index, offset)
        with lock:
            counters["processed"] += 1
            if used_ocr:
                counters["ocr"] += 1
            done = counters["processed"]
        if done % PROGRESS_EVERY == 0:
            log.info(
                "[doc %s] stage 2/6 extract: %d/%d pages (%d OCR, x%d)",
                doc_tag,
                done,
                page_count,
                counters["ocr"],
                workers,
            )

    log.info("[doc %s] stage 2/6 extract: %d threads", doc_tag, workers)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(handle, i): i for i in indices}
        try:
            for future in as_completed(futures):
                future.result()  # re-raises inside the worker thread's page
        except Exception:
            # Stop handing out new pages; those already running finish and
            # commit, so nothing half-written is left behind.
            for pending in futures:
                pending.cancel()
            log.exception(
                "[doc %s] stage 2/6 extract FAILED (%d/%d pages saved; a retry resumes)",
                doc_tag,
                counters["processed"],
                page_count,
            )
            raise
    return counters["processed"], counters["ocr"]


def process_document(project_id: str, document_id: str, spaces_key: str) -> dict:
    started = time.monotonic()
    doc_tag = document_id[:8]
    log.info("[doc %s] processing started (project %s)", doc_tag, project_id[:8])

    # The document (or its whole project) may have been deleted while this job
    # sat in the queue. There is nothing to process and never will be, so drop
    # the job instead of raising — raising would burn all 5 retries writing
    # foreign-key violations into the log.
    if not db.document_exists(document_id):
        log.warning(
            "[doc %s] document no longer exists (deleted while queued) — discarding job",
            doc_tag,
        )
        return {"skipped": "document deleted"}

    db.set_document_status(document_id, "processing")

    with tempfile.TemporaryDirectory() as tmp:
        pdf_path = os.path.join(tmp, "original.pdf")
        log.info("[doc %s] stage 1/6 download: %s", doc_tag, spaces_key)
        try:
            storage.download_to_file(spaces_key, pdf_path)
        except Exception:
            log.exception("[doc %s] stage 1/6 download FAILED for %s", doc_tag, spaces_key)
            raise
        log.info(
            "[doc %s] stage 1/6 download done (%.1f MB)",
            doc_tag,
            os.path.getsize(pdf_path) / 1e6,
        )

        pdf = fitz.open(pdf_path)
        try:
            page_count = pdf.page_count
            db.set_document_pages(document_id, page_count)

            offset = db.combined_offset(project_id, document_id)
            already_done = db.processed_page_numbers(document_id)
            processed = skipped = ocr_count = 0
            log.info(
                "[doc %s] stage 2/6 extract: %d pages (%d already done — resuming)",
                doc_tag,
                page_count,
                len(already_done),
            )

            todo = [i for i in range(page_count) if (i + 1) not in already_done]
            skipped = page_count - len(todo)
            processed, ocr_count = _extract_pages(
                project_id, document_id, pdf_path, todo, offset, page_count, doc_tag
            )
        finally:
            pdf.close()
    log.info(
        "[doc %s] stage 2/6 extract done: %d processed, %d resumed-skip, %d OCR",
        doc_tag,
        processed,
        skipped,
        ocr_count,
    )

    # FR-4 revision handling: every page of the new revision is committed, so
    # the old revision can be retired BEFORE numbering/portions are rebuilt —
    # it leaves the combined set, retrieval (Qdrant points deleted), and the
    # page-summary pool, while its rows stay for version history. Idempotent
    # for job retries.
    revision = db.document_revision_info(document_id)
    previous_id = revision["previous_version_id"]
    if previous_id is not None:
        log.info(
            "[doc %s] stage 3/6 revisions: superseding previous revision %s (rev %d → %d)",
            doc_tag,
            previous_id[:8],
            revision["revision"] - 1,
            revision["revision"],
        )
        db.supersede_document(previous_id)
        db.delete_document_page_summaries(project_id, previous_id)
        # NOTE: the old revision's Qdrant points are deleted AFTER embedding,
        # below — vector reuse copies from them first.
    else:
        log.info("[doc %s] stage 3/6 revisions: first revision, nothing to supersede", doc_tag)

    # Numbering only. Discipline detection belongs to the scrape-region job,
    # which applies the project's title-block box (workers/src/scrape.py) —
    # the worker queues it for this document once processing finishes.
    #
    # This renumbers EVERY page in the project, so two documents of the same
    # project finishing together would interleave and corrupt the manifest.
    # Documents of different projects never contend for this lock.
    log.info("[doc %s] stage 4/6 numbering: recomputing combined page numbers", doc_tag)
    try:
        with db.project_lock(project_id):
            db.recompute_combined_numbering(project_id)
    except Exception:
        log.exception("[doc %s] stage 4/6 numbering FAILED", doc_tag)
        raise
    log.info("[doc %s] stage 4/6 numbering done", doc_tag)

    # Embed this document's new chunks (reusing the previous revision's
    # vectors for unchanged text), then refresh payloads for the rest of the
    # project (portion/discipline/combined page may have shifted).
    to_embed = db.chunks_to_embed(document_id)
    embedded_ids: list[str] = []
    if not config.EMBEDDINGS_ENABLED:
        # EMBEDDINGS_ENABLED=false: skip the provider and Qdrant entirely. Chunks keep a
        # NULL embeddingId and are picked up by a later run once re-enabled, so
        # summaries can be tested without spending the embedding rate limit.
        log.warning(
            "[doc %s] stage 5/6 embed SKIPPED (EMBEDDINGS_ENABLED=false) — "
            "%d chunks left unindexed; chat retrieval will find nothing until re-enabled",
            doc_tag,
            len(to_embed),
        )
    else:
        log.info("[doc %s] stage 5/6 embed: %d chunks to index", doc_tag, len(to_embed))
        try:
            embedded_ids = embeddings.embed_document_chunks(
                to_embed, previous_document_id=previous_id
            )
            if previous_id is not None:
                embeddings.delete_document_points(previous_id)
            if embedded_ids:
                db.set_embedding_ids(embedded_ids)
                others = [
                    c
                    for c in db.embedded_chunk_payloads(project_id)
                    if c["chunk_id"] not in set(embedded_ids)
                ]
                embeddings.refresh_payloads(others)
        except Exception:
            log.exception("[doc %s] stage 5/6 embed FAILED", doc_tag)
            raise
        log.info(
            "[doc %s] stage 5/6 embed done: %d/%d chunks in Qdrant",
            doc_tag,
            len(embedded_ids),
            len(to_embed),
        )

    db.set_document_status(document_id, "completed")
    cache.invalidate_summaries(project_id)
    result = {
        "chunks": len(to_embed),
        "embedded": len(embedded_ids),
        "pages": page_count,
        "processed": processed,
        "resumedSkip": skipped,
        "ocrPages": ocr_count,
        "supersededPrevious": previous_id is not None,
        "embeddingsEnabled": config.EMBEDDINGS_ENABLED,
    }
    log.info(
        "[doc %s] stage 6/6 finalize: completed in %.1fs — %s",
        doc_tag,
        time.monotonic() - started,
        result,
    )
    return result
