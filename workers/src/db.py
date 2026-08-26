"""Direct PostgreSQL access for the worker (Prisma is Node-only).

Table/column names match apps/api/prisma/schema.prisma (@@map table names,
camelCase columns). The combined-numbering rule here MUST match
apps/api/src/manifest.ts: documents ordered by ("createdAt", id), pages
1..N within each document.
"""

import hashlib
import json
import uuid
from contextlib import contextmanager

import psycopg

import config
import logutil
from hashing import text_hash

log = logutil.get("db")

# Every helper below borrows a connection for one statement, and the scrape
# loop commits per page — so at concurrency N this opened (and TLS-negotiated)
# a fresh connection thousands of times per document. Pool them instead: the
# call sites are unchanged, but the connections are reused and their number is
# bounded, which is what keeps N parallel jobs from exhausting Postgres
# max_connections.
_pool = None
_pool_unavailable = False


def _get_pool():
    global _pool, _pool_unavailable
    if _pool is not None or _pool_unavailable:
        return _pool
    try:
        from psycopg_pool import ConnectionPool

        _pool = ConnectionPool(
            config.DATABASE_URL,
            min_size=1,
            max_size=config.DB_POOL_SIZE,
            timeout=30,
            open=True,
        )
        log.info("Postgres connection pool ready (max %d)", config.DB_POOL_SIZE)
    except Exception as exc:
        # psycopg_pool is an extra; without it the worker still runs, just with
        # a connection per statement as before.
        log.warning("connection pool unavailable, using one connection per call: %s", exc)
        _pool_unavailable = True
    return _pool


@contextmanager
def connect():
    pool = _get_pool()
    if pool is None:
        with psycopg.connect(config.DATABASE_URL) as conn:
            yield conn
        return
    with pool.connection() as conn:
        yield conn


def _lock_key(project_id: str) -> int:
    """Stable signed 64-bit key for a project's advisory lock. Derived from the
    id rather than Postgres' hashtext() so the value is identical across
    processes and testable without a database."""
    digest = hashlib.sha256(project_id.encode()).digest()[:8]
    return int.from_bytes(digest, "big", signed=True)


@contextmanager
def document_lock(document_id: str):
    """Refuse to process one document twice at the same time.

    Yields True when this caller holds the lock and False when someone else
    already does — `pg_try_advisory_lock`, not `pg_advisory_lock`, because the
    right response to "already running" is to walk away, not to queue up and do
    the same work again afterwards.

    BullMQ re-delivers a job whose lock lapsed, and a document that takes half
    an hour gives it plenty of opportunity: a real run had the same 148-page
    document processed by two executions at once, interleaving their page
    counters and doubling the uploads on the slowest link in the system.
    WORKER_LOCK_DURATION_MS makes that rare; this makes it harmless.

    Postgres frees the lock if the worker dies, so a genuinely crashed job is
    still retried — which is the property a lock stored anywhere else would
    have to reimplement.
    """
    key = _lock_key(f"document:{document_id}")
    with connect() as conn:
        acquired = conn.execute(
            "SELECT pg_try_advisory_lock(%s)", (key,)
        ).fetchone()[0]
        try:
            yield bool(acquired)
        finally:
            if acquired:
                try:
                    conn.execute("SELECT pg_advisory_unlock(%s)", (key,))
                except Exception:
                    pass


@contextmanager
def project_lock(project_id: str):
    """Serialize the project-wide steps of otherwise-parallel jobs.

    Most of the pipeline is per-document and parallelises cleanly, but three
    steps rewrite state that belongs to the WHOLE project — combined page
    numbering, the portion rebuild, and chunk→portion assignment. Two documents
    of the same project finishing at the same moment would interleave those and
    corrupt the manifest, which is a correctness-critical path.

    A Postgres advisory lock (rather than an in-process lock) because the
    contenders are separate worker processes and replicas. Held on its own
    pooled connection for the length of the critical section; Postgres releases
    it automatically if the worker dies, so a crash cannot wedge a project.
    """
    key = _lock_key(project_id)
    with connect() as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (key,))
        try:
            yield
        finally:
            try:
                conn.execute("SELECT pg_advisory_unlock(%s)", (key,))
            except Exception:
                # The connection died; Postgres drops the lock with the session.
                log.warning("could not release the lock for project %s", project_id[:8])


