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
    """Pages in documents ordered before this one (manifest ordering rule)."""
    with connect() as conn:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(d.pages), 0)
            FROM documents d, documents me
            WHERE me.id = %s
              AND d."projectId" = %s
              AND d.id <> me.id
              AND (d."createdAt" < me."createdAt"
                   OR (d."createdAt" = me."createdAt" AND d.id < me.id))
            """,
            (document_id, project_id),
        ).fetchone()
        return int(row[0]) if row else 0


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
) -> None:
    """Committed per page so a failure at page N preserves pages 1..N-1."""
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO pages (id, "documentId", "pageNumber", "combinedPageNumber", "imageUrl", text)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT ("documentId", "pageNumber")
            DO UPDATE SET "combinedPageNumber" = EXCLUDED."combinedPageNumber",
                          "imageUrl" = EXCLUDED."imageUrl",
                          text = EXCLUDED.text
            """,
            (str(uuid.uuid4()), document_id, page_number, combined_page_number, image_key, text),
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
                INSERT INTO chunks (id, "pageId", text, bbox, "tokenCount")
                VALUES (%s, %s, %s, %s, %s)
                """,
                (str(uuid.uuid4()), page_id, chunk.text, json.dumps(chunk.bbox), chunk.token_count),
            )


def assign_chunk_portions(project_id: str) -> None:
    """Point every chunk at the portion covering its page's combined number.
    Run after portions are rebuilt (combined numbering may have shifted)."""
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
              AND pages."combinedPageNumber" BETWEEN portions."startPage" AND portions."endPage"
            """,
            (project_id, project_id),
        )


def chunks_to_embed(document_id: str) -> list[dict]:
    """Chunks without a Qdrant point yet, with the metadata the payload needs."""
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT c.id, c.text, p."pageNumber", p."combinedPageNumber",
                   c."portionId", po.discipline, d."projectId", d.id
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


def pages_for_classification(project_id: str) -> list[tuple[int, str | None]]:
    """(combinedPageNumber, text) for every page in the project, combined order."""
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT p."combinedPageNumber", p.text
            FROM pages p
            JOIN documents d ON p."documentId" = d.id
            WHERE d."projectId" = %s
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
                             AND (d2."createdAt" < d."createdAt"
                                  OR (d2."createdAt" = d."createdAt" AND d2.id < d.id))
                       ) AS combined
                FROM pages p
                JOIN documents d ON p."documentId" = d.id
                WHERE d."projectId" = %s
            ) sub
            WHERE pages.id = sub.id
            """,
            (project_id,),
        )
