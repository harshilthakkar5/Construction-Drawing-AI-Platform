FROM python:3.11-slim

WORKDIR /app

# OpenCV/Paddle runtime libraries
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Bake the OCR models into the image (~15 MB). Without this the first page
# that needs OCR downloads them from a CDN that is slow and drop-prone from
# some regions — mid-job, inside a queue worker, which is the worst possible
# moment. Best-effort: a failure here leaves the runtime download as the
# fallback rather than breaking the build over someone else's CDN.
RUN python -c "from paddleocr import PaddleOCR; PaddleOCR(use_angle_cls=True, lang='en', show_log=False)" \
    || echo "WARNING: OCR model pre-download failed; the worker will fetch them at first use"

COPY src/ src/

CMD ["python", "src/worker.py"]
