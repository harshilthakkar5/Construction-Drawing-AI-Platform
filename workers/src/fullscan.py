"""The full AI scan: every page of a project, checked for RFIs in sheet pairs.

A 500 MB set is never sent to a model, and could not be: the work is split
into the units a model can read accurately and code can check.

  Phase 1  catalogue    sheet_facts.py — what every page is (plan, section…),
                        its level, scales and grid. No model.
  Phase 2  plan         fullscan_plan.py — which sheets should agree (same
                        level, two disciplines; an enlarged plan over its
                        overall plan), lined up by geometry, cut into matching
                        tiles, priced. No model. The person approves it.
  Phase 3  first look   each tile pair (the SAME area of two sheets) to the
                        model with the words printed inside both windows;
                        half price through the provider's batch API.
  Phase 4  close look   every possible problem re-rendered close up on both
                        sheets and confirmed or rejected; then the code rules
                        (rfi_review.grounded, the pairing rules) and one
                        finding per element. What survives is a CANDIDATE —
                        nothing is an RFI until a person accepts it.

Everything is resumable: tiles are rows (`rfi_full_scan_tiles`) marked done as
they finish, each verified issue carries its verdict, and every candidate is
saved the moment it is confirmed. A dead worker, a budget stop or a cancel
loses at most the calls in flight. The token ceiling is checked against the
LEDGER (usage_events tagged with this scan's id) before every call or batch.

Never input: a document excluded from RFI analysis (historical RFIs are the
answer key), a superseded revision, annotations (stripped before any render).
"""

from __future__ import annotations

import concurrent.futures
import contextvars
import json
import logging
import os
import re
import threading
import time
from contextlib import contextmanager

import db
import fullscan_plan as plan_rules
import rfi_checks
import sheet_facts
from rfi_review import grounded

log = logging.getLogger("worker.fullscan")

HEARTBEAT_SECONDS = float(os.environ.get("FULL_SCAN_HEARTBEAT_SECONDS", "15"))


# --- Persistence --------------------------------------------------------------------


def _set(scan_id: str, **fields) -> None:
    columns = ", ".join(f'"{k}" = %s' for k in fields)
    with db.connect() as conn:
        conn.execute(f"UPDATE rfi_full_scans SET {columns} WHERE id = %s", (*fields.values(), scan_id))


def _now():
    with db.connect() as conn:
        return conn.execute("SELECT now()").fetchone()[0]


def load_scan(scan_id: str) -> dict | None:
    with db.connect() as conn:
        cur = conn.execute("SELECT * FROM rfi_full_scans WHERE id = %s", (scan_id,))
        row = cur.fetchone()
        if row is None:
            return None
        return dict(zip([c.name for c in cur.description], row))


def status_of(scan_id: str) -> str | None:
    with db.connect() as conn:
        row = conn.execute("SELECT status FROM rfi_full_scans WHERE id = %s", (scan_id,)).fetchone()
    return row[0] if row else None


class Cancelled(Exception):
    pass


class AccessRevoked(Exception):
    pass


