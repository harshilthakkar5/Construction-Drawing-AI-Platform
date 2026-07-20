"""Per-document processing pipeline (FR-6..FR-9).

Streams one page at a time with PyMuPDF — memory stays flat regardless of
PDF size. Each page is uploaded and committed individually, so a failure at
page N preserves pages 1..N-1 and a retried job resumes where it left off.

Stages (each logged, errors logged with the failing stage/page):
  1/6 download   — fetch the original PDF from object storage
  2/6 extract    — per-page text/PNG/thumb (+ OCR fallback) + chunking
  3/6 revisions  — supersede the replaced document (FR-4), if any
  4/6 portions   — recompute combined numbering + portion detection
  5/6 embed      — Voyage embeddings into Qdrant (reuse across revisions)
  6/6 finalize   — mark completed, invalidate caches
"""

import io
import os
import tempfile
import time

import fitz  # PyMuPDF
from PIL import Image

import cache
import chunker
import config
import db
import embeddings
import logutil
import ocr
import portions
import storage

log = logutil.get("pipeline")

# Progress heartbeat for very large PDFs: log every N pages.
PROGRESS_EVERY = 25


def _thumbnail_jpg(png: bytes) -> bytes:
    with Image.open(io.BytesIO(png)) as img:
        ratio = config.THUMB_WIDTH / img.width
        thumb = img.convert("RGB").resize(
            (config.THUMB_WIDTH, max(1, round(img.height * ratio))), Image.LANCZOS
        )
        out = io.BytesIO()
        thumb.save(out, format="JPEG", quality=70)
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
        _thumbnail_jpg(png),
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

    # Hybrid chunking: structural blocks -> token windows w/ bbox.
    page_chunks = chunker.chunk_page(page.get_text("blocks"))
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


def process_document(project_id: str, document_id: str, spaces_key: str) -> dict:
    started = time.monotonic()
    doc_tag = document_id[:8]
    log.info("[doc %s] processing started (project %s)", doc_tag, project_id[:8])
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

            for index in range(page_count):
                page_number = index + 1
                if page_number in already_done:
                    skipped += 1
                    continue
                try:
                    if _process_page(project_id, document_id, pdf, index, offset):
                        ocr_count += 1
                except Exception:
                    log.exception(
                        "[doc %s] stage 2/6 extract FAILED at page %d/%d "
                        "(pages 1..%d are saved; a retry resumes here)",
                        doc_tag,
                        page_number,
                        page_count,
                        page_number - 1,
                    )
                    raise
                processed += 1
                if processed % PROGRESS_EVERY == 0:
                    log.info(
                        "[doc %s] stage 2/6 extract: %d/%d pages (%d OCR)",
                        doc_tag,
                        page_number,
                        page_count,
                        ocr_count,
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

    log.info("[doc %s] stage 4/6 portions: recomputing numbering + detection", doc_tag)
    try:
        db.recompute_combined_numbering(project_id)
        detected = portions.detect_and_store(project_id)
        db.assign_chunk_portions(project_id)
    except Exception:
        log.exception("[doc %s] stage 4/6 portions FAILED", doc_tag)
        raise
    log.info("[doc %s] stage 4/6 portions done: %d portions", doc_tag, len(detected))

    # Embed this document's new chunks (reusing the previous revision's
    # vectors for unchanged text), then refresh payloads for the rest of the
    # project (portion/discipline/combined page may have shifted).
    to_embed = db.chunks_to_embed(document_id)
    log.info("[doc %s] stage 5/6 embed: %d chunks to index", doc_tag, len(to_embed))
    try:
        embedded_ids = embeddings.embed_document_chunks(to_embed, previous_document_id=previous_id)
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
        "portions": len(detected),
        "chunks": len(to_embed),
        "embedded": len(embedded_ids),
        "pages": page_count,
        "processed": processed,
        "resumedSkip": skipped,
        "ocrPages": ocr_count,
        "supersededPrevious": previous_id is not None,
    }
    log.info(
        "[doc %s] stage 6/6 finalize: completed in %.1fs — %s",
        doc_tag,
        time.monotonic() - started,
        result,
    )
    return result
