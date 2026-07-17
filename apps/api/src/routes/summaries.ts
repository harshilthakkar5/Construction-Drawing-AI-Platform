import { Router } from "express";
import { z } from "zod";
import { prisma } from "../db.js";
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
