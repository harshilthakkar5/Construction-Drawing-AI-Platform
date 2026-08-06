"""BullMQ consumer entrypoint.

Hosts four consumers:
- process-document (produced by apps/api): page-streaming pipeline. On
  completion it enqueues a scrape-region job for the new document when the
  project has a title-block region — and NOTHING else. Summaries are never
  generated automatically; see docs/region-based-classification.md.
- scrape-region: apply the project's region box to its pages, read the sheet
  numbers, rebuild the portions.
- summarize-portion: FR-10/12 for ONE discipline, because a user pressed its
  button.
- summarize-project: the project rollup, also user-triggered.

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
import scrape
import summarize
import telemetry
from contracts import (
    PROCESS_DOCUMENT_QUEUE,
    SCRAPE_REGION_QUEUE,
    SUMMARIZE_PORTION_QUEUE,
    SUMMARIZE_PROJECT_QUEUE,
    ProcessDocumentJob,
    ScrapeRegionJob,
    SummarizePortionJob,
    SummarizeProjectJob,
)

log = logutil.get("worker")

scrape_queue: Queue | None = None


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

        # Categorize the new document's pages with the project's existing box.
        # No summary job is queued here — summaries are user-approved.
        region = await asyncio.to_thread(db.get_sheet_region, payload.project_id)
        if region is None:
            log.info(
                "project %s has no title-block region — pages left unclassified until "
                "one is defined",
                payload.project_id[:8],
            )
        elif scrape_queue is not None:
            await scrape_queue.add(
                "scrape",
                {
                    "projectId": payload.project_id,
                    "regionVersion": region["version"],
                    "documentId": payload.document_id,
                },
            )
            log.info(
                "queued scrape-region for document %s (region v%d)",
                payload.document_id[:8],
                region["version"],
            )
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


async def scrape_region(job, job_token: str):
    payload = ScrapeRegionJob.from_payload(job.data)
    log.info(
        "scrape-region job %s: project=%s region=v%d document=%s",
        job.id,
        payload.project_id,
        payload.region_version,
        payload.document_id or "(all)",
    )
    try:
        with telemetry.observe_job(SCRAPE_REGION_QUEUE):
            result = await asyncio.to_thread(
                scrape.run,
                payload.project_id,
                payload.region_version,
                payload.document_id,
            )
        log.info("scrape-region job %s done: %s", job.id, result)
        return result
    except Exception:
        log.exception("scrape-region job %s FAILED for project %s", job.id, payload.project_id)
        raise


async def summarize_portion(job, job_token: str):
    payload = SummarizePortionJob.from_payload(job.data)
    log.info(
        "summarize-portion job %s: project=%s portion=%s",
        job.id,
        payload.project_id,
        payload.portion_id,
    )
    try:
        with telemetry.observe_job(SUMMARIZE_PORTION_QUEUE):
            result = await asyncio.to_thread(
                summarize.run_portion, payload.project_id, payload.portion_id
            )
        log.info("summarize-portion job %s done: %s", job.id, result)
        return result
    except Exception:
        log.exception(
            "summarize-portion job %s FAILED for portion %s", job.id, payload.portion_id
        )
        raise


async def summarize_project(job, job_token: str):
    payload = SummarizeProjectJob.from_payload(job.data)
    log.info("summarize-project job %s: project=%s", job.id, payload.project_id)
    try:
        with telemetry.observe_job(SUMMARIZE_PROJECT_QUEUE):
            result = await asyncio.to_thread(summarize.run_project, payload.project_id)
        log.info("summarize-project job %s done: %s", job.id, result)
        return result
    except Exception:
        log.exception("summarize-project job %s FAILED for project %s", job.id, payload.project_id)
        raise


async def main() -> None:
    global scrape_queue
    logutil.setup()
    telemetry.start()
    scrape_queue = Queue(SCRAPE_REGION_QUEUE, {"connection": config.REDIS_URL})

    queues = {
        PROCESS_DOCUMENT_QUEUE: process_document,
        SCRAPE_REGION_QUEUE: scrape_region,
        SUMMARIZE_PORTION_QUEUE: summarize_portion,
        SUMMARIZE_PROJECT_QUEUE: summarize_project,
    }
    workers = [
        Worker(name, handler, {"connection": config.REDIS_URL})
        for name, handler in queues.items()
    ]
    log.info("consuming %s via %s", ", ".join(queues), config.REDIS_URL)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    await stop.wait()
    log.info("shutting down…")
    for worker in workers:
        await worker.close()
    await scrape_queue.close()


if __name__ == "__main__":
    asyncio.run(main())
