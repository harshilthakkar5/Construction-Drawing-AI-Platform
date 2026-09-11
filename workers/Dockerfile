FROM python:3.11-slim

WORKDIR /app

# OpenCV/Paddle runtime libraries
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Two checks, and the split matters: one is allowed to fail and one is not.
#
# FIRST, that paddleocr IMPORTS. A build that gets this far has already run
# `pip install`, so a failure here is a corrupt install — a truncated wheel, a
# bad layer in the build cache, a full disk — not a missing package. Left
# tolerated, it ships an image whose OCR is dead: workers/src/ocr.py catches
# the error per page and carries on, so nothing crashes and nothing is loud,
# while every scanned sheet silently yields no text AND no sheet number. That
# is worse than a failed build, so this one exits.
RUN python -c "import paddleocr" \
 || { echo "paddleocr could not be imported. The install in this image is corrupt, not merely absent." >&2; \
      echo "Rebuild clean: docker builder prune -f && docker build --no-cache ./workers" >&2; \
      exit 1; }

# SECOND, bake the OCR models in (~15 MB). Without this the first page that
# needs OCR downloads them from a CDN that is slow and drop-prone from some
# regions — mid-job, inside a queue worker, the worst possible moment. THIS one
# is best-effort: a failure is someone else's CDN having a bad minute, and the
# runtime download remains as the fallback.
RUN python -c "from paddleocr import PaddleOCR; PaddleOCR(use_angle_cls=True, lang='en', show_log=False)" \
    || echo "WARNING: OCR model pre-download failed; the worker will fetch them at first use"

COPY src/ src/

CMD ["python", "src/worker.py"]
