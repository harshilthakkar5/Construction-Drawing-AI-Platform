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
from dataclasses import dataclass, field

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


@dataclass
class PlanResult:
    """What planning found, before anything is written: the pages read, the
    pairs lined up (with their windows) and everything left out. Built once by
    the RFI scan (sharing its PDF opener) or by a plan-only job."""

    facts: list
    excluded: dict
    read: int
    catalogue: dict
    kept: list
    skipped: dict
    open_errors: dict
    notes: list = field(default_factory=list)
    # What lining up read ({"walls": …, "geometry": …}), reused by the code's
    # own comparisons so no page is read twice.
    cache: dict = field(default_factory=dict)


def live_document_ids(project_id: str) -> list[str]:
    rows, _ = sheet_facts.live_pages(project_id)
    return sorted({r["documentId"] for r in rows})


def build_plan(project_id: str, open_page, *, check_cancel=lambda: None, progress=None) -> PlanResult:
    """Phases 1 and 2 with no writes to any scan row: the catalogue, the pairs
    and their line-up. `progress(fraction, detail)` is called as it goes."""
    report = progress or (lambda fraction, detail: None)
    last = [0.0]

    def catalogue_progress(done: int, total: int) -> None:
        # Not every page: a 1000-page set would write a thousand updates.
        if time.monotonic() - last[0] > 3 or done == total:
            last[0] = time.monotonic()
            check_cancel()
            report(0.6 * done / max(total, 1), f"Reading what each sheet is: page {done} of {total}")

    facts, excluded, read = sheet_facts.catalogue(project_id, catalogue_progress, open_page=open_page)
    catalogue = sheet_facts.summary(facts, excluded)
    report(0.62, "Choosing the sheet pairs that should agree")
    pairs, skipped = plan_rules.candidate_pairs(facts)
    open_errors: dict[str, str] = {}
    cache: dict = {}
    kept = _line_up(project_id, pairs, skipped, open_errors, open_page=open_page, check_cancel=check_cancel, cache=cache,
                    tick=lambda done, work: report(0.62 + 0.35 * done / max(work, 1),
                                                   f"Lining up sheet pairs: {done} of {work}"))
    notes = []
    off = sheet_facts.excluded_documents(project_id) if excluded.get("excludedFromRfi") else []
    kept_out = plan_rules.kept_out_note(excluded, len(facts), off)
    if kept_out:
        notes.append(kept_out)
    unopened = unopened_note(open_errors, _filenames(project_id, list(open_errors)))
    if unopened:
        notes.append(unopened)
    # With no page read, "nothing could be paired" restates the note above and
    # reads as a second, separate problem.
    if not kept and (facts or not kept_out):
        notes.append(plan_rules.no_pairs_note(skipped))
    return PlanResult(facts, excluded, read, catalogue, kept, skipped, open_errors, notes, cache)


