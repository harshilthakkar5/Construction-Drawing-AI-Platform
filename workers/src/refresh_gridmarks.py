"""Re-read the grid marks of a project's processed documents, without reprocessing them.

    python src/refresh_gridmarks.py <projectId>

Grid-mark chunks are written at ingest, so a change to gridmarks.py reaches a
project only when its documents are processed again — which re-chunks every page,
re-mints every chunk id and re-embeds everything. This replaces the "gridmarks"
chunks alone (db.replace_page_kind), re-links chunks to portions, and embeds just
the new chunks when embeddings are enabled. Text chunks, summaries and RFIs are
untouched.
"""

from __future__ import annotations

import logging
import sys

import fitz

import config
import db
import embeddings
import gridmarks
import rfi_review

log = logging.getLogger("refresh_gridmarks")


def refresh(project_id: str) -> dict:
    with db.connect() as conn:
        docs = conn.execute(
            """SELECT id, "spacesKey", filename FROM documents
                WHERE "projectId" = %s AND "supersededAt" IS NULL AND status::text = 'completed'""",
            (project_id,),
        ).fetchall()
    totals = {"documents": len(docs), "pages": 0, "removed": 0, "written": 0, "embedded": 0}
    for doc_id, key, name in docs:
        pdf = fitz.open(rfi_review.cached_pdf(doc_id, key))
        for index, page in enumerate(pdf):
            chunks = gridmarks.chunks_for(page) if config.GRID_MARKS_ENABLED else []
            removed = db.replace_page_kind(doc_id, index + 1, "gridmarks", chunks)
            if removed or chunks:
                totals["pages"] += 1
                totals["removed"] += removed
                totals["written"] += len(chunks)
        pdf.close()
        log.info("%s: grid marks re-read", name)
    with db.project_lock(project_id):
        db.assign_chunk_portions(project_id)
    if config.EMBEDDINGS_ENABLED and embeddings.provider_available():
        # shortcut: the removed chunks' Qdrant points stay as orphans; retrieval drops ids with
        # no chunk row, so they cost a little space and nothing else. Delete by id if that grows.
        for doc_id, _, _ in docs:
            embedded = embeddings.embed_document_chunks(db.chunks_to_embed(doc_id))
            db.set_embedding_ids(embedded)
            totals["embedded"] += len(embedded)
    return totals


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    try:
        print(refresh(sys.argv[1]))
    finally:
        if db._pool is not None:
            db._pool.close()  # a script exits; the worker's pool threads would warn for 20s
