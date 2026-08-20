"""Table (schedule) detection — the fitz-touching half of table extraction.

Schedules are the densest facts in a drawing set: door, panel, beam, fixture
and finish schedules are what an engineer actually looks up. The block
extractor shreds them, because a table's cells arrive as unrelated text blocks
in content-stream order — "3070" ends up in a different chunk from the door
type it belongs to.

This module finds the table regions and hands their cells to
`chunker.table_chunks`, which decides how they become chunk text. Everything
that needs a fitz page lives here; everything that is a rule about the
resulting chunk lives in chunker.py, where it is unit-tested without a PDF.

Detection is best-effort by design: `page.find_tables()` is a heuristic over
ruling lines and text alignment, and a construction sheet is full of things
that resemble a grid (the title block, revision strips, drawing borders).
A miss costs nothing — the cells stay in the block stream and chunk the way
they always did — so every failure path here returns an empty list rather
than raising.
"""

from __future__ import annotations

import config
import logutil
from chunker import Table

log = logutil.get("tables")


def find_page_tables(page) -> list[Table]:
    """Detected tables on one page, or [] when detection is off or fails."""
    if not config.TABLE_EXTRACTION_ENABLED:
        return []
    try:
        found = page.find_tables()
    except Exception as exc:
        # find_tables() reaches into page geometry and can raise on malformed
        # or unusual content streams. A page's tables are a bonus, never a
        # reason to fail a document that is otherwise extracting fine.
        log.debug("table detection failed on page %s: %s", page.number, exc)
        return []

    tables: list[Table] = []
    for table in getattr(found, "tables", []) or []:
        try:
            rows = table.extract()
            x0, y0, x1, y1 = table.bbox
        except Exception as exc:
            log.debug("could not extract a detected table: %s", exc)
            continue
        if not rows:
            continue
        tables.append(Table(x0=x0, y0=y0, x1=x1, y1=y1, rows=rows))

    if len(tables) > config.MAX_TABLES_PER_PAGE:
        # A page reporting dozens of tables has had its drawing border and
        # grid lines read as a grid. Trust none of them rather than replace
        # the page's real text with fragments of the border.
        log.debug(
            "page %s reported %d tables (> %d) — treating as false positives",
            page.number,
            len(tables),
            config.MAX_TABLES_PER_PAGE,
        )
        return []
    return tables
