"""Worker logging: timestamped, leveled stage logs for the whole pipeline.

Every module logs through here so the worker's stdout reads as a clear
processing narrative:

    2026-07-20 10:12:01 INFO  [pipeline] [doc 13c3…] stage 1/6 download: …

Set LOG_LEVEL (DEBUG|INFO|WARNING|ERROR) to tune verbosity; DEBUG adds
per-page detail.
"""

import logging
import os

_configured = False


def setup() -> None:
    """Configure the root handler once (called from worker.py; get() also
    calls it so ad-hoc scripts and tests get sane output)."""
    global _configured
    if _configured:
        return
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # urllib3 logs "Failed to parse headers … HTTP/1.1 200 OK" with a traceback on every MinIO upload
    # (the 100-continue reply), though the upload succeeded: noise that buried every real line.
    logging.getLogger("urllib3.connection").setLevel(logging.ERROR)
    _configured = True


def get(name: str) -> logging.Logger:
    setup()
    return logging.getLogger(name)
