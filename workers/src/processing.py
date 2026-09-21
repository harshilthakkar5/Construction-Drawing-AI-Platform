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
import math
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
import vlm

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


def _process_page(
    project_id: str,
    document_id: str,
    pdf,
    index: int,
    offset: int,
    spend: "DocumentSpend | None" = None,
) -> bool:
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
        tables=tables.find_page_tables(page, document_id),
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
    description = _describe_page(page, project_id, page_number, spend)
    if description:
        # Whole-page bbox: the description is about the whole sheet, so
        # clicking its citation should land on the sheet rather than on some
        # arbitrary rectangle the description never confined itself to. Every
        # piece keeps that same rectangle — splitting the prose does not give
        # any piece of it a narrower claim on the drawing.
        rect = page.rect
        page_chunks = page_chunks + chunker.split_description(
            description,
            {"x": 0, "y": 0, "width": rect.width, "height": rect.height},
            # Stamped on every piece, so the configuration that produced this
            # description travels WITH it. Until this existed the only record
            # was a --label typed by hand off a worker log, and a wrong label
            # manufactures a measurement rather than merely lacking one.
            source_model=vlm.source_model(),
            source_settings=vlm.settings_snapshot(),
        )

    db.replace_page_chunks(document_id, page_number, page_chunks)
    log.debug("page %d done: %d chars, %d chunks", page_number, len(text), len(page_chunks))
    return used_ocr


class DocumentSpend:
    """What the vision pass cost ONE document, counted where it is spent.

    The per-page log line has always named the trade — "27 images where the
    whole-sheet pass sends 1" — and that is the wrong unit for the decision
    anyone actually makes, which is whether to queue a 400-page set. Nobody
    reads four hundred info lines and adds them up. A measured page took 235
    seconds and 152 images; the document total is the number that says what
    that becomes, and it belongs beside the chunk and page counts the job
    already reports.

    Thread-safe because pages are extracted by a pool and each one records its
    own spend. Owned per document rather than per process: `PROCESS_CONCURRENCY`
    documents run at once, and a module-level counter would silently bill one
    document for another's pages.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.crop_pages = 0
        self.sheet_pages = 0
        self.images = 0
        self.calls = 0
        self.refusals: dict[str, int] = {}

    def cropped(self, intersections: int, batch: int) -> None:
        calls = max(1, math.ceil(intersections / max(1, batch)))
        with self._lock:
            self.crop_pages += 1
            self.images += intersections
            self.calls += calls

    def whole_sheet(self) -> None:
        with self._lock:
            self.sheet_pages += 1
            self.images += 1
            self.calls += 1

    def refused_crops(self, reason: str) -> None:
        with self._lock:
            self.refusals[reason] = self.refusals.get(reason, 0) + 1

    def described_pages(self) -> int:
        return self.crop_pages + self.sheet_pages

    def summary(self) -> dict:
        """The numbers the job result carries, or {} when nothing was described.

        Empty rather than zeros: a document with the vision pass OFF did not
        spend nothing on it, it did not run it, and a row of zeros in the job
        result reads like the former.
        """
        if not self.described_pages():
            return {}
        return {
            "vlmPages": self.described_pages(),
            "vlmCropPages": self.crop_pages,
            "vlmSheetPages": self.sheet_pages,
            "vlmImages": self.images,
            "vlmCalls": self.calls,
        }

    def report(self, doc_tag: str) -> None:
        if not self.described_pages():
            return
        per_page = self.images / self.described_pages()
        log.info(
            "%s vision pass cost: %d images in %d call(s) over %d page(s) — %.1f images per "
            "page (%d cropped, %d whole-sheet). The whole-sheet pass alone would have sent %d.",
            doc_tag,
            self.images,
            self.calls,
            self.described_pages(),
            per_page,
            self.crop_pages,
            self.sheet_pages,
            self.described_pages(),
        )
        for reason, pages in sorted(self.refusals.items(), key=lambda kv: -kv[1]):
            log.info("%s   %d page(s) got no crops — %s", doc_tag, pages, reason)


def _describe_page(
    page, project_id: str, page_number: int, spend: DocumentSpend | None = None
) -> str | None:
    """The vision pass, or None when it is off, unconfigured, or failed.

    Off by default. It costs one model call per page whether or not anyone
    asks a geometry question, so it is opt-in and its worth is measured
    (benchmarks/drawing_eval.mjs) rather than assumed.

    A failure here is never a failed page. The description is an ADDITION to
    what this pipeline already produced; losing it leaves the page exactly as
    good as it was before this existed, and a raise would throw away the text,
    the chunks and the images that did succeed.

    The sheet number is not passed: `pages.sheetNumber` is written by the
    separate scrape-region job, which has not run when this does. The prompt
    reads slightly better with it, and nothing depends on it — moving this to
    its own job after the scrape is a Phase 2 question, not a correctness one.
    """
    if not config.VLM_ENABLED:
        return None
    try:
        if not vlm.available():
            log.warning("VLM_ENABLED but %s has no key — skipping", vlm.provider())
            return None
        spend = spend or DocumentSpend()
        if vlm.CROP_MODE == "intersections":
            # Phase C REPLACES the sheet pass rather than joining it: two
            # accounts of the same intersection in one corpus have nothing to
            # say which retrieval should surface. Every way the crop pass
            # declines — no grid, too many intersections, nothing readable —
            # returns None and lands here, because the answer to all of them is
            # the pass this was meant to improve on.
            # The gating rule is asked HERE rather than only inside the crop
            # pass, so the caller knows what was decided and can count it. A
            # rule nobody can count is a rule nobody can check: "how many pages
            # of this set were cropped?" had no answer short of grepping a log.
            decision = vlm.crop_decision(page)
            if decision.crop:
                described = vlm.describe_crops(page, project_id=project_id)
                if described:
                    spend.cropped(decision.intersections, vlm.CROP_BATCH)
                    return described
                spend.refused_crops("the crop reply carried nothing usable")
            else:
                spend.refused_crops(decision.reason)
        described = vlm.describe_page(vlm.render(page), project_id=project_id)
        if described:
            spend.whole_sheet()
        return described
    except Exception as exc:
        log.warning("page %d: vision pass failed: %s", page_number, exc)
        return None


def _extract_pages(
    project_id: str,
    document_id: str,
    pdf_path: str,
    indices: list[int],
    offset: int,
    page_count: int,
    doc_tag: str,
    spend: "DocumentSpend",
) -> tuple[int, int]:
    """Stage 2 across several pages at once. Returns (processed, ocr_count).

    `spend` is this DOCUMENT's vision tally, filled from the pool threads and
    read once at finalize. It is a parameter rather than a module global
    because PROCESS_CONCURRENCY documents run at once and a shared counter
    would bill one document for another's pages.

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
    # Every Document a pool thread opens, so they can all be closed at the end.
    # Windows will not delete a file that is still open, so leaking these made
    # TemporaryDirectory cleanup raise WinError 32 — which then REPLACED
    # whatever the real outcome was, failing jobs whose pages had all been
    # extracted and committed. On Linux the same leak is silent, which is why
    # it survived: an unlinked open file is legal there.
    opened: list = []

    def handle(index: int) -> None:
        pdf = getattr(local, "pdf", None)
        if pdf is None:
            pdf = local.pdf = fitz.open(pdf_path)
            with lock:
                opened.append(pdf)
        used_ocr = _process_page(project_id, document_id, pdf, index, offset, spend)
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
    try:
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
    finally:
        # After the pool has drained, so no thread is mid-page. On the failure
        # path too: the caller is about to remove the temp directory either way,
        # and a leaked handle there turns a real error into a confusing
        # PermissionError about a temp file.
        for pdf in opened:
            try:
                pdf.close()
            except Exception:  # noqa: BLE001 - closing must never mask the real error
                pass
    return counters["processed"], counters["ocr"]


