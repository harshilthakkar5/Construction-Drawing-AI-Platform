"""PaddleOCR wrapper (FR-7): run only on pages with no text layer.

PaddleOCR is a heavy optional dependency; if it isn't installed (or
OCR_ENABLED=false), pages without a text layer keep empty text and a
warning is logged instead of failing the whole document.
"""

import gc
import threading

import config
import logutil

log = logutil.get("ocr")

_engine = None
_unavailable = False

# ONE call into the engine at a time. A Paddle predictor is not thread-safe,
# and the engine is shared by every thread in the worker: PAGE_CONCURRENCY
# page threads at ingest, the scrape threads and an RFI scan, all at once.
# Concurrent runs corrupt its state, and after that EVERY call fails — the
# oneDNN "could not create a primitive" a scan logged on page after page.
# OCR is CPU-bound and Paddle already uses several cores per call, so the
# lock costs no throughput.
_lock = threading.RLock()

# Longest side a failed tile is retried at: PaddleOCR's own default, small
# enough that a worker short of memory can still run it.
FALLBACK_LIMIT = 960


class OcrError(RuntimeError):
    """The engine failed on an image even after a rebuild and a smaller retry."""


# Set once OCR must not run again in this process: a failure that looks like
# memory running out, or a worker still over OCR_MAX_RSS_MB after the engine
# was thrown away. A laptop whose OCR run grew until Docker's VM ran out of
# memory lost Docker AND the terminal; the old recovery made that worse by
# building a SECOND engine while the first was still held. Missing OCR on a
# few pages is recoverable (they stay unmarked for the next scan); a machine
# that falls over is not.
_halted: str | None = None
_calls = 0

# How oneDNN, Paddle and the C++ runtime say "no memory left". "could not
# create a primitive" is oneDNN failing to allocate; it is also what a CPU
# without AVX says, and that never recovers either.
_MEMORY_SIGNS = ("could not create a primitive", "out of memory", "bad_alloc", "memoryerror",
                 "resourceexhausted", "cannot allocate", "failed to allocate", "alloc failed")


def _looks_like_memory(message: str) -> bool:
    m = message.lower()
    return any(sign in m for sign in _MEMORY_SIGNS)


def _rss_mb() -> float | None:
    """This process's resident memory in MB, or None when it cannot be read."""
    try:
        import psutil

        return psutil.Process().memory_info().rss / 2**20
    except Exception:
        return None


def _free_mb() -> float | None:
    """Memory the machine (or Docker's VM) still has available, in MB."""
    try:
        import psutil

        return psutil.virtual_memory().available / 2**20
    except Exception:
        return None


def halted() -> str | None:
    """Why OCR is stopped in this worker process, or None."""
    return _halted


def _halt(reason: str) -> None:
    global _halted
    _halted = reason
    _free_engine()
    log.error("OCR STOPPED in this worker until it is restarted: %s. Pages not read stay unmarked and are "
              "read by a later scan. Fixes: give the worker more memory (WORKER_MEM_LIMIT, and the Docker "
              "VM's memory in .wslconfig on Windows), lower OCR_DET_LIMIT to 960, or OCR_ENABLED=false.", reason)


def _free_engine() -> None:
    """Drop the engine AND collect it before anything new is built: a new
    engine next to a dead one doubles the memory at the worst moment."""
    global _engine, _calls
    _engine = None
    _calls = 0
    gc.collect()


def _guard_memory() -> None:
    """Before a call: refuse it when the machine is nearly out of memory, and
    recycle (or stop) the engine when this process has grown too large."""
    global _calls
    free = _free_mb()
    if config.OCR_MIN_FREE_MB and free is not None and free < config.OCR_MIN_FREE_MB:
        _free_engine()
        raise OcrError(f"only {free:.0f} MB of memory free (OCR_MIN_FREE_MB={config.OCR_MIN_FREE_MB}); "
                       "OCR skipped so the machine does not run out")
    if config.OCR_ENGINE_RECYCLE and _calls >= config.OCR_ENGINE_RECYCLE:
        log.info("OCR: rebuilding the engine after %d images to release its memory", _calls)
        _free_engine()
    rss = _rss_mb()
    if config.OCR_MAX_RSS_MB and rss is not None and rss > config.OCR_MAX_RSS_MB:
        _free_engine()
        rss = _rss_mb()
        if rss is not None and rss > config.OCR_MAX_RSS_MB:
            _halt(f"the worker uses {rss:.0f} MB, over OCR_MAX_RSS_MB={config.OCR_MAX_RSS_MB}, even after "
                  "the OCR engine was released")
            raise OcrError(_halted)


