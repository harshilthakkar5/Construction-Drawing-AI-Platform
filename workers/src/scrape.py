"""Region scrape + discipline classification (the scrape-region job).

Applies the project's ONE user-drawn title-block box to every page that hasn't
been scraped at the current region version, reads the sheet number out of each
scraped string, and rebuilds the project's portions from the result.

Stages (each logged, failures logged with the failing stage/page):
  1/5 scrape   — apply the box page by page (one PDF download per document)
  2/5 classify — scraped string → sheet number → PREFIX_TO_DISCIPLINE
  3/5 group    — one portion per discipline, UPSERTed so IDs stay stable
  4/5 stale    — flag summaries whose discipline's page set changed
  5/5 finalize — refresh Qdrant payloads, invalidate caches, write counters

Same failure discipline as the extraction pipeline: results are committed per
page, so a crash at page 700 keeps pages 1..699 and a retry resumes there.
"""

from __future__ import annotations

import os
import tempfile
import time

import fitz  # PyMuPDF
import redis as redis_lib

import cache
import classify
import config
import db
import embeddings
import logutil
import region as region_mod
import storage

log = logutil.get("scrape")

PROGRESS_EVERY = 25

_redis = None


def _get_redis():
    """Shared Redis handle for the classification cache; None when unavailable
    (the scrape still runs, it just re-pays for every page)."""
    global _redis
    if _redis is None:
        try:
            _redis = redis_lib.Redis.from_url(config.REDIS_URL)
            _redis.ping()
        except Exception as exc:
            log.warning("Redis classification cache unavailable: %s", exc)
            _redis = False
    return _redis or None


def _scrape_pages(project_id: str, box: region_mod.Region, version: int, pending: list[dict]) -> dict:
    """Stage 1: apply the box to every pending page, one document at a time."""
    by_document: dict[str, list[dict]] = {}
    for page in pending:
        by_document.setdefault(page["document_id"], []).append(page)

    scraped = found = 0
    for document_id, pages in by_document.items():
        spaces_key = pages[0]["spaces_key"]
        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = os.path.join(tmp, "original.pdf")
            try:
                storage.download_to_file(spaces_key, pdf_path)
            except Exception:
                log.exception(
                    "stage 1/5 scrape: download FAILED for document %s (%s)",
                    document_id[:8],
                    spaces_key,
                )
                raise
            pdf = fitz.open(pdf_path)
            try:
                for page_row in pages:
                    index = page_row["page_number"] - 1
                    if index < 0 or index >= pdf.page_count:
                        # The stored page count and the file disagree (a failed
                        # revision upload); leave the page unscraped rather than
                        # crashing the whole project's run.
                        log.warning(
                            "stage 1/5 scrape: page %d missing from %s — skipped",
                            page_row["page_number"],
                            document_id[:8],
                        )
                        continue
                    text, method = region_mod.extract_region_text(pdf.load_page(index), box)
                    db.set_page_region_text(page_row["page_id"], text, method, version)
                    scraped += 1
                    if text:
                        found += 1
                    if scraped % PROGRESS_EVERY == 0:
                        db.set_region_status(
                            project_id, "running", scraped_pages=scraped
                        )
                        log.info(
                            "stage 1/5 scrape: %d/%d pages (%d with text)",
                            scraped,
                            len(pending),
                            found,
                        )
            finally:
                pdf.close()
    return {"scraped": scraped, "found": found}


def _classify_pages(project_id: str) -> list[dict]:
    """Stage 2+3: scraped text → discipline → portions. Returns the portions."""
    classify.set_usage_project(project_id)  # attributes Haiku sheet reads
    rows = db.pages_to_classify(project_id)
    if not rows:
        db.upsert_portions(project_id, [])
        return []

    redis_conn = _get_redis()
    resolved: list[str | None] = []
    ai_hits = manual = 0
    for row in rows:
        if row["discipline_source"] == "manual" and row["discipline"]:
            resolved.append(row["discipline"])
            manual += 1
            continue
        sheet_number, discipline, source = classify.classify_region_text(
            row["region_text"], row["filename"], redis_conn
        )
        resolved.append(discipline)
        if source == "region_ai":
            ai_hits += 1
        if (
            sheet_number != row["sheet_number"]
            or discipline != row["discipline"]
            or source != row["discipline_source"]
        ):
            db.set_page_sheet(row["page_id"], sheet_number, discipline, source)

    log.info(
        "stage 2/5 classify: %d pages (%d read by AI, %d manual, %d unresolved)",
        len(rows),
        ai_hits,
        manual,
        sum(1 for d in resolved if d is None),
    )

    # Pages with no readable sheet number inherit from their neighbours, then
    # the inherited value is persisted so retrieval and rollups can join on it.
    filled = classify.fill_unresolved(resolved)
    for row, before, after in zip(rows, resolved, filled):
        if before is None and row["discipline"] != after:
            db.set_page_sheet(row["page_id"], row["sheet_number"], after, "inherited")

    pages = [(row["combined_page"], row["region_text"]) for row in rows]
    portions = classify.group_portions(pages, filled)
    db.upsert_portions(project_id, portions)
    db.assign_chunk_portions(project_id)
    log.info(
        "stage 3/5 group: %s",
        ", ".join(
            f"{p['name']} {p['startPage']}-{p['endPage']} ({p['pageCount']}p)" for p in portions
        )
        or "no pages",
    )
    return portions


