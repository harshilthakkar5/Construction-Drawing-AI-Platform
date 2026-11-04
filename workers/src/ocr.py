"""PaddleOCR wrapper (FR-7): run only on pages with no text layer.

PaddleOCR is a heavy optional dependency; if it isn't installed (or
OCR_ENABLED=false), pages without a text layer keep empty text and a
warning is logged instead of failing the whole document.
"""

import gc
import re
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
# Memory failures in a row. Each one ends the engine's process (all its memory
# goes back to the OS) and the next image starts a fresh one; only a run of
# them says the machine cannot hold even one engine, and stops OCR.
_memory_strikes = 0
MEMORY_STRIKES = 3

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


def _cgroup_free_mb(root: str = "/sys/fs/cgroup") -> float | None:
    """Room left under this container's memory limit (cgroup v2), or None
    outside a limited container. psutil reports the whole Docker VM, so a
    worker at its WORKER_MEM_LIMIT still looked like it had gigabytes free."""
    try:
        with open(f"{root}/memory.max") as f:
            limit = f.read().strip()
        if limit == "max":
            return None
        with open(f"{root}/memory.current") as f:
            used = int(f.read().strip())
        return (int(limit) - used) / 2**20
    except (OSError, ValueError):
        return None


def _free_mb() -> float | None:
    """Memory still available to this worker in MB: the machine's (or Docker
    VM's), or the container limit's when that is tighter."""
    try:
        import psutil

        free = psutil.virtual_memory().available / 2**20
    except Exception:
        free = None
    room = _cgroup_free_mb()
    if room is None:
        return free
    return room if free is None else min(free, room)


def _memory_report() -> str:
    """The numbers that say which limit was hit, for the halt message."""
    rss, free = _rss_mb(), _free_mb()
    parts = []
    if rss is not None:
        parts.append(f"worker using {rss:.0f} MB")
    if free is not None:
        parts.append(f"{free:.0f} MB free" + (" under the container limit" if _cgroup_free_mb() is not None else ""))
    return "; ".join(parts) or "memory figures unavailable"


def halted() -> str | None:
    """Why OCR is stopped in this worker process, or None."""
    return _halted


def _halt(reason: str) -> None:
    global _halted
    _halted = reason
    _free_engine()
    # The image size in `reason` is the tile, always 1600px; the setting that
    # sizes the engine's work is OCR_DET_LIMIT, so say what it is now.
    lower = ("lower OCR_DET_LIMIT to 960 (now %d), " % config.OCR_DET_LIMIT
             if config.OCR_DET_LIMIT > 960 else "OCR_DET_LIMIT is already %d, so " % config.OCR_DET_LIMIT)
    log.error("OCR STOPPED in this worker until it is restarted: %s (%s). Pages not read stay unmarked and are "
              "read by a later scan. Fixes: give the worker more memory (WORKER_MEM_LIMIT, and the Docker "
              "VM's memory in .wslconfig on Windows), %slower PAGE_CONCURRENCY (now %d) so fewer pages are "
              "rendered beside the engine, or OCR_ENABLED=false.", reason, _memory_report(), lower,
              config.PAGE_CONCURRENCY)


def _free_engine() -> None:
    """Drop the engine AND end its process before anything new is built: a
    new engine next to a dead one doubles the memory at the worst moment."""
    global _engine, _calls
    engine, _engine = _engine, None
    _calls = 0
    close = getattr(engine, "close", None)
    if close:
        close()
    del engine
    gc.collect()


def _child_main(conn, det_limit: int) -> None:
    """The OCR engine's own process: build PaddleOCR, then read images from
    `conn` until told to stop. Paddle keeps the memory it allocates for the
    life of the process, so rebuilding the engine in the worker never gave it
    back and a long run grew until an allocation failed; ending this process
    does."""
    try:
        from paddleocr import PaddleOCR

        engine = PaddleOCR(use_angle_cls=True, lang="en", show_log=False, enable_mkldnn=False,
                           det_limit_side_len=det_limit, det_limit_type="max")
    except BaseException as exc:  # noqa: BLE001 - reported to the parent, which decides
        conn.send(("missing" if isinstance(exc, ImportError) else "error", f"{type(exc).__name__}: {exc}"))
        return
    conn.send(("ready", None))
    while True:
        try:
            img = conn.recv()
        except EOFError:
            return
        if img is None:
            return
        try:
            conn.send(("ok", engine.ocr(img, cls=True)))
        except Exception as exc:  # noqa: BLE001
            conn.send(("error", f"{exc}" or type(exc).__name__))


