"""ScanProgress: what the RFIs tab shows while a scan runs.

A rescan that read shape text ran an hour behind one spinner; these pin what
the person now sees and that reporting can never break the scan."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402

import rfi_scan  # noqa: E402


class _Conn:
    def __init__(self, sink):
        self.sink = sink

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=()):
        self.sink.append((sql, params))

        class R:
            def fetchone(self):
                return ["NOW"]

        return R()


@pytest.fixture
def writes(monkeypatch):
    sink = []
    monkeypatch.setattr(rfi_scan.db, "connect", lambda: _Conn(sink))
    return sink


def _updates(sink):
    return [p for sql, p in sink if sql.startswith("UPDATE rfi_scans")]


def test_a_step_maps_its_share_onto_the_bar_and_beats_the_heartbeat(writes):
    report = rfi_scan.ScanProgress("scan-1")
    report("ocr", 5, 10, "Reading text drawn as shapes: page 6 of 10 (M0.02)")
    [params] = _updates(writes)
    stage, progress, detail, heartbeat, scan = params
    lo, hi = rfi_scan.STEPS["ocr"]
    assert (stage, progress, scan) == ("ocr", round(lo + (hi - lo) / 2), "scan-1")
    assert "M0.02" in detail and heartbeat == "NOW"


def test_writes_within_a_step_are_throttled_but_a_new_step_or_force_always_lands(writes, monkeypatch):
    clock = iter([100.0, 100.5, 101.0, 101.2])
    monkeypatch.setattr(rfi_scan.time, "monotonic", lambda: next(clock))
    report = rfi_scan.ScanProgress("scan-1")
    report("grids", 1, 100, "page 1")
    report("grids", 2, 100, "page 2")              # 0.5s later, same step: skipped
    report("grids", 3, 100, "page 3", force=True)  # forced: lands
    report("checks", detail="Running the code checks")  # new step: lands
    assert [p[0] for p in _updates(writes)] == ["grids", "grids", "checks"]


def test_a_failed_progress_write_never_fails_the_scan(monkeypatch):
    def broken():
        raise RuntimeError("database gone")

    monkeypatch.setattr(rfi_scan.db, "connect", broken)
    rfi_scan.ScanProgress("scan-1")("ocr", 1, 2, "page 2")  # no exception


def test_every_step_the_scan_reports_is_on_the_bar():
    import re

    source = (Path(rfi_scan.__file__)).read_text()
    used = set(re.findall(r'(?:progress|report)\("([a-z]+)"', source))
    assert used and used <= set(rfi_scan.STEPS)
    bounds = [rfi_scan.STEPS[k] for k in ("starting", "ocr", "loading", "grids", "checks", "pairs", "geometry",
                                          "pinpoint", "plan", "wording", "saving")]
    assert all(a[1] <= b[0] for a, b in zip(bounds, bounds[1:]))  # the bar only moves forward


def test_a_database_without_the_progress_columns_still_sees_the_scan_start(monkeypatch):
    """The status is written on its own, before any progress column: a worker
    on new code against a database without that migration used to fail its
    very first write, so the scan stayed "queued" and looked stuck in line."""
    sink = []

    class OldSchema(_Conn):
        def execute(self, sql, params=()):
            if '"stage"' in sql:
                raise RuntimeError('column "stage" does not exist')
            return super().execute(sql, params)

    monkeypatch.setattr(rfi_scan.db, "connect", lambda: OldSchema(sink))
    monkeypatch.setattr(rfi_scan, "_run", lambda project_id, scan_id, *a: {"findings": 0})
    monkeypatch.setattr(rfi_scan, "_ai_plan_requested", lambda scan_id: None)
    assert rfi_scan.run("project-1", "scan-1") == {"findings": 0}
    [first, *_] = [sql for sql, _ in sink if sql.startswith("UPDATE rfi_scans")]
    assert '"status"' in first and '"stage"' not in first
