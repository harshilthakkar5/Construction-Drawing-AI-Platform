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
    pages = db.pages_for_classification(project_id)
    # Classify each page ONCE, persist the per-page discipline (so chunks and
    # summaries group by discipline, not page range), then build one portion
    # per discipline.
    disciplines = classify.classify_pages(pages, redis_conn=_get_redis())
    db.set_page_disciplines(
        project_id, [(combined, disc) for (combined, _), disc in zip(pages, disciplines)]
    )
    portions = classify.group_portions(pages, disciplines)
    db.replace_portions(project_id, portions)
    log.info(
        "project %s: %s",
        project_id[:8],
        ", ".join(f"{p['name']} {p['startPage']}-{p['endPage']}" for p in portions) or "no pages",
    )
    return portions