def project_exists(project_id: str) -> bool:
    """False when the project was deleted while a job was queued."""
    with connect() as conn:
        return conn.execute(
            "SELECT 1 FROM projects WHERE id = %s", (project_id,)
        ).fetchone() is not None


def project_roles(project_id: str) -> list[str]:
    """Disciplines the project was created FOR. They steer what a summary
    emphasises (summarize._system_blocks); they never filter what is
    extracted, classified or indexed."""
    with connect() as conn:
        row = conn.execute(
            'SELECT roles FROM projects WHERE id = %s', (project_id,)
        ).fetchone()
    return list(row[0]) if row and row[0] else []


def document_exists(document_id: str) -> bool:
    """False when the document (or its project) was deleted while the job was
    queued — the worker discards such jobs instead of retrying into
    foreign-key violations."""
    with connect() as conn:
        return conn.execute(
            "SELECT 1 FROM documents WHERE id = %s", (document_id,)
        ).fetchone() is not None


def set_document_status(document_id: str, status: str) -> None:
    with connect() as conn:
        conn.execute(
            'UPDATE documents SET status = %s::"DocumentStatus" WHERE id = %s',
            (status, document_id),
        )


def set_document_pages(document_id: str, pages: int) -> None:
    with connect() as conn:
        conn.execute("UPDATE documents SET pages = %s WHERE id = %s", (pages, document_id))


def combined_offset(project_id: str, document_id: str) -> int:
    """Pages in documents ordered before this one (manifest ordering rule).
    Superseded revisions (FR-4) are excluded — they left the combined set."""
    with connect() as conn:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(d.pages), 0)
            FROM documents d, documents me
            WHERE me.id = %s
              AND d."projectId" = %s
              AND d.id <> me.id
              AND d."supersededAt" IS NULL
              AND (d."createdAt" < me."createdAt"
                   OR (d."createdAt" = me."createdAt" AND d.id < me.id))
            """,
            (document_id, project_id),
        ).fetchone()
        return int(row[0]) if row else 0


# --- Document revisions (FR-4) ---


def document_revision_info(document_id: str) -> dict:
    """previousVersionId + revision for the revision-handling steps."""
    with connect() as conn:
        row = conn.execute(
            'SELECT "previousVersionId", revision FROM documents WHERE id = %s',
            (document_id,),
        ).fetchone()
        if row is None:
            raise RuntimeError(f"document {document_id} not found")
        return {"previous_version_id": row[0], "revision": row[1]}


def supersede_document(document_id: str) -> None:
    """Mark an old revision replaced: it disappears from the manifest,
    combined numbering, and (via Qdrant point deletion) retrieval, but the
    rows stay for audit/version history. Idempotent."""
    with connect() as conn:
        conn.execute(
            'UPDATE documents SET "supersededAt" = NOW() WHERE id = %s AND "supersededAt" IS NULL',
            (document_id,),
        )


def delete_document_page_summaries(project_id: str, document_id: str) -> None:
    """Drop a superseded revision's page summaries so rollups only see the
    latest revision (higher levels are recomputed every run anyway)."""
    with connect() as conn:
        conn.execute(
            """
            DELETE FROM summaries
            WHERE "projectId" = %s AND level = 'page'
              AND summary->>'documentId' = %s
            """,
            (project_id, document_id),
        )


def matching_embedded_chunks(old_document_id: str, hashes: list[str]) -> dict[str, str]:
    """textHash -> embedded chunk id in the previous revision. Lets the new
    revision reuse Qdrant vectors for unchanged chunk text (no Voyage call)."""
    if not hashes:
        return {}
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT ON (c."textHash") c."textHash", c.id
            FROM chunks c
            JOIN pages p ON c."pageId" = p.id
            WHERE p."documentId" = %s
              AND c."embeddingId" IS NOT NULL
              AND c."textHash" = ANY(%s)
            ORDER BY c."textHash", c.id
            """,
            (old_document_id, hashes),
        ).fetchall()
        return {r[0]: r[1] for r in rows}


