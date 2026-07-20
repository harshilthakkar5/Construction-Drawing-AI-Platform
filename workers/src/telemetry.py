"""OpenTelemetry metrics for the worker (Phase 5).

Exposes Prometheus metrics on WORKER_METRICS_PORT (default 9465): job
durations/counts per queue and embedding batch counters. Degrades to no-ops
when the OpenTelemetry packages aren't installed (same spirit as the optional
PaddleOCR dependency) so the worker always runs.
"""

from __future__ import annotations

import os
import time
from contextlib import contextmanager

import logutil

log = logutil.get("telemetry")

_job_duration = None
_jobs_total = None
_enabled = False


def start() -> None:
    """Boot the Prometheus exporter; call once from the worker entrypoint."""
    global _job_duration, _jobs_total, _enabled
    try:
        from opentelemetry import metrics
        from opentelemetry.exporter.prometheus import PrometheusMetricReader
        from opentelemetry.sdk.metrics import MeterProvider
        from prometheus_client import start_http_server
    except ImportError:
        log.warning("opentelemetry not installed — metrics disabled")
        return

    port = int(os.environ.get("WORKER_METRICS_PORT", "9465"))
    reader = PrometheusMetricReader()
    metrics.set_meter_provider(MeterProvider(metric_readers=[reader]))
    start_http_server(port)

    meter = metrics.get_meter("cdip-worker")
    _job_duration = meter.create_histogram(
        "worker_job_duration_ms", description="BullMQ job duration by queue/outcome"
    )
    _jobs_total = meter.create_counter(
        "worker_jobs_total", description="BullMQ jobs consumed by queue/outcome"
    )
    _enabled = True
    log.info("Prometheus metrics on http://localhost:%d/metrics", port)


@contextmanager
def observe_job(queue: str):
    """Times one job and records outcome=ok|error."""
    start_time = time.monotonic()
    outcome = "ok"
    try:
        yield
    except Exception:
        outcome = "error"
        raise
    finally:
        if _enabled:
            attrs = {"queue": queue, "outcome": outcome}
            _job_duration.record((time.monotonic() - start_time) * 1000, attrs)
            _jobs_total.add(1, attrs)
