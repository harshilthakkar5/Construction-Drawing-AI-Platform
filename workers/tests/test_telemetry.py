"""The metrics exporter is an aid: a taken port must not stop a worker."""

import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import telemetry  # noqa: E402


def test_a_taken_metrics_port_disables_metrics_and_carries_on(monkeypatch):
    with socket.socket() as held:
        held.bind(("0.0.0.0", 0))
        held.listen()
        monkeypatch.setenv("WORKER_METRICS_PORT", str(held.getsockname()[1]))
        monkeypatch.setattr(telemetry, "_enabled", False)
        telemetry.start()  # must not raise
    assert telemetry._enabled is False
    with telemetry.observe_job("rfi-review"):  # jobs still run, unmeasured
        pass