def preview(project_id: str, preview_id: str, box: dict, sample_size: int = 5) -> dict:
    """Dry run: scrape a candidate box on a handful of pages spread across the
    project so the user can check the box before paying for a full scrape.

    The result lands in Redis under region:preview:{previewId} (the API polls
    it) rather than being returned to an HTTP request — PDF work never happens
    inside a request.
    """
    rows = db.sample_pages(project_id, sample_size)
    region = region_mod.Region.from_row(box)
    results: list[dict] = []

    by_document: dict[str, list[dict]] = {}
    for row in rows:
        by_document.setdefault(row["document_id"], []).append(row)

    for document_id, pages in by_document.items():
        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = os.path.join(tmp, "original.pdf")
            try:
                storage.download_to_file(pages[0]["spaces_key"], pdf_path)
            except Exception as exc:
                log.warning("preview: could not download %s: %s", document_id[:8], exc)
                continue
            pdf = fitz.open(pdf_path)
            try:
                for row in pages:
                    index = row["page_number"] - 1
                    if index < 0 or index >= pdf.page_count:
                        continue
                    text, method = region_mod.extract_region_text(pdf.load_page(index), region)
                    results.append(
                        {
                            "combinedPageNumber": row["combined_page"],
                            "documentId": row["document_id"],
                            "filename": row["filename"],
                            "text": text,
                            "method": method,
                        }
                    )
            finally:
                pdf.close()

    results.sort(key=lambda r: r["combinedPageNumber"])
    found = sum(1 for r in results if r["text"])
    payload = {
        "rows": results,
        "foundCount": found,
        "notFoundCount": len(results) - found,
    }
    cache.store_region_preview(preview_id, payload)
    log.info(
        "preview %s: %d/%d sample pages returned text", preview_id[:8], found, len(results)
    )
    return payload


def run(project_id: str, region_version: int, document_id: str | None = None) -> dict:
    started = time.monotonic()
    tag = project_id[:8]

    # The project (or the region) may have gone while this job sat in the queue.
    if not db.project_exists(project_id):
        log.warning("project %s no longer exists — discarding scrape job", tag)
        return {"skipped": "project deleted"}
    region_row = db.get_sheet_region(project_id)
    if region_row is None:
        log.warning("project %s has no title-block region — discarding scrape job", tag)
        return {"skipped": "no region"}
    if region_row["version"] != region_version:
        # The user redrew the box; a newer job is already queued for it.
        log.info(
            "project %s: region moved to v%d (job was for v%d) — discarding",
            tag,
            region_row["version"],
            region_version,
        )
        return {"skipped": "superseded region version"}

    box = region_mod.Region.from_row(region_row)
    before = db.portion_page_ids(project_id)

    pending = db.pages_to_scrape(project_id, region_version, document_id)
    db.set_region_status(
        project_id,
        "running",
        scraped_pages=0,
        total_pages=len(pending),
        last_error=None,
    )
    log.info(
        "[project %s] stage 1/5 scrape: %d pages pending at region v%d%s",
        tag,
        len(pending),
        region_version,
        f" (document {document_id[:8]})" if document_id else "",
    )

    try:
        # Scraping is per page and parallelises freely — it only writes each
        # page's own row. Classification and grouping rewrite the project's
        # whole portion set, so two scrape jobs for the same project (one per
        # uploaded document) must not interleave there.
        scrape_result = _scrape_pages(project_id, box, region_version, pending)
        with db.project_lock(project_id):
            portions = _classify_pages(project_id)

            # A discipline that gained or lost pages has a summary that no
            # longer covers what it claims. Keep the text, flag it — the user
            # decides whether to spend tokens refreshing it. Inside the lock:
            # the comparison is only meaningful against a settled portion set.
            after = db.portion_page_ids(project_id)
            changed = [pid for pid, pages in after.items() if before.get(pid) != pages]
            stale = db.mark_portions_stale(changed)
            if stale:
                db.mark_project_summary_stale(project_id)
                log.info(
                    "[project %s] stage 4/5 stale: %d portion summaries flagged", tag, stale
                )
    except Exception as exc:
        log.exception("[project %s] region scrape FAILED", tag)
        db.set_region_status(project_id, "failed", last_error=str(exc)[:500])
        raise

    # Stage 5: portion/discipline moved on the chunks, so the Qdrant payloads
    # the chat filter matches on have to follow.
    if config.EMBEDDINGS_ENABLED:
        try:
            embeddings.refresh_payloads(db.embedded_chunk_payloads(project_id))
        except Exception:
            # Retrieval metadata is recoverable (re-run the scrape); a failure
            # here must not throw away a completed scrape.
            log.exception("[project %s] stage 5/5 payload refresh failed", tag)

    not_found = scrape_result["scraped"] - scrape_result["found"]
    db.set_region_status(
        project_id,
        "completed",
        scraped_pages=scrape_result["scraped"],
        not_found_pages=not_found,
        last_error=None,
        touch_scraped_at=True,
    )
    cache.invalidate_summaries(project_id)

    result = {
        "scrapedPages": scrape_result["scraped"],
        "pagesWithText": scrape_result["found"],
        "notFoundPages": not_found,
        "portions": len(portions),
        "staleSummaries": stale,
    }
    log.info(
        "[project %s] stage 5/5 finalize: done in %.1fs — %s",
        tag,
        time.monotonic() - started,
        result,
    )
    return result
