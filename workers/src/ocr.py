"""PaddleOCR wrapper (FR-7): run only on pages with no text layer.

PaddleOCR is a heavy optional dependency; if it isn't installed (or
OCR_ENABLED=false), pages without a text layer keep empty text and a
warning is logged instead of failing the whole document.
"""

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


def _rebuild() -> None:
    """Throw the engine away: after a failed run its state cannot be trusted."""
    global _engine
    _engine = None


def _predict(img):
    """engine.ocr(img), serialised, with one recovery: on a failure the engine
    is rebuilt and the image retried at FALLBACK_LIMIT (scaled back, so the
    caller sees pixel coordinates of the image it passed). Raises OcrError
    when that fails too — never an empty reading, since "could not read" and
    "nothing there" must stay tellable apart."""
    import cv2

    with _lock:
        engine = _get_engine()
        if engine is None:
            return None
        try:
            return engine.ocr(img, cls=True)
        except Exception as first:
            log.warning("OCR engine failed on a %dx%d image (%s); rebuilding it and retrying at %dpx",
                        img.shape[1], img.shape[0], first, FALLBACK_LIMIT)
            _rebuild()
            engine = _get_engine()
            if engine is None:
                raise OcrError(f"{first} (and the engine could not be restarted)") from first
            scale = min(1.0, FALLBACK_LIMIT / max(img.shape[:2]))
            small = img if scale == 1.0 else cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
            try:
                result = engine.ocr(small, cls=True)
            except Exception as second:
                _rebuild()
                raise OcrError(str(second)) from second
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
    """Whether OCR can run at all (loads the engine on first call)."""
    with _lock:
        return _get_engine() is not None


def image_lines(pix) -> list[tuple[list, str, float]]:
    """Every line PaddleOCR finds in a fitz Pixmap (RGB, no alpha), as
    (four corner points in pixels, text, confidence). Raises OcrError when
    the engine cannot read it."""
    if not available():
        return []
    import numpy as np

    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n == 3:
        img = img[:, :, ::-1]  # RGB -> BGR, what PaddleOCR's cv2 pipeline expects
    out = []
    for block in _predict(np.ascontiguousarray(img)) or []:
        for item in block or []:
            if len(item) >= 2 and item[1]:
                out.append((item[0], str(item[1][0]), float(item[1][1])))
    return out