@contextmanager
def heartbeat(scan_id: str):
    """Touch heartbeatAt while the job lives; the API reports a scan whose
    heartbeat stopped as failed, and offers to resume it."""
    stop = threading.Event()

    def beat() -> None:
        while not stop.wait(HEARTBEAT_SECONDS):
            try:
                _set(scan_id, heartbeatAt=_now())
            except Exception as exc:  # a missed beat must never fail the scan
                log.debug("full scan heartbeat for %s failed: %s", scan_id[:8], exc)

    _set(scan_id, heartbeatAt=_now())
    thread = threading.Thread(target=beat, name=f"full-scan-heartbeat-{scan_id[:8]}", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()


def source_revisions(project_id: str) -> dict[str, int]:
    """documentId -> page count of every live document the scan may read. A
    run whose set no longer matches is stale: tiles point at pages that moved."""
    rows, _ = sheet_facts.live_pages(project_id)
    out: dict[str, int] = {}
    for r in rows:
        out[r["documentId"]] = out.get(r["documentId"], 0) + 1
    return out


def handle(scan_id: str, mode: str) -> dict:
    if mode == "plan":
        return run_plan(scan_id)
    if mode == "run":
        import fullscan_run

        return fullscan_run.run(scan_id)
    raise ValueError(f"unknown full scan mode {mode!r}")


# --- Phase 1 + 2: the plan ---------------------------------------------------------------


def run_plan(scan_id: str) -> dict:
    scan = load_scan(scan_id)
    if scan is None:
        return {"skipped": "no such scan"}
    if scan["status"] != "planning":
        return {"skipped": scan["status"]}
    project_id = scan["projectId"]
    try:
        with heartbeat(scan_id):
            return _plan(scan_id, project_id)
    except Cancelled:
        return {"cancelled": True}
    except Exception as exc:
        _set(scan_id, status="failed", error=f"planning failed: {exc}"[:500], completedAt=_now())
        raise


def _check_cancel(scan_id: str) -> None:
    if status_of(scan_id) == "cancelled":
        raise Cancelled()


def _plan(scan_id: str, project_id: str) -> dict:
    started = time.monotonic()
    _set(scan_id, stage="catalogue", progress=1)
    last = [0.0]

    def progress(done: int, total: int) -> None:
        # Not every page: a 1000-page set would write a thousand updates.
        if time.monotonic() - last[0] > 3 or done == total:
            last[0] = time.monotonic()
            _check_cancel(scan_id)
            _set(scan_id, progress=1 + int(59 * done / max(total, 1)))

    facts, excluded, read = sheet_facts.catalogue(project_id, progress)
    catalogue = sheet_facts.summary(facts, excluded)
    log.info("full scan %s: catalogue of %d page(s), %d read now: %s", scan_id[:8], len(facts), read, catalogue["byKind"])
    _set(scan_id, stage="pairs", progress=62, catalogue=json.dumps(catalogue))

    pairs, skipped = plan_rules.candidate_pairs(facts)
    kept = _line_up(scan_id, project_id, pairs, skipped)
    tiles = [t for p in kept for t in p.tiles()]
    if len(tiles) > plan_rules.MAX_TILES:
        cut = len(tiles) - plan_rules.MAX_TILES
        skipped.setdefault(f"tile beyond the first {plan_rules.MAX_TILES} (FULL_SCAN_MAX_TILES)", []).append(f"{cut} tile(s)")
    notes = []
    off = sheet_facts.excluded_documents(project_id) if excluded.get("excludedFromRfi") else []
    kept_out = plan_rules.kept_out_note(excluded, len(facts), off)
    if kept_out:
        notes.append(kept_out)
    # With no page read, "nothing could be paired" restates the note above and
    # reads as a second, separate problem.
    if not kept and (facts or not kept_out):
        notes.append(plan_rules.no_pairs_note(skipped))

    with db.connect() as conn:
        conn.execute("DELETE FROM rfi_full_scan_tiles WHERE \"scanId\" = %s", (scan_id,))
        written = 0
        pair_json = []
        for index, pair in enumerate(kept):
            pair_tiles = pair.tiles()
            room = plan_rules.MAX_TILES - written
            pair_tiles = pair_tiles[: max(0, room)]
            for t_index, tile in enumerate(pair_tiles):
                conn.execute(
                    'INSERT INTO rfi_full_scan_tiles (id, "scanId", "pairIndex", "tileIndex", windows, status, "updatedAt") '
                    "VALUES (gen_random_uuid()::text, %s, %s, %s, %s::jsonb, 'pending', now())",
                    (scan_id, index, t_index, json.dumps(tile)),
                )
            written += len(pair_tiles)
            pair_json.append({
                "index": index,
                "kind": pair.kind,
                "a": pair.a.ref(),
                "b": pair.b.ref(),
                "reason": pair.reason,
                "tiles": len(pair_tiles),
                "transform": pair.transform.as_json() if pair.transform else None,
            })

    estimate = plan_rules.estimate(written)
    _set(
        scan_id,
        status="planned",
        stage="planned",
        progress=100,
        pairs=json.dumps(pair_json),
        skipped=json.dumps(plan_rules.skipped_list(skipped)),
        estimate=json.dumps(estimate),
        sourceRevisions=json.dumps(source_revisions(project_id)),
        notes=json.dumps(notes),
        plannedAt=_now(),
    )
    log.info(
        "full scan %s: planned %d pair(s), %d tile(s) in %.1fs", scan_id[:8], len(kept), written, time.monotonic() - started
    )
    return {"pairs": len(kept), "tiles": written, "pages": len(facts)}


def _line_up(scan_id: str, project_id: str, pairs, skipped: dict[str, list[str]]):
    """Each candidate pair with its tiles, or into `skipped` with the reason.
    Same-level pairs line up from the cached grid positions; one that cannot
    (no grid, as on most electrical and life-safety plans) is lined up by the
    walls both sheets draw instead (wall_match), read from the PDFs. An
    enlarged pair needs both sheets' geometry (the columns they draw)."""
    import plan_match
    import wall_match
    from rfi_review import _documents

    kept = []
    enlarged = [p for p in pairs if p.kind == "enlarged"]
    by_walls = []
    for pair in pairs:
        if pair.kind != "same_level":
            continue
        windows, why = plan_rules.same_level_windows(pair)
        if windows:
            pair.windows = windows
            kept.append(pair)
        else:
            by_walls.append((pair, why))
    if not enlarged and not by_walls:
        return kept
    docs = sorted({f.document_id for p in enlarged for f in (p.a, p.b)} | {f.document_id for p, _ in by_walls for f in (p.a, p.b)})
    geometry: dict[tuple[str, int], object] = {}
    walls: dict[tuple[str, int], object] = {}
    work = len(by_walls) + len(enlarged)
    with _documents(project_id, docs) as open_page:
        for i, (pair, grid_why) in enumerate(by_walls):
            _check_cancel(scan_id)
            for f in (pair.a, pair.b):
                key = (f.document_id, f.page_number)
                if key not in walls:
                    page = open_page(f.document_id, f.page_number)
                    walls[key] = wall_match.read_walls(page) if page is not None else None
            wa, wb = walls[(pair.a.document_id, pair.a.page_number)], walls[(pair.b.document_id, pair.b.page_number)]
            shift = wall_match.align(wa, wb) if wa is not None and wb is not None else None
            windows, why = plan_rules.wall_windows(pair, shift)
            if windows:
                pair.windows = windows
                kept.append(pair)
                log.info("full scan %s: %s / %s lined up by walls (%d match, next best %d)",
                         scan_id[:8], pair.a.label, pair.b.label, shift.matched, shift.runner_up)
            else:
                skipped.setdefault(f"pair not compared: {grid_why}, and the walls do not line up either", []).append(
                    f"{pair.a.label} / {pair.b.label}"
                )
            _set(scan_id, progress=62 + int(35 * (i + 1) / work))
        for i, pair in enumerate(enlarged):
            _check_cancel(scan_id)
            for f in (pair.a, pair.b):
                key = (f.document_id, f.page_number)
                if key not in geometry:
                    page = open_page(f.document_id, f.page_number)
                    geometry[key] = plan_match.SheetGeometry.read(page) if page is not None else None
            ga, gb = geometry[(pair.a.document_id, pair.a.page_number)], geometry[(pair.b.document_id, pair.b.page_number)]
            alignments = plan_match.align_sheets(ga, gb) if ga is not None and gb is not None else []
            windows, why = plan_rules.enlarged_windows(pair, alignments, detail_boxes(ga, alignments))
            if windows:
                pair.windows = windows
                kept.append(pair)
            else:
                skipped.setdefault(f"pair not compared: {why}", []).append(f"{pair.a.label} / {pair.b.label}")
            _set(scan_id, progress=62 + int(35 * (len(by_walls) + i + 1) / work))
    return kept


def detail_boxes(geometry, alignments) -> dict[int, list[float]]:
    """{detail: display rect} — the ink region plan_match segmented each
    aligned detail from, so a window stays inside its own drawing."""
    import numpy as np

    labels = getattr(geometry, "labels", None)
    if labels is None:
        return {}
    ppp = geometry.pt_per_px
    out = {}
    for al in alignments:
        ys, xs = np.where(labels == al.detail)
        if len(xs):
            out[al.detail] = [float(xs.min() * ppp), float(ys.min() * ppp), float((xs.max() + 1) * ppp), float((ys.max() + 1) * ppp)]
    return out


# --- Shared by the run ----------------------------------------------------------------


def spent_tokens(scan_id: str) -> tuple[int, int]:
    """(input, output) tokens this scan has spent, from the ledger. The one
    source of truth for the ceiling: it counts batch entries, retries and
    calls from a previous attempt of this scan alike."""
    with db.connect() as conn:
        row = conn.execute(
            'SELECT COALESCE(SUM("inputTokens"), 0), COALESCE(SUM("outputTokens"), 0) '
            'FROM usage_events WHERE "reviewRunId" = %s',
            (scan_id,),
        ).fetchone()
    return int(row[0]), int(row[1])


def fingerprint(check_id: str, sheets: list[str], element: str, material: str) -> str:
    """One finding per element and pair of sheets, whichever tile saw it.
    Identifiers in the element text that the drawings really carry decide it;
    with none, the normalised element words do."""
    import rfi_scan

    norm_material = rfi_checks.normalize(material)
    idents = sorted({
        rfi_checks.normalize(t) for t in rfi_scan._IDENT.findall(element.upper()) if rfi_checks.normalize(t) in norm_material
    })
    key = idents or [re.sub(r"[^A-Z0-9]+", " ", element.upper()).strip()]
    return rfi_checks.fingerprint("full_scan", check_id, *sorted(sheets), *key)


def run_in_context(fn):
    """A callable that runs `fn` in a copy of the CURRENT context — so a pool
    thread keeps the usage tag (usage.tagged) its submitter set."""
    ctx = contextvars.copy_context()
    return lambda *a, **k: ctx.run(fn, *a, **k)


def pool(workers: int):
    return concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="full-scan")


__all__ = ["handle", "run_plan", "spent_tokens", "fingerprint", "grounded"]
