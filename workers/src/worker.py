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

from bullmq import Queue, Worker

import config
import db
import logutil
import processing
import summarize
import telemetry
from contracts import (
    PROCESS_DOCUMENT_QUEUE,
    SUMMARIZE_PROJECT_QUEUE,
    ProcessDocumentJob,
    SummarizeProjectJob,
)

log = logutil.get("worker")

summarize_queue: Queue | None = None


async def process_document(job, job_token: str):
    payload = ProcessDocumentJob.from_payload(job.data)
    log.info(
        "process-document job %s attempt %d: project=%s document=%s",
        job.id,
        job.attemptsMade + 1,
        payload.project_id,
        payload.document_id,
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
        log.info("process-document job %s done: %s", job.id, result)
        if summarize_queue is not None:
            await summarize_queue.add(
                "summarize", {"projectId": payload.project_id}
            )
            log.info("queued summarize-project for project %s", payload.project_id[:8])
        return result
    except Exception:
        log.exception(
            "process-document job %s FAILED (attempt %d/5) for document %s — "
            "BullMQ will retry with backoff",
            job.id,
            job.attemptsMade + 1,
            payload.document_id,
        )
        try:
            # Skip if the document was deleted — status writes would fail too.
            if db.document_exists(payload.document_id):
                db.set_document_status(payload.document_id, "failed")
        except Exception:
            log.exception("could not mark document %s as failed", payload.document_id)
        raise


async def summarize_project(job, job_token: str):
    payload = SummarizeProjectJob.from_payload(job.data)
    log.info("summarize-project job %s: project=%s", job.id, payload.project_id)
    try:
        with telemetry.observe_job(SUMMARIZE_PROJECT_QUEUE):
            result = await asyncio.to_thread(summarize.run, payload.project_id)
        log.info("summarize-project job %s done: %s", job.id, result)
        return result
    except Exception:
        log.exception("summarize-project job %s FAILED for project %s", job.id, payload.project_id)
        raise


async def main() -> None:
    global summarize_queue
    logutil.setup()
    telemetry.start()
    summarize_queue = Queue(SUMMARIZE_PROJECT_QUEUE, {"connection": config.REDIS_URL})

    workers = [
        Worker(PROCESS_DOCUMENT_QUEUE, process_document, {"connection": config.REDIS_URL}),
        Worker(SUMMARIZE_PROJECT_QUEUE, summarize_project, {"connection": config.REDIS_URL}),
    ]
    log.info(
        "consuming '%s' and '%s' via %s", PROCESS_DOCUMENT_QUEUE, SUMMARIZE_PROJECT_QUEUE, config.REDIS_URL
    )

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    await stop.wait()
    log.info("shutting down…")
    for worker in workers:
        await worker.close()
    await summarize_queue.close()


if __name__ == "__main__":
    asyncio.run(main())
