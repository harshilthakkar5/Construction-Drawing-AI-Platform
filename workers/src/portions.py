"""Project-level portion detection, run after each document completes.

Rebuilds the whole project's portions because combined numbering can shift
when a document is added. Haiku fallback results are cached in Redis by
title-block text hash, so rebuilds don't re-pay for unchanged pages.
"""

import redis as redis_lib

import classify
import config
import db
import logutil

log = logutil.get("portions")

_redis = None


def _get_redis():
    global _redis
    if _redis is None:
        try:
            _redis = redis_lib.Redis.from_url(config.REDIS_URL)
            _redis.ping()
        except Exception as exc:
            log.warning("Redis classification cache unavailable: %s", exc)
            _redis = False
    return _redis or None


def detect_and_store(project_id: str) -> list[dict]:
    """Rebuild the project's portions. Detection runs for the WHOLE project
    (combined numbering shifts when a document arrives), but pages that already
    have a stored discipline are reused — so a new upload only pays for AI
    sheet-number reads on its own pages."""
    classify.set_usage_project(project_id)  # attributes Haiku sheet reads
    rows = db.pages_for_classification(project_id)
    pages = [(combined, text) for combined, text, _, _ in rows]

    resolved: list[str | None] = [stored for _, _, _, stored in rows]
    pending = [i for i, discipline in enumerate(resolved) if not discipline]
    if pending:
        fresh = classify.resolve_disciplines(
            [pages[i] for i in pending],
            redis_conn=_get_redis(),
            filenames=[rows[i][2] for i in pending],
        )
        for slot, discipline in zip(pending, fresh):
            resolved[slot] = discipline
    log.info(
        "project %s: classified %d new pages, reused %d already classified",
        project_id[:8],
        len(pending),
        len(rows) - len(pending),
    )

    # Pages with no readable sheet number inherit from their neighbours.
    disciplines = classify.fill_unresolved(resolved)

    # Persist only what changed, then build one portion per discipline.
    changed = [
        (combined, discipline)
        for (combined, _, _, stored), discipline in zip(rows, disciplines)
        if stored != discipline
    ]
    if changed:
        db.set_page_disciplines(project_id, changed)
    portions = classify.group_portions(pages, disciplines)
    db.replace_portions(project_id, portions)
    log.info(
        "project %s portions: %s",
        project_id[:8],
        ", ".join(f"{p['name']} {p['startPage']}-{p['endPage']}" for p in portions) or "no pages",
    )
    return portions
