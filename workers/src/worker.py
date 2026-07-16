"""BullMQ consumer entrypoint.

Consumes jobs produced by apps/api on the process-document queue.
Scaffold only: the processor logs the job and returns — the real pipeline
(stream pages with PyMuPDF → OCR fallback → extract → chunk → embed) comes later.
Retry/backoff policy lives on the producer side (apps/api/src/queues.ts), so a
raised exception here is enough to trigger a retry with exponential backoff.
"""

import asyncio
import os
import signal

from bullmq import Worker
from dotenv import load_dotenv

from contracts import PROCESS_DOCUMENT_QUEUE, ProcessDocumentJob

load_dotenv()


async def process_document(job, job_token: str):
    payload = ProcessDocumentJob.from_payload(job.data)
    print(
        f"[worker] received job {job.id}: project={payload.project_id} "
        f"document={payload.document_id} key={payload.spaces_key}"
    )
    # Placeholder: page-streaming pipeline lands here. Partial results must be
    # persisted per page so a failure at page N preserves pages 1..N-1.
    return {"ok": True}


async def main() -> None:
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    worker = Worker(
        PROCESS_DOCUMENT_QUEUE,
        process_document,
        {"connection": redis_url},
    )
    print(f"[worker] consuming '{PROCESS_DOCUMENT_QUEUE}' via {redis_url}")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    await stop.wait()
    print("[worker] shutting down…")
    await worker.close()


if __name__ == "__main__":
    asyncio.run(main())
