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

import threading
import time

import config
import logutil
from chunker import Table

log = logutil.get("tables")

# Documents whose sheets proved too expensive to scan. Detection is best-effort
# and its cost is unbounded on drawing-heavy sets, so the first page to blow the
# budget takes the rest of its document with it — one slow page instead of
# every page. Keyed by document id and guarded, because pages are extracted by
# a thread pool.
_gave_up: set[str] = set()
_gave_up_lock = threading.Lock()


def reset_budget(document_id: str) -> None:
    """Forget a document's give-up state — for a retry, and for tests."""
    with _gave_up_lock:
        _gave_up.discard(document_id)


def find_page_tables(page, document_id: str = "") -> list[Table]:
    """Detected tables on one page, or [] when detection is off, over budget,
    or failing."""
    if not config.TABLE_EXTRACTION_ENABLED:
        return []
    with _gave_up_lock:
        if document_id in _gave_up:
            return []

    started = time.perf_counter()
    try:
        found = page.find_tables()
    except Exception as exc:
        # find_tables() reaches into page geometry and can raise on malformed
        # or unusual content streams. A page's tables are a bonus, never a
        # reason to fail a document that is otherwise extracting fine.
        log.debug("table detection failed on page %s: %s", page.number, exc)
        return []

    elapsed = time.perf_counter() - started
    if elapsed > config.TABLE_DETECTION_BUDGET_SECONDS:
        # Not this page's problem alone — a set is uniform, so if one sheet is
        # this expensive the rest will be too.
        with _gave_up_lock:
            first = document_id not in _gave_up
            _gave_up.add(document_id)
        if first:
            log.warning(
                "table detection took %.1fs on page %s (budget %.1fs) — "
                "skipping it for the rest of this document. Drawing-heavy "
                "sheets scan as one huge grid; raise "
                "TABLE_DETECTION_BUDGET_SECONDS to allow it, or set "
                "TABLE_EXTRACTION_ENABLED=false to switch it off entirely.",
                elapsed,
                page.number,
                config.TABLE_DETECTION_BUDGET_SECONDS,
            )

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
