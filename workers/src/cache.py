"""Redis cache invalidation (Phase 5).

The API caches each project's full summary list under one key
(apps/api/src/routes/summaries.ts — keep the key format in sync). The worker
is the only writer of summaries, so it deletes the key whenever a processing
or summarize run changes them; the API-side TTL is just a backstop.
"""

from __future__ import annotations

import redis as redis_lib

import config

_client: redis_lib.Redis | None = None


def _get_client() -> redis_lib.Redis:
    global _client
    if _client is None:
        _client = redis_lib.Redis.from_url(config.REDIS_URL)
    return _client


def summaries_cache_key(project_id: str) -> str:
    return f"cache:summaries:{project_id}"


def invalidate_summaries(project_id: str) -> None:
    try:
        _get_client().delete(summaries_cache_key(project_id))
    except Exception as exc:  # cache invalidation must never fail the job
        print(f"[cache] failed to invalidate summaries cache: {exc}")
