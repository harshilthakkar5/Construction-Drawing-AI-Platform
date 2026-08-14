"""PaddleOCR wrapper (FR-7): run only on pages with no text layer.

PaddleOCR is a heavy optional dependency; if it isn't installed (or
OCR_ENABLED=false), pages without a text layer keep empty text and a
warning is logged instead of failing the whole document.
"""

import config
import logutil

log = logutil.get("ocr")

_engine = None
_unavailable = False


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
        _engine = PaddleOCR(use_angle_cls=True, lang="en", show_log=False)
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


def ocr_png_bytes(png: bytes) -> str:
    engine = _get_engine()
    if engine is None:
        return ""
    import cv2
    import numpy as np

    img = cv2.imdecode(np.frombuffer(png, dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return ""
    result = engine.ocr(img, cls=True)
    lines: list[str] = []
    for block in result or []:
        for item in block or []:
            # item = [box, (text, confidence)]
            if len(item) >= 2 and item[1]:
                lines.append(str(item[1][0]))
    return "\n".join(lines)