def processed_page_numbers(document_id: str) -> set[int]:
    """Pages already fully processed — lets a retried job resume (partial results preserved)."""
    with connect() as conn:
        rows = conn.execute(
            'SELECT "pageNumber" FROM pages WHERE "documentId" = %s AND "imageUrl" IS NOT NULL',
            (document_id,),
        ).fetchall()
        return {r[0] for r in rows}


def upsert_page(
    document_id: str,
    page_number: int,
    combined_page_number: int,
    image_key: str,
    text: str,
    pdf_width: float | None = None,
    pdf_height: float | None = None,
) -> None:
    """Committed per page so a failure at page N preserves pages 1..N-1.
    pdf_width/pdf_height are the PDF page size in points — the coordinate
    space of chunk bboxes, used by the viewer for FR-19 highlights."""
    text = text.replace("\x00", "")  # PostgreSQL rejects NUL bytes in text
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO pages (id, "documentId", "pageNumber", "combinedPageNumber", "imageUrl",
                               text, "pdfWidth", "pdfHeight")
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT ("documentId", "pageNumber")
            DO UPDATE SET "combinedPageNumber" = EXCLUDED."combinedPageNumber",
                          "imageUrl" = EXCLUDED."imageUrl",
                          text = EXCLUDED.text,
                          "pdfWidth" = EXCLUDED."pdfWidth",
                          "pdfHeight" = EXCLUDED."pdfHeight"
            """,
            (
                str(uuid.uuid4()),
                document_id,
                page_number,
                combined_page_number,
                image_key,
                text,
                pdf_width,
                pdf_height,
            ),
        )


def replace_page_chunks(document_id: str, page_number: int, chunks: list) -> None:
    """Replace a page's chunks in one transaction (idempotent reprocessing).
    chunks: chunker.Chunk objects."""
    with connect() as conn:
        page_row = conn.execute(
            'SELECT id FROM pages WHERE "documentId" = %s AND "pageNumber" = %s',
            (document_id, page_number),
        ).fetchone()
        if page_row is None:
            raise RuntimeError(f"page row missing for {document_id} p{page_number}")
        page_id = page_row[0]
        conn.execute('DELETE FROM chunks WHERE "pageId" = %s', (page_id,))
        for chunk in chunks:
            conn.execute(
                """
                INSERT INTO chunks (id, "pageId", text, bbox, "tokenCount", "textHash")
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    str(uuid.uuid4()),
                    page_id,
                    chunk.text,
                    json.dumps(chunk.bbox),
                    chunk.token_count,
                    text_hash(chunk.text),
                ),
            )


def set_page_disciplines(project_id: str, mapping: list[tuple[int, str]]) -> None:
    """Persist each page's discipline (by combined page number). Written by the
    detection pass before chunks are assigned to portions."""
    with connect() as conn:
        for combined, discipline in mapping:
            conn.execute(
                """
                UPDATE pages SET discipline = %s
                FROM documents
                WHERE pages."documentId" = documents.id
                  AND documents."projectId" = %s
                  AND documents."supersededAt" IS NULL
                  AND pages."combinedPageNumber" = %s
                """,
                (discipline, project_id, combined),
            )


# --- Title-block region (region-based discipline detection) ---


