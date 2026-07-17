import { Router } from "express";
import { z } from "zod";
import { prisma } from "../db.js";

/**
 * Hierarchical summaries (FR-10..13) are written by the worker's
 * summarize-project job; the API only reads them. Levels: page (bulk,
 * incremental), section, portion, project.
 */
export const summariesRouter = Router({ mergeParams: true });

const paramsSchema = z.object({ projectId: z.string().uuid() });
const querySchema = z.object({
  level: z.enum(["page", "section", "portion", "project"]).optional(),
});

summariesRouter.get("/", async (req, res) => {
  const { projectId } = paramsSchema.parse(req.params);
  const { level } = querySchema.parse(req.query);
  await prisma.project.findUniqueOrThrow({ where: { id: projectId } });
  const summaries = await prisma.summary.findMany({
    where: { projectId, ...(level ? { level } : {}) },
  });
  res.json(summaries);
});