def process_document(project_id: str, document_id: str, spaces_key: str) -> dict:
    """Entry point. Refuses to run if this document is already being processed.

    BullMQ hands a job to another slot when its lock lapses, and these jobs run
    for minutes — long enough that it happened in practice. The second delivery
    would otherwise re-download the PDF and re-upload every page alongside the
    first, on the link that is already the bottleneck.
    """
    with db.document_lock(document_id) as acquired:
        if not acquired:
            log.warning(
                "[doc %s] already being processed by another job — discarding this "
                "delivery rather than duplicating the work",
                document_id[:8],
            )
            return {"skipped": "already processing"}
        return _process_document(project_id, document_id, spaces_key)


def _process_document(project_id: str, document_id: str, spaces_key: str) -> dict:
    started = time.monotonic()
    doc_tag = document_id[:8]
    # This document's vision tally, filled from the page pool and read at
    # finalize. Per document rather than per process: several documents run at
    # once and a shared counter would bill one for another's pages.
    spend = DocumentSpend()
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
    # A retry re-reads the whole document, so it gets a fresh table-detection
    # budget rather than inheriting a give-up from the attempt that failed.
    tables.reset_budget(document_id)

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
                project_id, document_id, pdf_path, todo, offset, page_count, doc_tag, spend
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
        **spend.summary(),
    }
    # Before the finalize line, because the cost is a fact about the run and
    # the finalize line is a fact about the job. A page said "27 images where
    # the whole-sheet pass sends 1" four hundred times and nobody added it up.
    spend.report(f"[doc {doc_tag}]")
    log.info(
        "[doc %s] stage 6/6 finalize: completed in %.1fs — %s",
        doc_tag,
        time.monotonic() - started,
        result,
    )
    return result
