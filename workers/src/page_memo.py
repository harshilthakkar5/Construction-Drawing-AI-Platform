"""Per-sheet results that never change for one document, kept between reviews.

A review reads the same sheets again and again: grid bubbles, column
positions, element marks. One Columns review read the grid of its 9 sheets 23
times and spent ~100 s of CPU before its first model call, and a sweep of the
62 library elements read S2.20 and S2.30-B in nearly every run. A document's
bytes never change (a revision is a NEW document id), so a page is keyed by
(documentId, pageNumber), set on the fitz page by whoever opened it (`tag`). A
page nobody tagged is computed every time, exactly as before.

Kept in memory per worker process (an LRU, `PAGE_MEMO_ENTRIES`), and — for
JSON-shaped results only — in Redis too, so other replicas share them. Every
hit is a deep COPY: callers fill fields in afterwards (Occurrence.unrotated),
and a shared object would carry one review's edits into the next.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import threading
from collections import OrderedDict
from typing import Any, Callable

import config

log = logging.getLogger("page_memo")

_ATTR = "cdip_key"
MAX_ENTRIES = int(os.environ.get("PAGE_MEMO_ENTRIES", "512"))
REDIS_TTL_SECONDS = 14 * 24 * 3600
_lock = threading.Lock()
_mem: OrderedDict[tuple, Any] = OrderedDict()
_redis: Any = None


def tag(page, document_id: str, page_number: int):
    """Mark a fitz page as (document, page) so its results can be reused."""
    if page is not None:
        setattr(page, _ATTR, (document_id, page_number))
    return page


def key_of(page) -> tuple | None:
    return getattr(page, _ATTR, None)


def _shared():
    global _redis
    if _redis is None:
        try:
            import redis as redis_lib

            _redis = redis_lib.Redis.from_url(config.REDIS_URL)
            _redis.ping()
        except Exception:  # no Redis: memory only
            _redis = False
    return _redis or None


def memo(name: str, page, compute: Callable[[], Any], *, shared: bool = False) -> Any:
    """compute(), or its earlier result for this page. `name` must change
    whenever what compute() returns would (put the algorithm's version in it).
    `shared` results go to Redis as JSON, so only JSON-shaped ones may set it."""
    key = key_of(page)
    if key is None:
        return compute()
    full = (name, *key)
    with _lock:
        if full in _mem:
            _mem.move_to_end(full)
            return copy.deepcopy(_mem[full])
    value = None
    store = _shared() if shared else None
    redis_key = f"rfi:page:{name}:{key[0]}:{key[1]}"
    if store is not None:
        try:
            raw = store.get(redis_key)
            if raw is not None:
                value = json.loads(raw)
        except Exception:
            value = None
    if value is None:
        value = compute()
        if store is not None:
            try:
                store.set(redis_key, json.dumps(value), ex=REDIS_TTL_SECONDS)
            except Exception:
                log.debug("page memo: could not store %s", redis_key)
    with _lock:
        _mem[full] = value
        _mem.move_to_end(full)
        while len(_mem) > MAX_ENTRIES:
            _mem.popitem(last=False)
    return copy.deepcopy(value)


def clear() -> None:
    """Forget everything held in memory (tests)."""
    with _lock:
        _mem.clear()
