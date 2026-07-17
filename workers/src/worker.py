"""BullMQ consumer entrypoint.

Hosts two consumers:
- process-document (produced by apps/api): page-streaming pipeline; on
  completion it enqueues a summarize-project job.
- summarize-project: bottom-up hierarchical summaries (FR-10..12).

Retry policy for process-document is set by the producer
(apps/api/src/queues.ts); raising here triggers the retry. FR-9 status flow:
'processing' on start, 'completed' on success, 'failed' on failure.
"""

import asyncio
import signal
import traceback

from bullmq import Queue, Worker

import config
import db
import processing
import summarize
import telemetry
from contracts import (
    PROCESS_DOCUMENT_QUEUE,
    SUMMARIZE_PROJECT_QUEUE,
    ProcessDocumentJob,
    SummarizeProjectJob,
)

summarize_queue: Queue | None = None


async def process_document(job, job_token: str):
    payload = ProcessDocumentJob.from_payload(job.data)
    print(
        f"[worker] job {job.id} attempt {job.attemptsMade + 1}: "
        f"project={payload.project_id} document={payload.document_id}"
    )
    try:
        # The pipeline is synchronous (CPU/IO via PyMuPDF, boto3, psycopg);
        # run it off the event loop so the queue connection stays alive.
        with telemetry.observe_job(PROCESS_DOCUMENT_QUEUE):
            result = await asyncio.to_thread(
                processing.process_document,
                payload.project_id,
                payload.document_id,
                payload.spaces_key,
            )
        print(f"[worker] job {job.id} done: {result}")
        if summarize_queue is not None:
            await summarize_queue.add(
                "summarize", {"projectId": payload.project_id}
            )
        return result
    except Exception:
        traceback.print_exc()
        try:
            db.set_document_status(payload.document_id, "failed")
        except Exception:
            traceback.print_exc()
        raise


async def summarize_project(job, job_token: str):
    payload = SummarizeProjectJob.from_payload(job.data)
    print(f"[worker] summarize job {job.id}: project={payload.project_id}")
    try:
        with telemetry.observe_job(SUMMARIZE_PROJECT_QUEUE):
            return await asyncio.to_thread(summarize.run, payload.project_id)
    except Exception:
        traceback.print_exc()
        raise


async def main() -> None:
    global summarize_queue
    telemetry.start()
    summarize_queue = Queue(SUMMARIZE_PROJECT_QUEUE, {"connection": config.REDIS_URL})

    workers = [
        Worker(PROCESS_DOCUMENT_QUEUE, process_document, {"connection": config.REDIS_URL}),
        Worker(SUMMARIZE_PROJECT_QUEUE, summarize_project, {"connection": config.REDIS_URL}),
    ]
    print(
        f"[worker] consuming '{PROCESS_DOCUMENT_QUEUE}' and "
        f"'{SUMMARIZE_PROJECT_QUEUE}' via {config.REDIS_URL}"
    )

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    await stop.wait()
    print("[worker] shutting down…")
    for worker in workers:
        await worker.close()
    await summarize_queue.close()


if __name__ == "__main__":
    asyncio.run(main())
