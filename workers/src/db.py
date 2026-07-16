"""Direct PostgreSQL access for the worker (Prisma is Node-only).

Table/column names match apps/api/prisma/schema.prisma (@@map table names,
camelCase columns). The combined-numbering rule here MUST match
apps/api/src/manifest.ts: documents ordered by ("createdAt", id), pages
1..N within each document.
"""

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