def get_sheet_region(project_id: str) -> dict | None:
    """The project's active region row, or None when the user hasn't drawn one."""
    with connect() as conn:
        row = conn.execute(
            """
            SELECT id, "relX", "relY", "relW", "relH", version, "scrapeStatus"
            FROM sheet_regions WHERE "projectId" = %s
            """,
            (project_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "id": row[0],
            "relX": row[1],
            "relY": row[2],
            "relW": row[3],
            "relH": row[4],
            "version": row[5],
            "scrapeStatus": row[6],
        }


def set_region_status(
    project_id: str,
    status: str,
    scraped_pages: int | None = None,
    total_pages: int | None = None,
    not_found_pages: int | None = None,
    last_error: str | None = None,
    touch_scraped_at: bool = False,
) -> None:
    """Progress/status writes for the scrape job. Only the fields passed are
    updated, so a progress heartbeat doesn't clobber the counters."""
    sets = ['"scrapeStatus" = %s::"RegionScrapeStatus"']
    params: list = [status]
    if scraped_pages is not None:
        sets.append('"scrapedPages" = %s')
        params.append(scraped_pages)
    if total_pages is not None:
        sets.append('"totalPages" = %s')
        params.append(total_pages)
    if not_found_pages is not None:
        sets.append('"notFoundPages" = %s')
        params.append(not_found_pages)
    sets.append('"lastError" = %s')
    params.append(last_error)
    if touch_scraped_at:
        sets.append('"lastScrapedAt" = NOW()')
    params.append(project_id)
    with connect() as conn:
        conn.execute(
            f'UPDATE sheet_regions SET {", ".join(sets)} WHERE "projectId" = %s',
            tuple(params),
        )


def pages_to_scrape(
    project_id: str, region_version: int, document_id: str | None = None
) -> list[dict]:
    """Pages whose stored scrape predates the current region version — exactly
    the work a (re-)scrape has to do. Grouped by document by the caller so each
    PDF is downloaded once. A retried job naturally skips the pages it already
    committed."""
    sql = """
        SELECT p.id, p."documentId", p."pageNumber", d."spacesKey", d.filename
        FROM pages p
        JOIN documents d ON p."documentId" = d.id
        WHERE d."projectId" = %s
          AND d."supersededAt" IS NULL
          AND (p."regionVersion" IS NULL OR p."regionVersion" <> %s)
    """
    params: list = [project_id, region_version]
    if document_id is not None:
        sql += ' AND d.id = %s'
        params.append(document_id)
    sql += ' ORDER BY d."createdAt", d.id, p."pageNumber"'
    with connect() as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
        return [
            {
                "page_id": r[0],
                "document_id": r[1],
                "page_number": r[2],
                "spaces_key": r[3],
                "filename": r[4],
            }
            for r in rows
        ]


def sample_pages(project_id: str, count: int) -> list[dict]:
    """`count` live pages spread evenly across the project — the sample a
    region preview runs against. Spread, not the first N, so the box is checked
    against more than one document's layout."""
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT p.id, p."documentId", p."pageNumber", p."combinedPageNumber",
                   d."spacesKey", d.filename,
                   ROW_NUMBER() OVER (ORDER BY p."combinedPageNumber") AS rn,
                   COUNT(*) OVER () AS total
            FROM pages p
            JOIN documents d ON p."documentId" = d.id
            WHERE d."projectId" = %s AND d."supersededAt" IS NULL
            ORDER BY p."combinedPageNumber"
            """,
            (project_id,),
        ).fetchall()
    if not rows:
        return []
    total = len(rows)
    step = max(1, total // max(1, count))
    picked = rows[::step][:count]
    return [
        {
            "page_id": r[0],
            "document_id": r[1],
            "page_number": r[2],
            "combined_page": r[3],
            "spaces_key": r[4],
            "filename": r[5],
        }
        for r in picked
    ]


def set_page_region_text(page_id: str, text: str, method: str, version: int) -> None:
    """One page's scrape result. Committed per page so a crash at page 700
    keeps pages 1..699 (same rule as the extraction pipeline)."""
    with connect() as conn:
        conn.execute(
            """
            UPDATE pages
            SET "sheetRegionText" = %s, "regionMethod" = %s, "regionVersion" = %s
            WHERE id = %s
            """,
            ((text or "").replace("\x00", ""), method, version, page_id),
        )


def pages_to_classify(project_id: str) -> list[dict]:
    """Every live page with its scraped region text, combined order. Pages
    whose discipline was set by hand (disciplineSource = 'manual') are returned
    too, so they keep their slot in the ordering, but callers must not
    overwrite them."""
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT p.id, p."combinedPageNumber", p."sheetRegionText", d.filename,
                   p.discipline, p."disciplineSource", p."sheetNumber"
            FROM pages p
            JOIN documents d ON p."documentId" = d.id
            WHERE d."projectId" = %s AND d."supersededAt" IS NULL
            ORDER BY p."combinedPageNumber"
            """,
            (project_id,),
        ).fetchall()
        return [
            {
                "page_id": r[0],
                "combined_page": r[1],
                "region_text": r[2],
                "filename": r[3],
                "discipline": r[4],
                "discipline_source": r[5],
                "sheet_number": r[6],
            }
            for r in rows
        ]


