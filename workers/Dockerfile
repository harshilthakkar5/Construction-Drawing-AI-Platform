FROM python:3.11-slim

WORKDIR /app

# OpenCV/Paddle runtime libraries
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# What "installed correctly" means, in one place so the check and its retry
# cannot disagree. Every native or heavy dependency is named — those are the
# ones that break — plus requests, which nothing here imports directly but
# paddleocr and google-genai both need.
ARG PY_IMPORT_CHECK="import requests, paddleocr, paddle, cv2, fitz, pdfplumber, boto3, psycopg, psycopg_pool, PIL, anthropic, httpx, dotenv, bullmq, redis, prometheus_client; import google.genai"

# Install AND verify in ONE layer, with one self-healing retry.
#
# THE SINGLE LAYER IS THE POINT. A flaky network or a bad disk can leave a
# package installed but unreadable — the symptom is an import dying inside a
# dependency, e.g.
#   zlib.error: Error -2 while decompressing data: inconsistent stream state
# from `import requests` by way of charset_normalizer. Verifying that in a
# LATER RUN cannot fix it: this layer succeeded, so Docker caches it, and every
# rebuild replays the same corrupt layer and fails the check again without pip
# ever running. The check has to live where pip lives, so that a failure
# invalidates the install itself and a rebuild genuinely re-downloads.
#
# The retry is `--force-reinstall`, not a bare repeat: pip considers an already
# present package satisfied and would do nothing at all.
RUN pip install --no-cache-dir -r requirements.txt \
 && ( python -c "$PY_IMPORT_CHECK" \
      || ( echo "a package installed but does not import — reinstalling once" >&2 \
           && pip install --no-cache-dir --force-reinstall -r requirements.txt \
           && python -c "$PY_IMPORT_CHECK" ) )

# Bake the OCR models in (~15 MB). Without this the first page that
# needs OCR downloads them from a CDN that is slow and drop-prone from some
# regions — mid-job, inside a queue worker, the worst possible moment. THIS one
# is best-effort: a failure is someone else's CDN having a bad minute, and the
# runtime download remains as the fallback.
RUN python -c "from paddleocr import PaddleOCR; PaddleOCR(use_angle_cls=True, lang='en', show_log=False)" \
    || echo "WARNING: OCR model pre-download failed; the worker will fetch them at first use"

COPY src/ src/

CMD ["python", "src/worker.py"]
