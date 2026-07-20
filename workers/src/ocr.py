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

        _engine = PaddleOCR(use_angle_cls=True, lang="en", show_log=False)
    except Exception as exc:  # ImportError or model download failure
        log.warning("PaddleOCR unavailable, OCR disabled: %s", exc)
        _unavailable = True
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
