"""Project-level portion detection, run after each document completes.

Rebuilds the whole project's portions because combined numbering can shift
when a document is added. Haiku fallback results are cached in Redis by
title-block text hash, so rebuilds don't re-pay for unchanged pages.
"""

import redis as redis_lib

import classify
import config
import db

_redis = None


def _get_redis():
    global _redis
    if _redis is None:
        try:
            _redis = redis_lib.Redis.from_url(config.REDIS_URL)
            _redis.ping()
        except Exception as exc:
            print(f"[portions] Redis cache unavailable: {exc}")
            _redis = False
    return _redis or None


def detect_and_store(project_id: str) -> list[dict]:
    pages = db.pages_for_classification(project_id)
    portions = classify.build_portions(pages, redis_conn=_get_redis())
    db.replace_portions(project_id, portions)
    print(
        f"[portions] project {project_id}: "
        + (
            ", ".join(f"{p['name']} {p['startPage']}-{p['endPage']}" for p in portions)
            or "no pages"
        )
    )
    return portions
