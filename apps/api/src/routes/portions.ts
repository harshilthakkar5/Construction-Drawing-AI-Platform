import { Router } from "express";
import type { PortionDto } from "@cdip/shared";
import { z } from "zod";
import { currentUser } from "../auth.js";
import { prisma } from "../db.js";
import { summarizePortionQueue } from "../queues.js";
import { redis } from "../redis.js";
import { summariesCacheKey } from "./summaries.js";

/**
 * Portions are the discipline categories: one row per discipline, written by
 * the worker's scrape-region job. The API reads them, and owns the one write
 * that starts from a user action — pressing "Generate summary".
 *
 * Nothing is summarized until that button is pressed (FR-10/12 on demand), so
 * summaryStatus is part of the list payload and the UI polls it.
 */
export const portionsRouter = Router({ mergeParams: true });

const projectParam = z.object({ projectId: z.string().uuid() });
const portionParams = projectParam.extend({ portionId: z.string().uuid() });

/** Statuses where a job is already in flight — pressing again is a no-op. */
const IN_FLIGHT = ["queued", "running"] as const;

portionsRouter.get("/", async (req, res) => {
  const { projectId } = projectParam.parse(req.params);
  await prisma.project.findUniqueOrThrow({ where: { id: projectId } });
  const portions = await prisma.portion.findMany({
    where: { projectId },
    orderBy: { startPage: "asc" },
  });

  // One real sheet number per discipline, so a category can be labelled with
  // what the user would read off the sheet ("Structural — S-003.0").
  const samples = await prisma.page.findMany({
    where: {
      document: { projectId, supersededAt: null },
      discipline: { in: portions.map((p) => p.discipline).filter((d): d is string => d !== null) },
      sheetNumber: { not: null },
    },
    select: { discipline: true, sheetNumber: true },
    orderBy: { combinedPageNumber: "asc" },
  });
  const sampleByDiscipline = new Map<string, string>();
  for (const page of samples) {
    if (page.discipline && !sampleByDiscipline.has(page.discipline)) {
      sampleByDiscipline.set(page.discipline, page.sheetNumber as string);
    }
  }

  const dtos: PortionDto[] = portions.map((portion) => ({
    id: portion.id,
    projectId: portion.projectId,
    name: portion.name,
    discipline: portion.discipline as PortionDto["discipline"],
    startPage: portion.startPage,
    endPage: portion.endPage,
    pageCount: portion.pageCount,
    summary: portion.summary,
    summaryStatus: portion.summaryStatus,
    summaryRequestedAt: portion.summaryRequestedAt?.toISOString() ?? null,
    summaryCompletedAt: portion.summaryCompletedAt?.toISOString() ?? null,
    summaryError: portion.summaryError,
    sheetNumberSample: portion.discipline
      ? (sampleByDiscipline.get(portion.discipline) ?? null)
      : null,
  }));
  res.json(dtos);
});

/**
 * The button. Summaries cost real tokens, so this is the only thing that
 * starts a summary run for a discipline — and it refuses to stack jobs.
 */
portionsRouter.post("/:portionId/summarize", async (req, res) => {
  const { projectId, portionId } = portionParams.parse(req.params);
  const portion = await prisma.portion.findUniqueOrThrow({
    where: { id: portionId, projectId },
  });
  if ((IN_FLIGHT as readonly string[]).includes(portion.summaryStatus)) {
    return void res
      .status(409)
      .json({ error: `a summary is already ${portion.summaryStatus} for this portion` });
  }
  if (portion.pageCount === 0) {
    return void res.status(409).json({ error: "this portion has no pages to summarize" });
  }

  const job = await summarizePortionQueue.add("summarize", {
    projectId,
    portionId,
    requestedById: currentUser(req).id,
  });
  const updated = await prisma.portion.update({
    where: { id: portionId },
    data: {
      summaryStatus: "queued",
      summaryJobId: job.id ?? null,
      summaryRequestedById: currentUser(req).id,
      summaryRequestedAt: new Date(),
      summaryError: null,
    },
  });
  await redis.del(summariesCacheKey(projectId)).catch(() => {});
  console.log(`[portions] summary queued for ${portion.name} (job ${job.id})`);
  res.status(202).json({ queued: true, jobId: job.id, summaryStatus: updated.summaryStatus });
});

/** This portion's own rollups — its portion summary plus its sections. */
portionsRouter.get("/:portionId/summary", async (req, res) => {
  const { projectId, portionId } = portionParams.parse(req.params);
  await prisma.portion.findUniqueOrThrow({ where: { id: portionId, projectId } });
  const summaries = await prisma.summary.findMany({
    where: { portionId, level: { in: ["portion", "section"] } },
  });
  res.json(summaries);
});
