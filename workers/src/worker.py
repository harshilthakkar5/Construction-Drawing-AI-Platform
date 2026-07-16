"""BullMQ consumer entrypoint.

Consumes process-document jobs produced by apps/api. Retry policy (attempts,
exponential backoff) is set by the producer (apps/api/src/queues.ts); raising
here triggers the retry. FR-9 status flow: a job marks the document
'processing' on start, 'completed' on success, 'failed' on any failure — a
retry flips it back to 'processing' when it restarts.
"""

import asyncio
import signal
import traceback

from bullmq import Worker

import config
import db
import processing
from contracts import PROCESS_DOCUMENT_QUEUE, ProcessDocumentJob


async def process_document(job, job_token: str):
    payload = ProcessDocumentJob.from_payload(job.data)
    print(
        f"[worker] job {job.id} attempt {job.attemptsMade + 1}: "
        f"project={payload.project_id} document={payload.document_id}"
    )
    try:
        # The pipeline is synchronous (CPU/IO via PyMuPDF, boto3, psycopg);
        # run it off the event loop so the queue connection stays alive.
        result = await asyncio.to_thread(
            processing.process_document,
            payload.project_id,
            payload.document_id,
            payload.spaces_key,
        )
        print(f"[worker] job {job.id} done: {result}")
        return result
    except Exception:
        traceback.print_exc()
        try:
            db.set_document_status(payload.document_id, "failed")
        except Exception:
            traceback.print_exc()
        raise


async def main() -> None:
    worker = Worker(
        PROCESS_DOCUMENT_QUEUE,
        process_document,
        {"connection": config.REDIS_URL},
    )
    print(f"[worker] consuming '{PROCESS_DOCUMENT_QUEUE}' via {config.REDIS_URL}")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    await stop.wait()
    print("[worker] shutting down…")
    await worker.close()


if __name__ == "__main__":
    asyncio.run(main())
