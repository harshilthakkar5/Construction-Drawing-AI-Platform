import { randomUUID } from "node:crypto";
import { Router } from "express";
import { type SheetRegionDto } from "@cdip/shared";
import { z } from "zod";
import { currentUser } from "../auth.js";
import { prisma } from "../db.js";
import { scrapeRegionQueue } from "../queues.js";
import { redis } from "../redis.js";
import {
  DEFAULT_PREVIEW_SAMPLE,
  isPlausibleBox,
  regionPreviewSchema,
  regionSaveSchema,
} from "../region.js";

/**
 * The title-block region: one box per project, drawn once by the user and
 * applied to every page of every PDF to scrape sheet numbers
 * (docs/region-based-classification.md).
 *
 * The box is stored in RELATIVE coordinates (0-1 of the rendered page), which
 * is what makes it survive mixed page sizes and rotations. Scraping itself
 * never happens here — the API only queues jobs; PDF bytes are the worker's
 * business.
 */
export const regionRouter = Router({ mergeParams: true });

/**
 * Redis key a finished preview lands under — the worker writes it, this route
 * reads it. Key format shared with workers/src/cache.py `region_preview_key`;
 * keep the two in sync (same arrangement as summariesCacheKey).
 */
export const regionPreviewKey = (previewId: string) => `region:preview:${previewId}`;

const projectParam = z.object({ projectId: z.string().uuid() });

type RegionRow = NonNullable<Awaited<ReturnType<typeof prisma.sheetRegion.findUnique>>>;

function toDto(region: RegionRow): SheetRegionDto {
  return {
    relX: region.relX,
    relY: region.relY,
    relW: region.relW,
    relH: region.relH,
    sampleDocumentId: region.sampleDocumentId,
    samplePageNumber: region.samplePageNumber,
    version: region.version,
    scrapeStatus: region.scrapeStatus,
    scrapedPages: region.scrapedPages,
    totalPages: region.totalPages,
    notFoundPages: region.notFoundPages,
    lastError: region.lastError,
    lastScrapedAt: region.lastScrapedAt?.toISOString() ?? null,
    updatedAt: region.updatedAt.toISOString(),
  };
}

/** Live pages the scrape will cover — superseded revisions have left the set. */
function livePageCount(projectId: string) {
  return prisma.page.count({ where: { document: { projectId, supersededAt: null } } });
}

regionRouter.get("/", async (req, res) => {
  const { projectId } = projectParam.parse(req.params);
  await prisma.project.findUniqueOrThrow({ where: { id: projectId } });
  const region = await prisma.sheetRegion.findUnique({ where: { projectId } });
  if (!region) return void res.status(404).json({ error: "no region defined" });
  res.json(toDto(region));
});

/**
 * Create or replace the box. Bumping `version` is what invalidates every
 * page's stored scrape: the worker re-scrapes exactly the pages whose
 * `regionVersion` no longer matches.
 */
regionRouter.put("/", async (req, res) => {
  const { projectId } = projectParam.parse(req.params);
  const body = regionSaveSchema.parse(req.body);
  if (!isPlausibleBox(body)) {
    return void res
      .status(400)
      .json({ error: "region is too small to hold a title block — draw a larger box" });
  }
  await prisma.project.findUniqueOrThrow({ where: { id: projectId } });

  const totalPages = await livePageCount(projectId);
  const region = await prisma.sheetRegion.upsert({
    where: { projectId },
    create: {
      projectId,
      ...body,
      createdById: currentUser(req).id,
      scrapeStatus: "pending",
      totalPages,
    },
    update: {
      ...body,
      version: { increment: 1 },
      scrapeStatus: "pending",
      scrapedPages: 0,
      notFoundPages: 0,
      totalPages,
      lastError: null,
    },
  });

  const job = await scrapeRegionQueue.add("scrape", {
    projectId,
    regionVersion: region.version,
  });
  console.log(
    `[region] project ${projectId} saved box v${region.version}, scrape queued (job ${job.id})`,
  );
  res.status(202).json({ ...toDto(region), jobId: job.id });
});

/** Re-run the scrape without moving the box (new uploads, or after a failure). */
regionRouter.post("/rescrape", async (req, res) => {
  const { projectId } = projectParam.parse(req.params);
  const region = await prisma.sheetRegion.findUnique({ where: { projectId } });
  if (!region) return void res.status(404).json({ error: "no region defined" });
  if (region.scrapeStatus === "running") {
    return void res.status(409).json({ error: "a scrape is already running" });
  }

  const updated = await prisma.sheetRegion.update({
    where: { projectId },
    data: {
      scrapeStatus: "pending",
      scrapedPages: 0,
      notFoundPages: 0,
      totalPages: await livePageCount(projectId),
      lastError: null,
    },
  });
  const job = await scrapeRegionQueue.add("scrape", {
    projectId,
    regionVersion: updated.version,
  });
  res.status(202).json({ ...toDto(updated), jobId: job.id });
});

/**
 * Dry run: what does this box scrape on a few sample pages? Queued like every
 * other PDF operation — the response carries a previewId to poll, so a 1000
 * page project never blocks an HTTP worker on PyMuPDF.
 */
regionRouter.post("/preview", async (req, res) => {
  const { projectId } = projectParam.parse(req.params);
  const { sampleSize, ...box } = regionPreviewSchema.parse(req.body);
  await prisma.project.findUniqueOrThrow({ where: { id: projectId } });

  if ((await livePageCount(projectId)) === 0) {
    return void res.status(409).json({ error: "no processed pages to preview against yet" });
  }

  const previewId = randomUUID();
  const job = await scrapeRegionQueue.add("preview", {
    projectId,
    previewId,
    box,
    sampleSize: sampleSize ?? DEFAULT_PREVIEW_SAMPLE,
  });
  res.status(202).json({ previewId, jobId: job.id });
});

/** 202 while the preview job is still running, 200 with the rows once done. */
regionRouter.get("/preview/:previewId", async (req, res) => {
  const { previewId } = z.object({ previewId: z.string().uuid() }).parse(req.params);
  const stored = await redis.get(regionPreviewKey(previewId)).catch(() => null);
  if (!stored) return void res.status(202).json({ status: "pending" });
  res.json(JSON.parse(stored));
});

/**
 * Clear the box. Page disciplines and portions are left exactly as they are —
 * deleting the region should not silently un-categorize a project someone is
 * already working with.
 */
regionRouter.delete("/", async (req, res) => {
  const { projectId } = projectParam.parse(req.params);
  await prisma.sheetRegion.deleteMany({ where: { projectId } });
  res.status(204).end();
});
