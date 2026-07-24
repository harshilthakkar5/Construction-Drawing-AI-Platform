"""Direct PostgreSQL access for the worker (Prisma is Node-only).

Table/column names match apps/api/prisma/schema.prisma (@@map table names,
camelCase columns). The combined-numbering rule here MUST match
apps/api/src/manifest.ts: documents ordered by ("createdAt", id), pages
1..N within each document.
"""

import json
import uuid
from contextlib import contextmanager

import psycopg

import config
from hashing import text_hash


@contextmanager
def connect():
    with psycopg.connect(config.DATABASE_URL) as conn:
        yield conn


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


def page_disciplines(project_id: str) -> dict[int, str | None]:
    """combined page number -> discipline (for grouping summaries)."""
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT p."combinedPageNumber", p.discipline
            FROM pages p
            JOIN documents d ON p."documentId" = d.id
            WHERE d."projectId" = %s AND d."supersededAt" IS NULL
            """,
            (project_id,),
        ).fetchall()
        return {r[0]: r[1] for r in rows}


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


def existing_page_summary_keys(project_id: str) -> set[tuple[str, int]]:
    """(documentId, pageNumber) pairs that already have a page summary —
    the incremental reuse check for the expensive bulk level."""
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT summary->>'documentId', (summary->>'pageNumber')::int
            FROM summaries
            WHERE "projectId" = %s AND level = 'page'
            """,
            (project_id,),
        ).fetchall()
        return {(r[0], r[1]) for r in rows}


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


def project_portions(project_id: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT id, name, discipline, "startPage", "endPage"
            FROM portions WHERE "projectId" = %s ORDER BY "startPage"
            """,
            (project_id,),
        ).fetchall()
        return [
            {"id": r[0], "name": r[1], "discipline": r[2], "start_page": r[3], "end_page": r[4]}
            for r in rows
        ]


def set_portion_summary_text(portion_id: str, text: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE portions SET summary = %s WHERE id = %s", (text, portion_id))


def pages_for_classification(project_id: str) -> list[tuple[int, str | None]]:
    """(combinedPageNumber, text) for every page in the project, combined order."""
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT p."combinedPageNumber", p.text
            FROM pages p
            JOIN documents d ON p."documentId" = d.id
            WHERE d."projectId" = %s AND d."supersededAt" IS NULL
            ORDER BY p."combinedPageNumber"
            """,
            (project_id,),
        ).fetchall()
        return [(r[0], r[1]) for r in rows]


def replace_portions(project_id: str, portions: list[dict]) -> None:
    """Atomically rebuild the project's portion rows (combined numbering may
    have shifted). Chunk links use ON DELETE SET NULL, so this stays safe once
    chunks exist; summaries are recomputed by the incremental pipeline later."""
    with connect() as conn:
        conn.execute('DELETE FROM portions WHERE "projectId" = %s', (project_id,))
        for p in portions:
            conn.execute(
                """
                INSERT INTO portions (id, "projectId", name, discipline, "startPage", "endPage")
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    str(uuid.uuid4()),
                    project_id,
                    p["name"],
                    p["discipline"],
                    p["startPage"],
                    p["endPage"],
                ),
            )


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