def set_page_sheet(
    page_id: str, sheet_number: str | None, discipline: str | None, source: str | None
) -> None:
    with connect() as conn:
        conn.execute(
            """
            UPDATE pages
            SET "sheetNumber" = %s, discipline = %s, "disciplineSource" = %s
            WHERE id = %s
            """,
            (sheet_number, discipline, source, page_id),
        )


def portion_page_ids(project_id: str) -> dict[str, set[str]]:
    """portionId -> the page ids currently carrying its discipline. Compared
    before/after a re-scrape to decide which summaries went stale."""
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT po.id, p.id
            FROM portions po
            JOIN documents d ON d."projectId" = po."projectId" AND d."supersededAt" IS NULL
            JOIN pages p ON p."documentId" = d.id AND p.discipline = po.discipline
            WHERE po."projectId" = %s
            """,
            (project_id,),
        ).fetchall()
    sets: dict[str, set[str]] = {}
    for portion_id, page_id in rows:
        sets.setdefault(portion_id, set()).add(page_id)
    return sets


def mark_portions_stale(portion_ids: list[str]) -> int:
    """A summary whose discipline gained or lost pages is stale, not wrong —
    keep the text (the user paid for it) and let them decide to regenerate."""
    if not portion_ids:
        return 0
    with connect() as conn:
        cur = conn.execute(
            """
            UPDATE portions SET "summaryStatus" = 'stale'
            WHERE id = ANY(%s) AND "summaryStatus" = 'ready'
            """,
            (portion_ids,),
        )
        return cur.rowcount or 0


def set_portion_summary_status(
    portion_id: str,
    status: str,
    error: str | None = None,
    completed: bool = False,
) -> None:
    sets = ['"summaryStatus" = %s::"PortionSummaryStatus"', '"summaryError" = %s']
    params: list = [status, error]
    if completed:
        sets.append('"summaryCompletedAt" = NOW()')
    params.append(portion_id)
    with connect() as conn:
        conn.execute(
            f'UPDATE portions SET {", ".join(sets)} WHERE id = %s', tuple(params)
        )


def mark_project_summary_stale(project_id: str) -> None:
    """The project rollup sits above the portions; flag it in its own JSON
    rather than adding a column for a single boolean."""
    with connect() as conn:
        conn.execute(
            """
            UPDATE summaries
            SET summary = jsonb_set(summary::jsonb, '{stale}', 'true'::jsonb, true)
            WHERE "projectId" = %s AND level = 'project'
            """,
            (project_id,),
        )


def assign_chunk_portions(project_id: str) -> None:
    """Point every chunk at its discipline's portion. Chunks are grouped by
    the page's stored discipline (one portion per discipline), so a discipline
    whose pages are non-contiguous still collapses to a single portion. Run
    after portions are rebuilt and page disciplines are stored."""
    with connect() as conn:
        conn.execute(
            """
            UPDATE chunks
            SET "portionId" = portions.id
            FROM pages, documents, portions
            WHERE chunks."pageId" = pages.id
              AND pages."documentId" = documents.id
              AND documents."projectId" = %s
              AND portions."projectId" = %s
              AND pages.discipline IS NOT NULL
              AND pages.discipline = portions.discipline
            """,
            (project_id, project_id),
        )


def chunks_to_embed(document_id: str) -> list[dict]:
    """Chunks without a Qdrant point yet, with the metadata the payload needs."""
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT c.id, c.text, p."pageNumber", p."combinedPageNumber",
                   c."portionId", po.discipline, d."projectId", d.id, c."textHash"
            FROM chunks c
            JOIN pages p ON c."pageId" = p.id
            JOIN documents d ON p."documentId" = d.id
            LEFT JOIN portions po ON c."portionId" = po.id
            WHERE d.id = %s AND c."embeddingId" IS NULL
            ORDER BY p."combinedPageNumber"
            """,
            (document_id,),
        ).fetchall()
        return [
            {
                "chunk_id": r[0],
                "text": r[1],
                "page_number": r[2],
                "combined_page": r[3],
                "portion_id": r[4],
                "discipline": r[5],
                "project_id": r[6],
                "document_id": r[7],
                "text_hash": r[8],
            }
            for r in rows
        ]


