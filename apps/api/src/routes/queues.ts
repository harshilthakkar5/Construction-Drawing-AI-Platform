import { Router } from "express";
import { z } from "zod";
import { prisma } from "../db.js";
import { processDocumentQueue, summarizeProjectQueue } from "../queues.js";

/**
 * Queue operations (Phase 5 ops surface). Lets you inspect and unstick the
 * BullMQ queues without touching Redis by hand — e.g. after documents or
 * projects were deleted while their jobs were still queued.
 *
 * Authenticated (mounted below requireAuth) but not project-scoped: these are
 * whole-instance operations.
 */
export const queuesRouter = Router();

const QUEUES = {
  "process-document": processDocumentQueue,
  "summarize-project": summarizeProjectQueue,
} as const;

type QueueName = keyof typeof QUEUES;

const queueParam = z.object({ name: z.enum(["process-document", "summarize-project"]) });

/** GET /queues — job counts per queue (waiting/active/failed/delayed/completed). */
queuesRouter.get("/", async (_req, res) => {
  const counts = await Promise.all(
    (Object.keys(QUEUES) as QueueName[]).map(async (name) => ({
      name,
      counts: await QUEUES[name].getJobCounts(),
    })),
  );
  res.json(counts);
});

/** GET /queues/:name/failed — recent failed jobs with their error, so you can
 * see WHY something failed without scraping worker logs. */
queuesRouter.get("/:name/failed", async (req, res) => {
  const { name } = queueParam.parse(req.params);
  const { limit } = z.object({ limit: z.coerce.number().int().min(1).max(200).default(50) }).parse(
    req.query,
  );
  const jobs = await QUEUES[name].getFailed(0, limit - 1);
  res.json(
    jobs.map((job) => ({
      id: job.id,
      data: job.data,
      attemptsMade: job.attemptsMade,
      failedReason: job.failedReason,
      timestamp: job.timestamp,
    })),
  );
});

/** POST /queues/:name/retry-failed — re-queue every failed job (e.g. after
 * fixing the cause). Returns how many were retried. */
queuesRouter.post("/:name/retry-failed", async (req, res) => {
  const { name } = queueParam.parse(req.params);
  const jobs = await QUEUES[name].getFailed(0, 999);
  let retried = 0;
  for (const job of jobs) {
    await job.retry().then(
      () => retried++,
      () => {},
    );
  }
  console.log(`[queues] retried ${retried} failed jobs on ${name}`);
  res.json({ queue: name, retried });
});

/**
 * POST /queues/process-document/purge-orphaned — remove queued/failed jobs
 * whose document no longer exists (deleted while queued). These can never
 * succeed; this is the cure for a worker log looping on foreign-key
 * violations. Also purges summarize jobs for deleted projects.
 */
queuesRouter.post("/purge-orphaned", async (_req, res) => {
  let removed = 0;
  const checked = { documents: 0, projects: 0 };

  const docJobs = [
    ...(await processDocumentQueue.getFailed(0, 999)),
    ...(await processDocumentQueue.getWaiting(0, 999)),
    ...(await processDocumentQueue.getDelayed(0, 999)),
  ];
  for (const job of docJobs) {
    const documentId = (job.data as { documentId?: string }).documentId;
    if (!documentId) continue;
    checked.documents++;
    const exists = await prisma.document.findUnique({
      where: { id: documentId },
      select: { id: true },
    });
    if (!exists) {
      await job.remove().then(
        () => removed++,
        () => {},
      );
    }
  }

  const summaryJobs = [
    ...(await summarizeProjectQueue.getFailed(0, 999)),
    ...(await summarizeProjectQueue.getWaiting(0, 999)),
    ...(await summarizeProjectQueue.getDelayed(0, 999)),
  ];
  for (const job of summaryJobs) {
    const projectId = (job.data as { projectId?: string }).projectId;
    if (!projectId) continue;
    checked.projects++;
    const exists = await prisma.project.findUnique({
      where: { id: projectId },
      select: { id: true },
    });
    if (!exists) {
      await job.remove().then(
        () => removed++,
        () => {},
      );
    }
  }

  console.log(`[queues] purged ${removed} orphaned jobs`);
  res.json({ removed, checked });
});

/** DELETE /queues/:name/failed — drop all failed jobs (give up on them). */
queuesRouter.delete("/:name/failed", async (req, res) => {
  const { name } = queueParam.parse(req.params);
  const removed = await QUEUES[name].clean(0, 1000, "failed");
  console.log(`[queues] removed ${removed.length} failed jobs from ${name}`);
  res.json({ queue: name, removed: removed.length });
});
