"""Per-document processing pipeline (FR-6..FR-9).

Streams one page at a time with PyMuPDF — memory stays flat regardless of
PDF size. Each page is uploaded and committed individually, so a failure at
page N preserves pages 1..N-1 and a retried job resumes where it left off.
"""

import io
import os
import tempfile

import fitz  # PyMuPDF
from PIL import Image

import chunker
import config
import db
import embeddings
import ocr
import portions
import storage


def _thumbnail_jpg(png: bytes) -> bytes:
    with Image.open(io.BytesIO(png)) as img:
        ratio = config.THUMB_WIDTH / img.width
        thumb = img.convert("RGB").resize(
            (config.THUMB_WIDTH, max(1, round(img.height * ratio))), Image.LANCZOS
        )
        out = io.BytesIO()
        thumb.save(out, format="JPEG", quality=70)
        return out.getvalue()


def process_document(project_id: str, document_id: str, spaces_key: str) -> dict:
    db.set_document_status(document_id, "processing")

    with tempfile.TemporaryDirectory() as tmp:
        pdf_path = os.path.join(tmp, "original.pdf")
        storage.download_to_file(spaces_key, pdf_path)

        pdf = fitz.open(pdf_path)
        try:
            page_count = pdf.page_count
            db.set_document_pages(document_id, page_count)

            offset = db.combined_offset(project_id, document_id)
            already_done = db.processed_page_numbers(document_id)
            processed = skipped = ocr_count = 0

            for index in range(page_count):
                page_number = index + 1
                if page_number in already_done:
                    skipped += 1
                    continue

                page = pdf.load_page(index)
                text = page.get_text("text").strip()

                zoom = fitz.Matrix(config.PAGE_RENDER_ZOOM, config.PAGE_RENDER_ZOOM)
                png = page.get_pixmap(matrix=zoom).tobytes("png")

                if not text:  # FR-7: OCR only when the page has no text layer
                    text = ocr.ocr_png_bytes(png)
                    ocr_count += 1

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
                )

                # Hybrid chunking: structural blocks -> token windows w/ bbox.
                page_chunks = chunker.chunk_page(page.get_text("blocks"))
                if not page_chunks and text:
                    # OCR-only page: no positioned blocks, so one chunk spans
                    # the whole page rect (coarse but truthful highlight).
                    rect = page.rect
                    page_chunks = [
                        chunker.Chunk(
                            text=text,
                            bbox={"x": 0, "y": 0, "width": rect.width, "height": rect.height},
                            token_count=chunker.estimate_tokens(text),
                        )
                    ]
                db.replace_page_chunks(document_id, page_number, page_chunks)
                processed += 1
        finally:
            pdf.close()

    db.recompute_combined_numbering(project_id)
    detected = portions.detect_and_store(project_id)
    db.assign_chunk_portions(project_id)

    # Embed this document's new chunks, then refresh payloads for the rest of
    # the project (portion/discipline/combined page may have shifted).
    to_embed = db.chunks_to_embed(document_id)
    embedded_ids = embeddings.embed_document_chunks(to_embed)
    if embedded_ids:
        db.set_embedding_ids(embedded_ids)
        others = [
            c
            for c in db.embedded_chunk_payloads(project_id)
            if c["chunk_id"] not in set(embedded_ids)
        ]
        embeddings.refresh_payloads(others)

    db.set_document_status(document_id, "completed")
    return {
        "portions": len(detected),
        "chunks": len(to_embed),
        "embedded": len(embedded_ids),
        "pages": page_count,
        "processed": processed,
        "resumedSkip": skipped,
        "ocrPages": ocr_count,
    }
