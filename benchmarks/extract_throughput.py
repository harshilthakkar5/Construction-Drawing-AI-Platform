#!/usr/bin/env python3
"""Measure what a worker can actually do with a PDF: pages/minute and peak RSS.

These two numbers decide the real ceilings, and nothing in this repo had ever
measured them:

  * pages/minute        -> how long a 1 GB set takes to ingest
  * peak RSS per page   -> how many documents can be processed at once, since
                           PROCESS_CONCURRENCY x PAGE_CONCURRENCY pages are in
                           flight and each holds a full-resolution pixmap

It runs the REAL extraction path (render, thumbnail, text, table detection,
chunking) with storage and database writes stubbed out, so what it times is the
CPU work rather than your network to Spaces. That makes it comparable between
machines and safe to run against a production PDF.

    python benchmarks/extract_throughput.py drawings.pdf
    python benchmarks/extract_throughput.py drawings.pdf --pages 50 --concurrency 4

Compare the reported pages/minute against `PAGE_CONCURRENCY` settings to find
the knee: past it, more threads stop buying throughput and only cost memory.
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "workers" / "src"))

import fitz  # noqa: E402


def peak_rss_mb() -> float:
    """Peak resident set size. resource.ru_maxrss is KB on Linux, bytes on macOS."""
    try:
        import resource

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return peak / 1024 if sys.platform != "darwin" else peak / (1024 * 1024)
    except Exception:
        return float("nan")


def render_one(pdf_path: str, index: int, zoom: float, local) -> dict:
    """One page through the same steps processing._process_page performs."""
    import chunker
    import tables

    pdf = getattr(local, "pdf", None)
    if pdf is None:
        # Each thread opens its own Document: PyMuPDF is not thread-safe across
        # a shared one, which is what the real pipeline does too.
        pdf = local.pdf = fitz.open(pdf_path)

    started = time.perf_counter()
    page = pdf.load_page(index)
    text = page.get_text("text")
    png = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom)).tobytes("png")
    found = tables.find_page_tables(page)
    chunks = chunker.chunk_page(
        page.get_text("blocks"),
        page_width=page.rect.width,
        page_height=page.rect.height,
        tables=found,
    )
    return {
        "seconds": time.perf_counter() - started,
        "png_bytes": len(png),
        "chars": len(text),
        "tables": len(found),
        "chunks": len(chunks),
        "has_text_layer": bool(text.strip()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", help="a real drawing set — synthetic PDFs measure nothing")
    parser.add_argument("--pages", type=int, default=0, help="0 = the whole document")
    parser.add_argument("--concurrency", type=int, default=0, help="0 = PAGE_CONCURRENCY")
    parser.add_argument("--zoom", type=float, default=0.0, help="0 = PAGE_RENDER_ZOOM")
    args = parser.parse_args()

    os.environ.setdefault("DATABASE_URL", "postgresql://unused")
    import config

    workers = args.concurrency or config.PAGE_CONCURRENCY
    zoom = args.zoom or config.PAGE_RENDER_ZOOM

    with fitz.open(args.pdf) as probe:
        total = probe.page_count
    count = min(args.pages or total, total)
    size_mb = os.path.getsize(args.pdf) / 1e6

    print(f"file        {args.pdf}  ({size_mb:.1f} MB, {total} pages)")
    print(f"measuring   {count} pages, {workers} threads, zoom {zoom}")
    print("(storage and database writes are stubbed — this is CPU work only)\n")

    local = threading.local()
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(
            pool.map(lambda i: render_one(args.pdf, i, zoom, local), range(count))
        )
    elapsed = time.perf_counter() - started

    per_page = [r["seconds"] for r in results]
    scanned = sum(1 for r in results if not r["has_text_layer"])
    pages_per_min = count / elapsed * 60

    print(f"wall clock        {elapsed:.1f}s")
    print(f"pages/minute      {pages_per_min:.1f}")
    print(f"per page          median {statistics.median(per_page):.2f}s  "
          f"p95 {sorted(per_page)[int(len(per_page) * 0.95) - 1]:.2f}s  "
          f"max {max(per_page):.2f}s")
    print(f"peak RSS          {peak_rss_mb():.0f} MB  ({workers} pages in flight)")
    print(f"rendered PNG      mean {statistics.mean(r['png_bytes'] for r in results) / 1e6:.1f} MB")
    print(f"chunks            {sum(r['chunks'] for r in results)} "
          f"({statistics.mean(r['chunks'] for r in results):.1f}/page)")
    print(f"tables found      {sum(r['tables'] for r in results)}")
    if scanned:
        print(f"no text layer     {scanned}/{count} pages — these need OCR, "
              "which this benchmark SKIPS (add several seconds per page)")

    print("\nExtrapolating:")
    for pages in (500, 1000, 3000):
        print(f"  {pages:>5} pages -> {pages / pages_per_min:.0f} min at this rate")
    print(
        f"\nMemory: PROCESS_CONCURRENCY({config.PROCESS_CONCURRENCY}) x "
        f"PAGE_CONCURRENCY({config.PAGE_CONCURRENCY}) = "
        f"{config.PROCESS_CONCURRENCY * config.PAGE_CONCURRENCY} pages in flight "
        "in the real worker. Size the box from the peak RSS above."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
