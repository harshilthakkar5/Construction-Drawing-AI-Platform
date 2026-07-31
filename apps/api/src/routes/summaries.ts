import { Router } from "express";
import { z } from "zod";
import { prisma } from "../db.js";
import { summarizeProjectQueue } from "../queues.js";
import { redis } from "../redis.js";

/**
 * Hierarchical summaries (FR-10..13) are written by the worker's
 * summarize-project job; the API only reads them. Levels: page (bulk,
 * incremental), section, portion, project.
 *
 * Phase 5: the full per-project summary list is cached in Redis under one key
 * (level filtering happens in-process). The worker DELs the key when a
 * summarize run rewrites summaries; the TTL is only a backstop.
 */
export const summariesRouter = Router({ mergeParams: true });

const paramsSchema = z.object({ projectId: z.string().uuid() });
const querySchema = z.object({
  level: z.enum(["page", "section", "portion", "project"]).optional(),
});

const CACHE_TTL_SECONDS = 300;
/** Key format shared with workers/src/cache.py — keep in sync. */
export const summariesCacheKey = (projectId: string) => `cache:summaries:${projectId}`;

interface SummaryRow {
  id: string;
  projectId: string;
  portionId: string | null;
  level: "page" | "section" | "portion" | "project";
  summary: unknown;
  sources: unknown;
}

summariesRouter.get("/", async (req, res) => {
  const { projectId } = paramsSchema.parse(req.params);
  const { level } = querySchema.parse(req.query);
  await prisma.project.findUniqueOrThrow({ where: { id: projectId } });

  const key = summariesCacheKey(projectId);
  let summaries: SummaryRow[] | null = null;
  const cached = await redis.get(key).catch(() => null);
  if (cached) {
    summaries = JSON.parse(cached) as SummaryRow[];
  } else {
    summaries = (await prisma.summary.findMany({ where: { projectId } })) as SummaryRow[];
    await redis.set(key, JSON.stringify(summaries), "EX", CACHE_TTL_SECONDS).catch(() => {});
  }

  res.json(level ? summaries.filter((s) => s.level === level) : summaries);
});

/**
 * Why is there no summary? Reports what each stage produced, so a missing
 * project summary can be traced without reading worker logs:
 *  - pagesWithChunks 0 → extraction/chunking produced nothing to summarize
 *  - page 0 but pagesWithChunks > 0 → the summarize job never ran, or exited
 *    early (SUMMARIES_ENABLED=false / ANTHROPIC_API_KEY unset on the worker)
 *  - page > 0 but portion/project 0 → the rollup tier failed
 */
summariesRouter.get("/status", async (req, res) => {
  const { projectId } = paramsSchema.parse(req.params);
  await prisma.project.findUniqueOrThrow({ where: { id: projectId } });

  const [grouped, portions, documents, pagesWithChunks] = await Promise.all([
    prisma.summary.groupBy({ by: ["level"], where: { projectId }, _count: true }),
    prisma.portion.count({ where: { projectId } }),
    prisma.document.groupBy({
      by: ["status"],
      where: { projectId, supersededAt: null },
      _count: true,
    }),
    prisma.page.count({
      where: { document: { projectId, supersededAt: null }, chunks: { some: {} } },
    }),
  ]);

  const counts = { page: 0, section: 0, portion: 0, project: 0 };
  for (const row of grouped) counts[row.level] = row._count;

  res.json({
    summaries: counts,
    portions,
    pagesWithChunks,
    documents: Object.fromEntries(documents.map((d) => [d.status, d._count])),
    hint:
      pagesWithChunks === 0
        ? "No page has chunks yet — processing has not produced text (check document status / worker logs)."
        : counts.page === 0
          ? "Pages have chunks but no page summaries: the summarize job did not run or exited early (SUMMARIES_ENABLED=false, or ANTHROPIC_API_KEY missing on the worker). POST /summaries/rebuild to re-run."
          : counts.project === 0
            ? "Page summaries exist but the rollups are missing — re-run with POST /summaries/rebuild and check the worker log."
            : "ok",
  });
});

/** Re-run the summarize job for this project without re-uploading anything. */
summariesRouter.post("/rebuild", async (req, res) => {
  const { projectId } = paramsSchema.parse(req.params);
  await prisma.project.findUniqueOrThrow({ where: { id: projectId } });
  await redis.del(summariesCacheKey(projectId)).catch(() => {});
  const job = await summarizeProjectQueue.add("summarize", { projectId });
  console.log(`[summaries] rebuild queued for project ${projectId} (job ${job.id})`);
  res.status(202).json({ queued: true, jobId: job.id });
});
