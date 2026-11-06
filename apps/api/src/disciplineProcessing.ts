import { QueueEvents } from "bullmq";
import { QUEUES, UNSORTED, type ProcessingStateDto } from "@cdip/shared";
import { prisma } from "./db.js";
import { processDocumentQueue, scrapeRegionQueue } from "./queues.js";
import { redis } from "./redis.js";
import { chosenAfter } from "./disciplineProcessingRules.js";

/**
 * Discipline-first processing, chosen per project. An upload gets the quick pass only; the full pass
 * (OCR, chunks, search index) runs for the disciplines in
 * `projects.processDisciplines`, or for everything when `processAll`.
 * The worker decides page by page (workers/src/db.py `is_due`); this file only
 * records the choice and queues the documents that have work.
 */

export async function processingState(projectId: string): Promise<ProcessingStateDto> {
  const project = await prisma.project.findUniqueOrThrow({
    where: { id: projectId },
    select: { processAll: true, processDisciplines: true },
  });
  const rows = await prisma.$queryRaw<{ discipline: string; pages: number; processed: number }[]>`
    SELECT COALESCE(p.discipline, ${UNSORTED}) AS discipline,
           count(*)::int AS pages,
           count(*) FILTER (WHERE p."fullyProcessed")::int AS processed
      FROM pages p JOIN documents d ON d.id = p."documentId"
     WHERE d."projectId" = ${projectId} AND d."supersededAt" IS NULL
     GROUP BY 1 ORDER BY 1`;
  const running = await prisma.document.count({
    where: { projectId, supersededAt: null, status: { in: ["uploaded", "processing"] } },
  });
  const chosen = new Set(project.processDisciplines);
  return {
    processAll: project.processAll,
    disciplines: rows.map((r) => ({ ...r, chosen: project.processAll || chosen.has(r.discipline) })),
    running: running > 0,
  };
}

/** Add disciplines to the project's choice and queue the documents that now have work. */
export async function chooseDisciplines(projectId: string, asked: string[], all: boolean): Promise<number> {
  const state = await processingState(projectId);
  const project = await prisma.project.findUniqueOrThrow({
    where: { id: projectId },
    select: { processDisciplines: true },
  });
  const next = chosenAfter(project.processDisciplines, asked, state.disciplines.map((d) => d.discipline), all);
  await prisma.project.update({ where: { id: projectId }, data: { processDisciplines: next } });
  return queueDueProcessing(projectId);
}

/** Queue every live document with a page whose discipline is chosen and not yet
 * processed. A document already waiting is not queued twice; one already
 * running re-checks its pages before it finishes (processing._page_modes). */
export async function queueDueProcessing(projectId: string): Promise<number> {
  const documents = await prisma.$queryRaw<{ id: string; spacesKey: string }[]>`
    SELECT DISTINCT d.id, d."spacesKey"
      FROM documents d
      JOIN pages p ON p."documentId" = d.id
      JOIN projects pr ON pr.id = d."projectId"
     WHERE d."projectId" = ${projectId} AND d."supersededAt" IS NULL
       AND NOT p."fullyProcessed"
       AND (pr."processAll" OR COALESCE(p.discipline, ${UNSORTED}) = ANY(pr."processDisciplines"))`;
  for (const document of documents) {
    await processDocumentQueue.add(
      "process",
      { projectId, documentId: document.id, spacesKey: document.spacesKey },
      { deduplication: { id: `process:${document.id}` } },
    );
  }
  if (documents.length) {
    console.log(`[processing] queued ${documents.length} document(s) of project ${projectId} for the full pass`);
  }
  return documents.length;
}

/**
 * When a title-block read finishes, pages now have disciplines: queue the ones
 * that belong to a chosen discipline (a new upload, a redrawn box, a page
 * moved by hand). One listener per API process, like listenForFinishedScans;
 * a duplicate queue add is dropped by the deduplication id.
 */
export function listenForSortedPages(): QueueEvents | null {
  try {
    const events = new QueueEvents(QUEUES.scrapeRegion, { connection: redis.duplicate() });
    events.on("completed", ({ jobId }) => {
      void (async () => {
        const job = await scrapeRegionQueue.getJob(jobId);
        if (job?.name !== "scrape") return; // the preview dry run sorts nothing
        await queueDueProcessing(job.data.projectId);
      })().catch((err) => console.warn(`[processing] after title-block read: ${(err as Error).message}`));
    });
    events.on("error", (err) => console.warn(`[processing] listener: ${err.message}`));
    return events;
  } catch (err) {
    console.warn(`[processing] listener not started: ${(err as Error).message}`);
    return null;
  }
}
