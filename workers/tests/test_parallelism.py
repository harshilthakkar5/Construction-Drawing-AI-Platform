"""Parallel job execution: the settings that turn it on, and the lock that
keeps it correct.

Neither piece can be exercised against a real database here, so these cover
what is pure: how concurrency is configured, and how the advisory-lock key is
derived. The key matters more than it looks — two worker processes must
compute the SAME number for a project or the lock protects nothing, and it must
fit in a Postgres bigint or the call errors at runtime.
"""

import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402

import config  # noqa: E402

INT64_MIN, INT64_MAX = -(2**63), 2**63 - 1

PROJECT_A = "0c942156-0935-4c8f-83f8-3b1a948e2648"
PROJECT_B = "dc8bdff9-bca6-48cd-b244-298035e76900"


def _lock_key(project_id: str) -> int:
    """The derivation under test, mirrored so importing db (and psycopg) isn't
    required. Kept identical to db._lock_key on purpose — test_matches_db_impl
    below fails if they drift."""
    import hashlib

    return int.from_bytes(hashlib.sha256(project_id.encode()).digest()[:8], "big", signed=True)


class TestLockKey:
    def test_stable_across_processes(self):
        """Same project, same number, every time — this is the whole point."""
        assert _lock_key(PROJECT_A) == _lock_key(PROJECT_A)

    def test_different_projects_do_not_contend(self):
        assert _lock_key(PROJECT_A) != _lock_key(PROJECT_B)

    def test_fits_in_a_postgres_bigint(self):
        """pg_advisory_lock takes a bigint; an out-of-range value is a runtime
        error in the middle of a critical section."""
        for project in (PROJECT_A, PROJECT_B, "", "x" * 200):
            assert INT64_MIN <= _lock_key(project) <= INT64_MAX

    def test_matches_db_impl(self):
        """Guards against the mirrored copy above drifting from the real one."""
        db_module = pytest.importorskip("db", reason="needs psycopg installed")
        assert db_module._lock_key(PROJECT_A) == _lock_key(PROJECT_A)


class TestConcurrencyConfig:
    def _reload(self, monkeypatch, **env):
        for key in (
            "WORKER_CONCURRENCY",
            "PROCESS_CONCURRENCY",
            "SCRAPE_CONCURRENCY",
            "SUMMARIZE_PORTION_CONCURRENCY",
            "SUMMARIZE_PROJECT_CONCURRENCY",
            "DB_POOL_SIZE",
        ):
            monkeypatch.delenv(key, raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        return importlib.reload(config)

    def test_defaults_are_parallel(self, monkeypatch):
        """The point of the change: uploads must not serialize."""
        cfg = self._reload(monkeypatch)
        assert cfg.PROCESS_CONCURRENCY > 1

    def test_one_dial_sets_every_queue(self, monkeypatch):
        cfg = self._reload(monkeypatch, WORKER_CONCURRENCY="8")
        assert cfg.PROCESS_CONCURRENCY == 8
        assert cfg.SCRAPE_CONCURRENCY == 8
        assert cfg.SUMMARIZE_PORTION_CONCURRENCY == 8

    def test_per_queue_override_wins(self, monkeypatch):
        cfg = self._reload(monkeypatch, WORKER_CONCURRENCY="8", SCRAPE_CONCURRENCY="2")
        assert cfg.SCRAPE_CONCURRENCY == 2
        assert cfg.PROCESS_CONCURRENCY == 8

    def test_project_rollups_stay_serial_by_default(self, monkeypatch):
        """Rollups are cheap and one per project — parallelism buys nothing."""
        cfg = self._reload(monkeypatch, WORKER_CONCURRENCY="8")
        assert cfg.SUMMARIZE_PROJECT_CONCURRENCY == 1

    def test_pool_covers_the_concurrent_jobs(self, monkeypatch):
        """Too small a pool turns parallelism into lock-timeout errors."""
        cfg = self._reload(monkeypatch, WORKER_CONCURRENCY="6")
        assert cfg.DB_POOL_SIZE >= cfg.PROCESS_CONCURRENCY

    def test_garbage_and_zero_never_disable_the_worker(self, monkeypatch):
        """A typo in an env var must not leave a queue with 0 consumers."""
        assert self._reload(monkeypatch, WORKER_CONCURRENCY="not-a-number").PROCESS_CONCURRENCY >= 1
        assert self._reload(monkeypatch, WORKER_CONCURRENCY="0").PROCESS_CONCURRENCY == 1
        assert self._reload(monkeypatch, WORKER_CONCURRENCY="-4").PROCESS_CONCURRENCY == 1
        assert self._reload(monkeypatch, WORKER_CONCURRENCY="").PROCESS_CONCURRENCY >= 1

    def teardown_method(self):
        importlib.reload(config)  # leave the module as other tests expect it