def _get_engine():
    global _engine, _unavailable
    if _engine is not None or _unavailable:
        return _engine
    if not config.OCR_ENABLED:
        _unavailable = True
        return None
    try:
        from paddleocr import PaddleOCR

        # First construction downloads ~15 MB of models to ~/.paddleocr, over a
        # CDN that is slow and drop-prone from some regions. Pre-warm it (see
        # workers/Dockerfile) so this does not happen mid-job in production.
        # det_limit_side_len: PaddleOCR shrinks anything larger to 960px by
        # default, which on a page_ocr tile throws away the resolution the
        # tile was rendered at. Small crops are not enlarged ("max").
        # enable_mkldnn is PaddleOCR's default already; said here because the
        # oneDNN path is where "could not create a primitive" comes from.
        _engine = PaddleOCR(use_angle_cls=True, lang="en", show_log=False, enable_mkldnn=False,
                            det_limit_side_len=config.OCR_DET_LIMIT, det_limit_type="max")
    except (ImportError, ModuleNotFoundError) as exc:
        # Permanent: the package genuinely isn't installed. Latch it, so every
        # page doesn't retry an import that cannot start working.
        #
        # Name the consequence, not just the cause: without OCR a scanned or
        # flattened sheet yields no text at all — no page content to chunk, and
        # an empty title-block region, so the sheet ends up unclassified.
        log.warning(
            "PaddleOCR not installed, OCR disabled (%s) — scanned pages will "
            "return no text and no sheet number. Fix: pip install -r requirements.txt",
            exc,
        )
        _unavailable = True
    except Exception as exc:
        # Usually the model download dropping partway. Transient, so do NOT
        # latch: a blip during one page must not leave the whole worker without
        # OCR until it is restarted. The next page tries again.
        log.warning(
            "PaddleOCR could not start (%s) — skipping OCR for this page and "
            "retrying on the next. If it repeats, pre-download the models: "
            "python -c \"from paddleocr import PaddleOCR; PaddleOCR(lang='en')\"",
            exc,
        )
    return _engine


def _predict(img):
    """engine.ocr(img), serialised and memory-guarded, with one recovery: on a
    failure the engine is released and rebuilt and the image retried at
    FALLBACK_LIMIT (scaled back, so the caller sees pixel coordinates of the
    image it passed). A failure that looks like memory running out is NOT
    retried — OCR stops in this worker instead (`_halt`). Raises OcrError when
    nothing could be read — never an empty reading, since "could not read" and
    "nothing there" must stay tellable apart."""
    global _calls
    import cv2

    with _lock:
        if _halted:
            raise OcrError(_halted)
        _guard_memory()
        engine = _get_engine()
        if engine is None:
            return None
        failure = None
        try:
            result = engine.ocr(img, cls=True)
            _calls += 1
            return result
        except Exception as exc:
            # Only the message leaves this block: the exception's traceback
            # holds the failed engine's frames, which must be freed with it.
            failure = f"{exc}" or type(exc).__name__
        del engine
        _free_engine()
        if _looks_like_memory(failure):
            _halt(f"the OCR engine failed on a {img.shape[1]}x{img.shape[0]} image with \"{failure}\", "
                  "which is how it reports running out of memory")
            raise OcrError(_halted)
        log.warning("OCR engine failed on a %dx%d image (%s); rebuilding it and retrying at %dpx",
                    img.shape[1], img.shape[0], failure, FALLBACK_LIMIT)
        engine = _get_engine()
        if engine is None:
            raise OcrError(f"{failure} (and the engine could not be restarted)")
        scale = min(1.0, FALLBACK_LIMIT / max(img.shape[:2]))
        small = img if scale == 1.0 else cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        second = None
        try:
            result = engine.ocr(small, cls=True)
            _calls += 1
        except Exception as exc:
            second = f"{exc}" or type(exc).__name__
        if second is not None:
            del engine
            _free_engine()
            if _looks_like_memory(second):
                _halt(f"the OCR engine failed twice, the second time with \"{second}\"")
                raise OcrError(_halted)
            raise OcrError(second)
        if scale == 1.0:
            return result
        return [[[[[x / scale, y / scale] for x, y in item[0]], *item[1:]] for item in block or []]
                for block in result or []]


def ocr_png_bytes(png: bytes) -> str:
    if not available():
        return ""
    import cv2
    import numpy as np

    img = cv2.imdecode(np.frombuffer(png, dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return ""
    result = _predict(img)
    lines: list[str] = []
    for block in result or []:
        for item in block or []:
            # item = [box, (text, confidence)]
            if len(item) >= 2 and item[1]:
                lines.append(str(item[1][0]))
    return "\n".join(lines)


def available() -> bool:
    """Whether OCR can run at all (loads the engine on first call). False once
    OCR has been stopped in this worker (`halted`)."""
    with _lock:
        return _halted is None and _get_engine() is not None


def image_lines(pix, pad_to: int | None = None) -> list[tuple[list, str, float]]:
    """Every line PaddleOCR finds in a fitz Pixmap (RGB, no alpha), as
    (four corner points in pixels, text, confidence). Raises OcrError when
    the engine cannot read it.

    `pad_to` pads a smaller image with white on the right and bottom to that
    square, so every tile reaches the detector at ONE size: Paddle keeps work
    buffers per input shape, and the edge tiles of every page were each a new
    shape. Coordinates are unchanged, since the padding is after the image."""
    if not available():
        return []
    import numpy as np

    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n == 3:
        img = img[:, :, ::-1]  # RGB -> BGR, what PaddleOCR's cv2 pipeline expects
    if pad_to and (img.shape[0] < pad_to or img.shape[1] < pad_to):
        padded = np.full((max(pad_to, img.shape[0]), max(pad_to, img.shape[1]), img.shape[2]), 255, dtype=np.uint8)
        padded[: img.shape[0], : img.shape[1]] = img
        img = padded
    out = []
    for block in _predict(np.ascontiguousarray(img)) or []:
        for item in block or []:
            if len(item) >= 2 and item[1]:
                out.append((item[0], str(item[1][0]), float(item[1][1])))
    return out
