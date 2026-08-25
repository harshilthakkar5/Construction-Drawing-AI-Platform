#!/usr/bin/env python3
"""Measure what a worker can actually do with a PDF: pages/minute and peak RSS.

These two numbers decide the real ceilings, and nothing in this repo had ever
measured them:

  * pages/minute        -> how long a 1 GB set takes to ingest
  * peak RSS per page   -> how many documents can be processed at once, since
                           PROCESS_CONCURRENCY x PAGE_CONCURRENCY pages are in
                           flight and each holds a full-resolution pixmap

By default it runs the extraction path (render, thumbnail, text, table
detection, chunking) WITHOUT touching storage, so it times CPU only.

    python benchmarks/extract_throughput.py drawings.pdf
    python benchmarks/extract_throughput.py drawings.pdf --upload

READ THIS BEFORE TRUSTING THE CPU NUMBER. Extraction uploads three objects per
page — a full-resolution PNG, a thumbnail and a text file — and on a real
worker that upload, not the rendering, is what takes the time. Measured against
production logs, the CPU-only figure overstated real throughput by roughly 18x
(125 pages/min measured here versus 6.7 pages/min actually achieved against
DigitalOcean Spaces). A page render is ~2 seconds; shipping a 15-megapixel PNG
to another continent is not.

So the CPU number answers "is my machine fast enough", and only `--upload`
answers "how long will my documents take". `--upload` does real round trips to
the configured bucket, so it needs the Spaces credentials in the environment
and it writes (then deletes) objects under a benchmark/ prefix.

The reported bytes-per-page is the number that matters most: multiply it by
your page count to see what the ingest actually has to ship.
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


BENCH_PREFIX = "benchmark/extract-throughput"


def thumbnail_bytes(page, width: int) -> bytes:
    """Same shape as processing._thumbnail_jpg: rendered at thumbnail scale
    rather than downsampled from the full-resolution pixmap."""
    import io

    from PIL import Image

    scale = width / page.rect.width
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale))
    with Image.open(io.BytesIO(pix.tobytes("png"))) as img:
        out = io.BytesIO()
        img.convert("RGB").save(out, format="JPEG", quality=70)
        return out.getvalue()


def render_one(pdf_path: str, index: int, zoom: float, local, upload: bool) -> dict:
    """One page through the same steps processing._process_page performs."""
    import chunker
    import config
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
    thumb = thumbnail_bytes(page, config.THUMB_WIDTH)
    encoded_text = text.encode("utf-8")
    found = tables.find_page_tables(page)
    chunks = chunker.chunk_page(
        page.get_text("blocks"),
        page_width=page.rect.width,
        page_height=page.rect.height,
        tables=found,
    )
    cpu_seconds = time.perf_counter() - started

    upload_seconds = 0.0
    if upload:
        import storage

        # The three objects the real pipeline writes per page, to the real
        # bucket — this is the part that dominates a production run.
        started_upload = time.perf_counter()
        for key, body, content_type in (
            (f"{BENCH_PREFIX}/{index}.png", png, "image/png"),
            (f"{BENCH_PREFIX}/{index}.jpg", thumb, "image/jpeg"),
            (f"{BENCH_PREFIX}/{index}.txt", encoded_text, "text/plain; charset=utf-8"),
        ):
            storage.put_bytes(key, body, content_type)
        upload_seconds = time.perf_counter() - started_upload

    return {
        "seconds": cpu_seconds + upload_seconds,
        "cpu_seconds": cpu_seconds,
        "upload_seconds": upload_seconds,
        "bytes": len(png) + len(thumb) + len(encoded_text),
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
    parser.add_argument(
        "--upload",
        action="store_true",
        help="also upload each page's three objects to the real bucket — the "
        "only mode that answers how long a document actually takes",
    )
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
    if args.upload:
        print(f"mode        REAL uploads to {config.SPACES_BUCKET} under {BENCH_PREFIX}/")
    else:
        print("mode        CPU only — no storage writes. On a real worker the "
              "upload\n            dominates, so this number will flatter you. "
              "See --upload.")
    print()

    local = threading.local()
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(
            pool.map(
                lambda i: render_one(args.pdf, i, zoom, local, args.upload),
                range(count),
            )
        )
    elapsed = time.perf_counter() - started

    per_page = [r["seconds"] for r in results]
    scanned = sum(1 for r in results if not r["has_text_layer"])
    pages_per_min = count / elapsed * 60

    bytes_per_page = statistics.mean(r["bytes"] for r in results)

    print(f"wall clock        {elapsed:.1f}s")
    print(f"pages/minute      {pages_per_min:.1f}"
          f"{'' if args.upload else '   <- CPU only, not achievable in production'}")
    print(f"per page          median {statistics.median(per_page):.2f}s  "
          f"p95 {sorted(per_page)[int(len(per_page) * 0.95) - 1]:.2f}s  "
          f"max {max(per_page):.2f}s")
    print(f"peak RSS          {peak_rss_mb():.0f} MB  ({workers} pages in flight)")
    print(f"chunks            {sum(r['chunks'] for r in results)} "
          f"({statistics.mean(r['chunks'] for r in results):.1f}/page)")
    print(f"tables found      {sum(r['tables'] for r in results)}")

    # The headline for a network-bound pipeline: three objects per page, and
    # the PNG is nearly all of it.
    print(f"\nbytes per page    {bytes_per_page / 1e6:.1f} MB "
          f"(PNG {statistics.mean(r['png_bytes'] for r in results) / 1e6:.1f} MB "
          "+ thumb + text)")
    print(f"total to ship     {bytes_per_page * total / 1e9:.2f} GB for all "
          f"{total} pages, against a {size_mb:.0f} MB input "
          f"({bytes_per_page * total / (size_mb * 1e6):.0f}x amplification)")

    if args.upload:
        cpu = sum(r["cpu_seconds"] for r in results)
        up = sum(r["upload_seconds"] for r in results)
        uploaded = sum(r["bytes"] for r in results)
        print(f"\nthread time       {cpu:.0f}s CPU + {up:.0f}s upload "
              f"({up / (cpu + up) * 100:.0f}% of the work is the upload)")
        print(f"upload throughput {uploaded / up / 1e6:.1f} MB/s effective")
    else:
        print("\nRe-run with --upload for a figure you can plan against — the "
              "pages/minute\nabove excludes shipping the bytes on the line above.")

    if scanned:
        print(f"\nno text layer     {scanned}/{count} pages — these need OCR, "
              "which this benchmark SKIPS (add several seconds per page)")

    if args.upload:
        # Leave nothing behind: this wrote three real objects per page into a
        # bucket someone pays for.
        import storage

        removed = 0
        for index in range(count):
            for extension in ("png", "jpg", "txt"):
                try:
                    storage.delete_key(f"{BENCH_PREFIX}/{index}.{extension}")
                    removed += 1
                except Exception as exc:  # noqa: BLE001 - cleanup is best effort
                    print(f"  could not delete {BENCH_PREFIX}/{index}.{extension}: {exc}")
        print(f"cleaned up        {removed} benchmark objects")

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
