"""db._get_pool: jobs start on several threads at once, and each used to build
its own pool, dropping all but the last with their connections open."""

import sys
import threading
import time
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import db  # noqa: E402


def test_threads_starting_together_share_one_pool(monkeypatch):
    built = []

    class SlowPool:
        def __init__(self, *a, **k):
            time.sleep(0.05)  # long enough for every thread to arrive
            built.append(self)

    monkeypatch.setitem(sys.modules, "psycopg_pool", types.SimpleNamespace(ConnectionPool=SlowPool))
    monkeypatch.setattr(db, "_pool", None)
    monkeypatch.setattr(db, "_pool_unavailable", False)
    got = []
    threads = [threading.Thread(target=lambda: got.append(db._get_pool())) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(built) == 1 and all(p is built[0] for p in got)
