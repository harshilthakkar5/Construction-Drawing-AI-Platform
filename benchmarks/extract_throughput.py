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

READ THIS BEFORE TRUSTING THE CPU NUMBER. Against production it overstated real
throughput by ~18x (125 pages/min here versus 6.7 actually achieved), because
extraction also uploads three objects per page and the stubbed run skips all of
it. Only `--upload` answers "how long will my documents take"; it does real
round trips to the configured bucket, so it needs the Spaces credentials in the
environment, and it deletes what it writes.

Do NOT assume which half dominates — it is not stable across sheet sets. On a
synthetic set the render was 57% and the upload negligible; on a real structural
IFC set it came out 63% CPU / 37% upload with 30 SECONDS of CPU per page. That
is why this prints a per-stage breakdown: "a page costs 30 seconds" is not
actionable until you know which stage owns the thirty, and the answer decides
whether the lever is PAGE_RENDER_ZOOM, TABLE_EXTRACTION_ENABLED, or the network.
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
from PIL import Image  # noqa: E402

# Imported at module scope deliberately: inside a timed function the first page
# pays the import cost and it is charged to whatever stage triggered it — which
# is exactly how the thumbnail first appeared to cost 25% of a page.
import io  # noqa: E402


def peak_rss_mb() -> float:
    """Peak resident set size, in MB.

    Three implementations because there is no portable one, and the number
    matters most on the machine least likely to have `resource`: Windows, where
    the POSIX module does not exist and this used to report `nan`.
    """
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        handle = ctypes.windll.kernel32.GetCurrentProcess()
        if ctypes.windll.psapi.GetProcessMemoryInfo(
            handle, ctypes.byref(counters), counters.cb
        ):
            return counters.PeakWorkingSetSize / (1024 * 1024)
        return float("nan")

    try:
        import resource

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return peak / 1024 if sys.platform != "darwin" else peak / (1024 * 1024)
    except Exception:
        return float("nan")


BENCH_PREFIX = "benchmark/extract-throughput"


def thumbnail_bytes(page, width: int) -> bytes:
    """Same shape as processing._thumbnail_jpg: rendered at thumbnail scale
    rather than downsampled from the full-resolution pixmap.

    (Measured both ways on a dense sheet: rendering again is ~5x faster than
    downsampling the full pixmap, so the pipeline's choice is the right one.)
    """
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

    # Warm the per-thread module imports before timing anything, for the same
    # reason: a cold import inside a stage is not that stage's cost.

    pdf = getattr(local, "pdf", None)
    if pdf is None:
        # Each thread opens its own Document: PyMuPDF is not thread-safe across
        # a shared one, which is what the real pipeline does too.
        pdf = local.pdf = fitz.open(pdf_path)

    stages: dict[str, float] = {}

    def timed(name: str, fn):
        """Per stage, because "30 seconds of CPU per page" is not actionable
        until you know WHICH thirty seconds."""
        started_stage = time.perf_counter()
        result = fn()
        stages[name] = stages.get(name, 0.0) + time.perf_counter() - started_stage
        return result

    started = time.perf_counter()
    page = timed("load_page", lambda: pdf.load_page(index))
    text = timed("extract text", lambda: page.get_text("text"))
    png = timed(
        "render + PNG encode",
        lambda: page.get_pixmap(matrix=fitz.Matrix(zoom, zoom)).tobytes("png"),
    )
    thumb = timed("thumbnail", lambda: thumbnail_bytes(page, config.THUMB_WIDTH))
    encoded_text = text.encode("utf-8")
    found = timed("detect tables", lambda: tables.find_page_tables(page))
    blocks = timed("extract blocks", lambda: page.get_text("blocks"))
    chunks = timed(
        "chunk",
        lambda: chunker.chunk_page(
            blocks,
            page_width=page.rect.width,
            page_height=page.rect.height,
            tables=found,
        ),
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

    if upload:
        stages["upload"] = upload_seconds
    return {
        "stages": stages,
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
        first = probe.load_page(0).rect
        page_megapixels = (first.width * zoom) * (first.height * zoom) / 1e6
    count = min(args.pages or total, total)
    size_mb = os.path.getsize(args.pdf) / 1e6

    print(f"file        {args.pdf}  ({size_mb:.1f} MB, {total} pages)")
    print(f"measuring   {count} pages, {workers} threads, zoom {zoom}")
    if args.upload:
        print(f"mode        REAL uploads to {config.SPACES_BUCKET} under {BENCH_PREFIX}/")
    else:
        print("mode        CPU only — no storage writes, so this number will "
              "flatter you.\n            Use --upload for a figure to plan "
              "against; the split between\n            CPU and upload varies "
              "enormously by sheet and by link.")
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

    # The breakdown is the point of running this at all: a page that costs 30
    # seconds is a different problem depending on which stage owns the 30.
    totals: dict[str, float] = {}
    for result in results:
        for stage, seconds in result["stages"].items():
            totals[stage] = totals.get(stage, 0.0) + seconds
    grand = sum(totals.values()) or 1.0
    print("\nwhere the time goes (thread time across all pages):")
    for stage, seconds in sorted(totals.items(), key=lambda kv: -kv[1]):
        bar = "#" * max(1, round(seconds / grand * 40))
        print(f"  {stage:<20} {seconds / count:6.2f}s/page  "
              f"{seconds / grand * 100:5.1f}%  {bar}")

    dominant, dominant_seconds = max(totals.items(), key=lambda kv: kv[1])
    if dominant == "detect tables":
        print("\n  Table detection dominates. It is a heuristic over ruled lines, and a "
              "\n  structural sheet is mostly ruled lines. Compare with "
              "TABLE_EXTRACTION_ENABLED=false\n  to see what it is buying you.")
    elif dominant == "render + PNG encode":
        print(f"\n  Rendering dominates. PAGE_RENDER_ZOOM={zoom} means "
              f"{page_megapixels:.0f} MP per sheet;\n  dropping to "
              f"{zoom * 0.75:.2f} cuts pixels ~44%, and the viewer may not need the "
              "detail.")
    elif dominant == "upload":
        print("\n  Upload dominates — the lever is bytes per page (render zoom, "
              "image format),\n  not concurrency.")

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
