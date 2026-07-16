import { Router } from "express";
import { z } from "zod";
import { prisma } from "../db.js";

/**
 * Portions are written by the worker's detection pass (workers/src/portions.py)
 * after each document finishes processing; the API only reads them.
 */
export const portionsRouter = Router({ mergeParams: true });

const projectParam = z.object({ projectId: z.string().uuid() });

portionsRouter.get("/", async (req, res) => {
  const { projectId } = projectParam.parse(req.params);
  await prisma.project.findUniqueOrThrow({ where: { id: projectId } });
  const portions = await prisma.portion.findMany({
    where: { projectId },
    orderBy: { startPage: "asc" },
  });
  res.json(portions);
});