def set_embedding_ids(chunk_ids: list[str]) -> None:
    """Point ID == chunk ID; a non-null embeddingId marks the chunk as embedded."""
    with connect() as conn:
        conn.execute(
            'UPDATE chunks SET "embeddingId" = id WHERE id = ANY(%s)',
            (chunk_ids,),
        )


def embedded_chunk_payloads(project_id: str) -> list[dict]:
    """Current payload fields for every embedded chunk in the project — used to
    refresh Qdrant after portion rebuilds shift portion/discipline/page."""
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT c.id, p."pageNumber", p."combinedPageNumber", c."portionId",
                   po.discipline, d."projectId", d.id
            FROM chunks c
            JOIN pages p ON c."pageId" = p.id
            JOIN documents d ON p."documentId" = d.id
            LEFT JOIN portions po ON c."portionId" = po.id
            WHERE d."projectId" = %s AND c."embeddingId" IS NOT NULL
              AND d."supersededAt" IS NULL
            """,
            (project_id,),
        ).fetchall()
        return [
            {
                "chunk_id": r[0],
                "page_number": r[1],
                "combined_page": r[2],
                "portion_id": r[3],
                "discipline": r[4],
                "project_id": r[5],
                "document_id": r[6],
            }
            for r in rows
        ]


# --- Summaries (FR-10..13) ---


def pages_with_chunks(project_id: str) -> list[dict]:
    """Every page of the project with its chunks, combined order."""
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT p."documentId", p."pageNumber", p."combinedPageNumber",
                   c.id, c.text, c."portionId"
            FROM pages p
            JOIN documents d ON p."documentId" = d.id
            LEFT JOIN chunks c ON c."pageId" = p.id
            WHERE d."projectId" = %s AND d."supersededAt" IS NULL
            ORDER BY p."combinedPageNumber", c.id
            """,
            (project_id,),
        ).fetchall()
    pages: dict[tuple, dict] = {}
    for doc_id, page_no, combined, chunk_id, chunk_text, portion_id in rows:
        key = (doc_id, page_no)
        page = pages.setdefault(
            key,
            {
                "document_id": doc_id,
                "page_number": page_no,
                "combined_page": combined,
                "portion_id": None,
                "chunks": [],
            },
        )
        if chunk_id is not None:
            page["chunks"].append({"id": chunk_id, "text": chunk_text})
            page["portion_id"] = portion_id or page["portion_id"]
    return sorted(pages.values(), key=lambda p: p["combined_page"])