def write_plan(scan_id: str, project_id: str, result: PlanResult, triage=None,
               extra_notes: list[str] | None = None) -> dict:
    """Store a built plan on its scan row: the tiles to look at and the price.
    `triage` (geometry_checks.Triage) sets aside the windows the code settled
    and carries what it tells the AI about the rest."""
    reasons = triage.reasons if triage is not None else {}
    hints = triage.hints if triage is not None else {}
    kept, skipped = result.kept, dict(result.skipped)
    tiles = [t for p in kept for t in p.tiles()]
    if len(tiles) > plan_rules.MAX_TILES:
        cut = len(tiles) - plan_rules.MAX_TILES
        skipped.setdefault(f"tile beyond the first {plan_rules.MAX_TILES} (FULL_SCAN_MAX_TILES)", []).append(f"{cut} tile(s)")
    notes = list(result.notes) + list(extra_notes or [])
    if result.open_errors:
        log.error("full scan %s: %s", scan_id[:8], unopened_note(result.open_errors, {}))
    with db.connect() as conn:
        conn.execute("DELETE FROM rfi_full_scan_tiles WHERE \"scanId\" = %s", (scan_id,))
        written = 0
        set_aside = 0
        pair_json = []
        for index, pair in enumerate(kept):
            pair_tiles = pair.tiles()
            room = plan_rules.MAX_TILES - written
            pair_tiles = pair_tiles[: max(0, room)]
            sent = 0
            for t_index, tile in enumerate(pair_tiles):
                why = reasons.get((index, t_index))
                if (index, t_index) in hints:
                    tile = {**tile, **hints[(index, t_index)]}
                # A tile the code already settled is stored done, with the
                # reason, so the run never pays for it and the screen can say
                # why it was not shown to the AI.
                conn.execute(
                    'INSERT INTO rfi_full_scan_tiles (id, "scanId", "pairIndex", "tileIndex", windows, status, '
                    'outcome, "outcomeNote", "updatedAt") '
                    "VALUES (gen_random_uuid()::text, %s, %s, %s, %s::jsonb, %s, %s, %s, now())",
                    (scan_id, index, t_index, json.dumps(tile), "skipped" if why else "pending",
                     "settled_by_code" if why else None, why),
                )
                if why:
                    set_aside += 1
                else:
                    sent += 1
            written += sent
            pair_json.append({
                "index": index,
                "kind": pair.kind,
                "a": pair.a.ref(),
                "b": pair.b.ref(),
                "reason": pair.reason,
                "tiles": sent,
                "tilesSettled": len(pair_tiles) - sent,
                "transform": pair.transform.as_json() if pair.transform else None,
            })
    if set_aside:
        notes.append(
            f"The code settled {set_aside} of {written + set_aside} area(s) itself, so they are not sent to the AI "
            "(blank on one sheet, or every line both sheets draw lines up exactly). "
            f"{written} area(s) are left for the AI to compare."
        )
    estimate = plan_rules.estimate(written)
    _set(
        scan_id,
        status="planned",
        stage="planned",
        progress=100,
        catalogue=json.dumps(result.catalogue),
        pairs=json.dumps(pair_json),
        skipped=json.dumps(plan_rules.skipped_list(skipped)),
        estimate=json.dumps(estimate),
        sourceRevisions=json.dumps(source_revisions(project_id)),
        notes=json.dumps(notes),
        plannedAt=_now(),
    )
    log.info("full scan %s: planned %d pair(s), %d tile(s) for the AI, %d settled by code",
             scan_id[:8], len(kept), written, set_aside)
    return {"pairs": len(kept), "tiles": written, "settled": set_aside, "pages": len(result.facts)}


def _plan(scan_id: str, project_id: str) -> dict:
    """The plan-only job ("Prepare the comparison only"): its own opener."""
    from rfi_review import _documents

    started = time.monotonic()
    _set(scan_id, stage="catalogue", progress=1)

    def progress(fraction: float, detail: str) -> None:
        _set(scan_id, stage="catalogue" if fraction < 0.6 else "pairs", progress=1 + int(96 * fraction))

    with _documents(project_id, live_document_ids(project_id)) as open_page:
        result = build_plan(project_id, open_page, check_cancel=lambda: _check_cancel(scan_id), progress=progress)
        import geometry_checks

        triage = geometry_checks.triage_tiles(result.kept, open_page, result.cache) if result.kept else None
    log.info("full scan %s: catalogue of %d page(s), %d read now: %s", scan_id[:8], len(result.facts), result.read,
             result.catalogue["byKind"])
    out = write_plan(scan_id, project_id, result, triage)
    log.info("full scan %s: planned in %.1fs", scan_id[:8], time.monotonic() - started)
    return out


UNOPENED = "pair not compared: the drawing file could not be downloaded or opened (see the note above)"


def unopened_note(errors: dict[str, str], names: dict[str, str]) -> str | None:
    """The plan's note when a PDF could not be read: which file, the error,
    and that this is a storage problem rather than a finding about the
    drawings. Without it every such pair read 'the walls do not line up'."""
    if not errors:
        return None
    parts = [f"{names.get(d, d[:8])} ({e})" for d, e in sorted(errors.items())]
    return (
        "The worker could not download or open " + ", ".join(parts[:3])
        + (f" and {len(parts) - 3} more" if len(parts) > 3 else "")
        + ", so the pairs on it were not compared. This is a storage or file problem, not a finding about "
        "the drawings: check the worker can reach the object store (STORAGE_BACKEND and its keys in the "
        "worker's .env) and plan again."
    )