class _ProcessEngine:
    """PaddleOCR in a child process, with the engine's own `ocr` method."""

    START_SECONDS = 600  # the first start may download the models
    CALL_SECONDS = 600

    def __init__(self, det_limit: int):
        import multiprocessing

        ctx = multiprocessing.get_context("spawn")  # the same on Windows, macOS and Linux
        self._conn, child = ctx.Pipe()
        self._proc = ctx.Process(target=_child_main, args=(child, det_limit), daemon=True,
                                 name="ocr-engine")
        self._proc.start()
        child.close()
        kind, detail = self._reply(self.START_SECONDS, "starting")
        if kind == "missing":
            self.close()
            raise ImportError(detail)
        if kind != "ready":
            self.close()
            raise RuntimeError(detail)

    def _reply(self, seconds: float, doing: str):
        try:
            if self._conn.poll(seconds):
                return self._conn.recv()
        except (EOFError, OSError):
            pass
        code = self._proc.exitcode
        self.close()
        if code is None:
            raise RuntimeError(f"the OCR process did not answer within {seconds:.0f}s while {doing}")
        raise RuntimeError(f"the OCR process ended while {doing} (exit code {code}); the usual cause is "
                           "the system running out of memory")

    def ocr(self, img, cls=True):
        try:
            self._conn.send(img)
        except (OSError, ValueError) as exc:
            self.close()
            raise RuntimeError(f"the OCR process is gone ({exc})") from None
        kind, detail = self._reply(self.CALL_SECONDS, "reading an image")
        if kind != "ok":
            raise RuntimeError(detail)
        return detail

    def rss_mb(self) -> float | None:
        try:
            import psutil

            return psutil.Process(self._proc.pid).memory_info().rss / 2**20
        except Exception:
            return None

    def close(self) -> None:
        try:
            self._conn.send(None)
        except Exception:
            pass
        self._proc.join(5)
        if self._proc.is_alive():
            self._proc.kill()
            self._proc.join(5)
        self._conn.close()


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
        log.info("OCR: restarting the engine after %d images to release its memory", _calls)
        _free_engine()
    # OCR_MAX_RSS_MB caps the ENGINE's process: past it, the process is
    # replaced, which hands every byte it held back to the OS.
    measure = getattr(_engine, "rss_mb", None)
    rss = measure() if measure else None
    if config.OCR_MAX_RSS_MB and rss is not None and rss > config.OCR_MAX_RSS_MB:
        log.info("OCR: the engine uses %.0f MB (OCR_MAX_RSS_MB=%d); restarting it", rss, config.OCR_MAX_RSS_MB)
        _free_engine()


def _get_engine():
    global _engine, _unavailable
    if _engine is not None or _unavailable:
        return _engine
    if not config.OCR_ENABLED:
        _unavailable = True
        return None
    try:
        import importlib.util

        # Checked without importing: importing Paddle here would put it in the
        # worker's own memory, which is what the separate process avoids.
        if importlib.util.find_spec("paddleocr") is None:
            raise ImportError("No module named 'paddleocr'")
        # The first start downloads ~15 MB of models to ~/.paddleocr (the
        # Dockerfile pre-warms them). det_limit_side_len: PaddleOCR shrinks
        # anything larger to 960px by default, which throws away the tile's
        # resolution; small crops are not enlarged ("max").
        _engine = _ProcessEngine(config.OCR_DET_LIMIT)
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
    global _calls, _memory_strikes
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
            _memory_strikes = 0
            return result
        except Exception as exc:
            # Only the message leaves this block: the exception's traceback
            # holds the failed engine's frames, which must be freed with it.
            failure = f"{exc}" or type(exc).__name__
        del engine
        _free_engine()
        if _looks_like_memory(failure):
            # Not retried now: the engine's process is gone with its memory,
            # and the NEXT image starts a fresh one. Only a run of these stops OCR.
            _memory_strikes += 1
            message = (f"the OCR engine failed on a {img.shape[1]}x{img.shape[0]} image with \"{failure}\", "
                       "which is how it reports running out of memory")
            if _memory_strikes >= MEMORY_STRIKES:
                _halt(f"{message}, {_memory_strikes} times in a row on a fresh engine each time")
                raise OcrError(_halted)
            log.warning("%s (%s); its process was ended, the next image starts a fresh one",
                        message, _memory_report())
            raise OcrError(message)
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
        _memory_strikes = 0 if second is None else _memory_strikes
        if second is not None:
            del engine
            _free_engine()
            if _looks_like_memory(second):
                _memory_strikes += 1
                if _memory_strikes >= MEMORY_STRIKES:
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
                lines.append(zeros_for_os(str(item[1][0])))
    return "\n".join(lines)


# OCR reads a zero drawn as strokes as the letter O. Victaulic's structural sheets
# printed "SO.1" twenty times for S0.1 (so no reference reached the footing
# schedule's sheet), "1O psf", "3OOO psi" and "IBC 17O4". Two narrow shapes only:
# a discipline prefix before ".digit", and an O inside a run that starts with digits.
_SHEET_O = re.compile(r"\b(FP|FA|IT|AV|A|C|E|F|G|H|I|L|M|P|S|T|X)O(?=\.\d)")
_NUMBER_O = re.compile(r"(?<![A-Za-z0-9.])\d+[O\d]*O[O\d]*(?![A-Za-z0-9])")


def zeros_for_os(text: str) -> str:
    """`text` with an O OCR read where a zero is printed put back. Pure."""
    text = _SHEET_O.sub(r"\g<1>0", text)
    return _NUMBER_O.sub(lambda m: m.group(0).replace("O", "0"), text)


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
                out.append((item[0], zeros_for_os(str(item[1][0])), float(item[1][1])))
    return out