def chunk_page_map(project_id: str) -> dict[str, int]:
    """chunk id -> combined page (for deriving jump targets from citations)."""
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT c.id, p."combinedPageNumber"
            FROM chunks c
            JOIN pages p ON c."pageId" = p.id
            JOIN documents d ON p."documentId" = d.id
            WHERE d."projectId" = %s AND d."supersededAt" IS NULL
            """,
            (project_id,),
        ).fetchall()
        return {r[0]: r[1] for r in rows}


def existing_page_summaries(project_id: str) -> dict[tuple[str, int], list[str]]:
    """(documentId, pageNumber) -> cited chunk ids, for the incremental reuse
    check. Reprocessing a page recreates its chunks with new IDs, so a summary
    whose sources no longer exist must be regenerated rather than reused."""
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT summary->>'documentId', (summary->>'pageNumber')::int, sources
            FROM summaries
            WHERE "projectId" = %s AND level = 'page'
            """,
            (project_id,),
        ).fetchall()
        return {(r[0], r[1]): (r[2] or []) for r in rows}


def page_summaries(project_id: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT summary FROM summaries
            WHERE "projectId" = %s AND level = 'page'
            ORDER BY (summary->>'combinedPage')::int
            """,
            (project_id,),
        ).fetchall()
        return [r[0] for r in rows]


def delete_page_summary(project_id: str, document_id: str, page_number: int) -> None:
    """Drop one page's summary before rewriting it (summaries are insert-only,
    so a stale row must go first or the page would have two)."""
    with connect() as conn:
        conn.execute(
            """
            DELETE FROM summaries
            WHERE "projectId" = %s AND level = 'page'
              AND summary->>'documentId' = %s
              AND (summary->>'pageNumber')::int = %s
            """,
            (project_id, document_id, page_number),
        )


def page_index(project_id: str) -> dict[tuple[str, int], dict]:
    """(documentId, pageNumber) -> {combined, discipline} from the LIVE pages
    table. Page summaries store the combined page number they were written
    with, which goes stale as later uploads shift the numbering — rollups
    resolve the current values through this index instead."""
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT p."documentId", p."pageNumber", p."combinedPageNumber", p.discipline
            FROM pages p
            JOIN documents d ON p."documentId" = d.id
            WHERE d."projectId" = %s AND d."supersededAt" IS NULL
            """,
            (project_id,),
        ).fetchall()
        return {(r[0], r[1]): {"combined": r[2], "discipline": r[3]} for r in rows}


def insert_summary(
    project_id: str,
    portion_id: str | None,
    level: str,
    summary: dict,
    sources: list[str],
) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO summaries (id, "projectId", "portionId", level, summary, sources)
            VALUES (%s, %s, %s, %s::"SummaryLevel", %s, %s)
            """,
            (
                str(uuid.uuid4()),
                project_id,
                portion_id,
                level,
                json.dumps(summary),
                json.dumps(sources),
            ),
        )


def delete_summaries(project_id: str, levels: list[str]) -> None:
    with connect() as conn:
        conn.execute(
            'DELETE FROM summaries WHERE "projectId" = %s AND level = ANY(%s::"SummaryLevel"[])',
            (project_id, levels),
        )


def delete_portion_summaries(portion_id: str) -> None:
    """Clear ONE portion's rollups before rewriting them. Scoped to the portion
    so regenerating Structural never touches Electrical's summary — the whole
    point of per-discipline, user-approved generation."""
    with connect() as conn:
        conn.execute(
            """
            DELETE FROM summaries
            WHERE "portionId" = %s AND level IN ('section', 'portion')
            """,
            (portion_id,),
        )


def portion_summary_rows(portion_id: str) -> list[dict]:
    """A portion's own rollup(s), portion level first — the input the project
    rollup is built from."""
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT summary FROM summaries
            WHERE "portionId" = %s AND level = 'portion'
            """,
            (portion_id,),
        ).fetchall()
        return [r[0] for r in rows]


