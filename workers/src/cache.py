"""Redis cache invalidation (Phase 5).

The API caches each project's full summary list under one key
(apps/api/src/routes/summaries.ts — keep the key format in sync). The worker
is the only writer of summaries, so it deletes the key whenever a processing
or summarize run changes them; the API-side TTL is just a backstop.
"""

from __future__ import annotations

import redis as redis_lib

import config
import logutil

log = logutil.get("cache")

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
        log.info("invalidated summaries cache for project %s", project_id[:8])
    except Exception as exc:  # cache invalidation must never fail the job
        log.warning("failed to invalidate summaries cache for %s: %s", project_id, exc)


# Region previews are a request/response over Redis: the worker writes the
# result, the API polls it. Key format duplicated in
# apps/api/src/routes/region.ts `regionPreviewKey` — keep the two in sync.
PREVIEW_TTL_SECONDS = 600


def region_preview_key(preview_id: str) -> str:
    return f"region:preview:{preview_id}"


def store_region_preview(preview_id: str, payload: dict) -> None:
    import json

    _get_client().set(
        region_preview_key(preview_id), json.dumps(payload), ex=PREVIEW_TTL_SECONDS
    )