def _filenames(project_id: str, document_ids: list[str]) -> dict[str, str]:
    if not document_ids:
        return {}
    with db.connect() as conn:
        return dict(conn.execute(
            'SELECT id, filename FROM documents WHERE "projectId" = %s AND id = ANY(%s::text[])',
            (project_id, document_ids),
        ).fetchall())


def _line_up(project_id: str, pairs, skipped: dict[str, list[str]],
             open_errors: dict[str, str] | None = None, *, open_page, check_cancel=lambda: None,
             tick=lambda done, work: None, cache: dict | None = None):
    """Each candidate pair with its tiles, or into `skipped` with the reason.
    Same-level pairs line up from the cached grid positions; one that cannot
    (no grid, as on most electrical and life-safety plans) is lined up by the
    walls both sheets draw instead (wall_match), read from the PDFs. An
    enlarged pair needs both sheets' geometry (the columns they draw).

    `cache` keeps what was read ({"walls": …, "geometry": …} by (document,
    page)) for the code's own comparisons of the same pairs afterwards."""
    import plan_match
    import wall_match

    open_errors = {} if open_errors is None else open_errors
    cache = {} if cache is None else cache
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
    geometry = cache.setdefault("geometry", {})
    walls = cache.setdefault("walls", {})
    work = len(by_walls) + len(enlarged)
    for i, (pair, grid_why) in enumerate(by_walls):
        check_cancel()
        for f in (pair.a, pair.b):
            key = (f.document_id, f.page_number)
            if key not in walls:
                page = open_page(f.document_id, f.page_number)
                walls[key] = wall_match.read_walls(page) if page is not None else None
        wa, wb = walls[(pair.a.document_id, pair.a.page_number)], walls[(pair.b.document_id, pair.b.page_number)]
        if wa is None or wb is None:
            # Not a disagreement: the drawing was never read.
            skipped.setdefault(UNOPENED, []).append(f"{pair.a.label} / {pair.b.label}")
            tick(i + 1, work)
            continue
        shift = wall_match.align(wa, wb)
        pair.wall_shift = shift
        windows, why = plan_rules.wall_windows(pair, shift)
        if windows:
            pair.windows = windows
            kept.append(pair)
            log.info("full scan: %s / %s lined up by walls (%d match, next best %d)",
                     pair.a.label, pair.b.label, shift.matched, shift.runner_up)
        else:
            skipped.setdefault(f"pair not compared: {grid_why}, and the walls do not line up either", []).append(
                f"{pair.a.label} / {pair.b.label}"
            )
        tick(i + 1, work)
    for i, pair in enumerate(enlarged):
        check_cancel()
        for f in (pair.a, pair.b):
            key = (f.document_id, f.page_number)
            if key not in geometry:
                page = open_page(f.document_id, f.page_number)
                geometry[key] = plan_match.SheetGeometry.read(page) if page is not None else None
        ga, gb = geometry[(pair.a.document_id, pair.a.page_number)], geometry[(pair.b.document_id, pair.b.page_number)]
        if ga is None or gb is None:
            skipped.setdefault(UNOPENED, []).append(f"{pair.a.label} / {pair.b.label}")
            tick(len(by_walls) + i + 1, work)
            continue
        alignments = plan_match.align_sheets(ga, gb)
        pair.alignments = alignments
        windows, why = plan_rules.enlarged_windows(pair, alignments, detail_boxes(ga, alignments))
        if windows:
            pair.windows = windows
            kept.append(pair)
        else:
            skipped.setdefault(f"pair not compared: {why}", []).append(f"{pair.a.label} / {pair.b.label}")
        tick(len(by_walls) + i + 1, work)
    open_errors.update(getattr(open_page, "errors", {}))
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