def project_portions(project_id: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT id, name, discipline, "startPage", "endPage", "summaryStatus"
            FROM portions WHERE "projectId" = %s ORDER BY "startPage"
            """,
            (project_id,),
        ).fetchall()
        return [
            {
                "id": r[0],
                "name": r[1],
                "discipline": r[2],
                "start_page": r[3],
                "end_page": r[4],
                "summary_status": r[5],
            }
            for r in rows
        ]


def set_portion_summary_text(portion_id: str, text: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE portions SET summary = %s WHERE id = %s", (text, portion_id))


def pages_for_classification(project_id: str) -> list[tuple[int, str | None, str, str | None]]:
    """(combinedPageNumber, text, filename, storedDiscipline) for every page in
    the project, combined order. The stored discipline lets the detection pass
    skip pages that were already classified — a new upload only pays for its
    own pages. The filename usually carries the sheet number, the most reliable
    discipline signal."""
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT p."combinedPageNumber", p.text, d.filename, p.discipline
            FROM pages p
            JOIN documents d ON p."documentId" = d.id
            WHERE d."projectId" = %s AND d."supersededAt" IS NULL
            ORDER BY p."combinedPageNumber"
            """,
            (project_id,),
        ).fetchall()
        return [(r[0], r[1], r[2], r[3]) for r in rows]


def upsert_portions(project_id: str, portions: list[dict]) -> dict[str, str]:
    """Rebuild the project's portions WITHOUT losing their identity.

    Keyed on (projectId, discipline): existing disciplines are updated in
    place, new ones inserted, and only disciplines that no longer have a single
    page are deleted. Portion IDs therefore survive a re-categorization — which
    is what keeps user-requested summaries (Summary.portionId, Cascade) and
    chunk links alive across a region edit.

    Returns discipline -> portion id.
    """
    if not portions:
        with connect() as conn:
            conn.execute('DELETE FROM portions WHERE "projectId" = %s', (project_id,))
        return {}

    ids: dict[str, str] = {}
    disciplines = [p["discipline"] for p in portions]
    with connect() as conn:
        for p in portions:
            row = conn.execute(
                """
                INSERT INTO portions (id, "projectId", name, discipline, "startPage",
                                      "endPage", "pageCount")
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT ("projectId", discipline) DO UPDATE
                SET name = EXCLUDED.name,
                    "startPage" = EXCLUDED."startPage",
                    "endPage" = EXCLUDED."endPage",
                    "pageCount" = EXCLUDED."pageCount"
                RETURNING id
                """,
                (
                    str(uuid.uuid4()),
                    project_id,
                    p["name"],
                    p["discipline"],
                    p["startPage"],
                    p["endPage"],
                    p.get("pageCount", 0),
                ),
            ).fetchone()
            ids[p["discipline"]] = row[0]
        # Disciplines that vanished from the project take their summaries with
        # them (Summary.portionId cascades) — there is nothing left to describe.
        # `discipline IS NULL` catches legacy rows from before the discipline
        # column existed — they can never match a page, so they are dead too.
        conn.execute(
            """
            DELETE FROM portions
            WHERE "projectId" = %s
              AND (discipline IS NULL OR NOT (discipline = ANY(%s)))
            """,
            (project_id, disciplines),
        )
    return ids


def recompute_combined_numbering(project_id: str) -> None:
    """Renumber every page in the project (run after a document completes)."""
    with connect() as conn:
        conn.execute(
            """
            UPDATE pages
            SET "combinedPageNumber" = sub.combined
            FROM (
                SELECT p.id,
                       p."pageNumber" + (
                           SELECT COALESCE(SUM(d2.pages), 0)
                           FROM documents d2
                           WHERE d2."projectId" = d."projectId"
                             AND d2.id <> d.id
                             AND d2."supersededAt" IS NULL
                             AND (d2."createdAt" < d."createdAt"
                                  OR (d2."createdAt" = d."createdAt" AND d2.id < d.id))
                       ) AS combined
                FROM pages p
                JOIN documents d ON p."documentId" = d.id
                WHERE d."projectId" = %s AND d."supersededAt" IS NULL
            ) sub
            WHERE pages.id = sub.id
            """,
            (project_id,),
        )
