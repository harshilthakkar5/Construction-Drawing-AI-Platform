import type { Prisma } from "@prisma/client";
import { Router } from "express";
import { z } from "zod";
import type { RfiPackageDto, RfiPackageItemRef, RfiPackageStatus } from "@cdip/shared";
import { currentUser } from "../auth.js";
import { prisma } from "../db.js";
import { rfiPackageQueue } from "../queues.js";
import { presignGetObject } from "../s3.js";

/**
 * Marked-up RFI packages: the output the team actually sends — a cover form
 * per RFI and the drawing sheets with red clouds, callouts and leaders as PDF
 * annotations (workers/src/rfi_package.py).
 *
 * The API only records what to render and hands out the download link: the
 * worker opens the original PDFs, never this process (a drawing set can be a
 * gigabyte, and no file is processed inside an HTTP request).
 */
export const rfiPackagesRouter = Router({ mergeParams: true });

const projectParam = z.object({ projectId: z.string().uuid() });
const packageParams = projectParam.extend({ packageId: z.string().uuid() });

/** The most a package holds — a review's worth of findings, not a whole log. */
export const MAX_PACKAGE_ITEMS = 50;

const createBody = z
  .object({
    items: z
      .array(z.object({ type: z.enum(["rfi", "candidate"]), id: z.string().uuid() }))
      .max(MAX_PACKAGE_ITEMS)
      .default([]),
    /** Every finding of one targeted review, strongest first. */
    reviewRunId: z.string().uuid().optional(),
  })
  .refine((b) => b.items.length > 0 || b.reviewRunId, { message: "name at least one RFI or finding, or a review" });

type PackageRow = Awaited<ReturnType<typeof prisma.rfiPackage.findFirstOrThrow>>;

/** A package presumed dead: queued or running with nothing written for this
 * long. Rendering a sheet takes seconds; this is a worker that stopped. */
export const STALE_PACKAGE_MS = 10 * 60 * 1000;

export async function toPackageDto(row: PackageRow, now = new Date()): Promise<RfiPackageDto> {
  let status = row.status as RfiPackageStatus;
  let error = row.error;
  if ((status === "queued" || status === "running") && now.getTime() - row.createdAt.getTime() > STALE_PACKAGE_MS) {
    status = "failed";
    error = error ?? "the worker did not finish this package; request it again";
  }
  const notes = Array.isArray(row.notes) ? row.notes.filter((n): n is string => typeof n === "string") : [];
  return {
    id: row.id,
    status,
    items: (Array.isArray(row.items) ? row.items : []) as unknown as RfiPackageItemRef[],
    pages: row.pages,
    notes,
    error,
    downloadUrl:
      status === "ready" && row.key
        ? await presignGetObject(row.key, 3600, `rfi-package-${row.id.slice(0, 8)}.pdf`)
        : null,
    createdAt: row.createdAt.toISOString(),
    finishedAt: row.finishedAt?.toISOString() ?? null,
  };
}

/**
 * Ask for a package. Every item must belong to THIS project — checked here,
 * before anything is queued, so a member of one project cannot render
 * another's drawings by naming an id.
 */
rfiPackagesRouter.post("/", async (req, res) => {
  const { projectId } = projectParam.parse(req.params);
  const body = createBody.parse(req.body ?? {});
  let items: RfiPackageItemRef[] = body.items;
  if (body.reviewRunId) {
    await prisma.rfiReviewRun.findFirstOrThrow({ where: { id: body.reviewRunId, projectId }, select: { id: true } });
    const found = await prisma.rfiCandidate.findMany({
      where: { projectId, reviewRunId: body.reviewRunId, status: { not: "dismissed" } },
      orderBy: [{ confidence: "asc" }, { createdAt: "asc" }],
      select: { id: true },
      take: MAX_PACKAGE_ITEMS,
    });
    if (!found.length) return void res.status(404).json({ error: "this review has no findings to mark up" });
    items = [...items, ...found.map((c) => ({ type: "candidate" as const, id: c.id }))].slice(0, MAX_PACKAGE_ITEMS);
  }
  const unique = items.filter((it, i) => items.findIndex((o) => o.type === it.type && o.id === it.id) === i);
  const rfiIds = unique.filter((i) => i.type === "rfi").map((i) => i.id);
  const candidateIds = unique.filter((i) => i.type === "candidate").map((i) => i.id);
  const [rfis, candidates] = await Promise.all([
    prisma.rfi.count({ where: { id: { in: rfiIds }, projectId } }),
    prisma.rfiCandidate.count({ where: { id: { in: candidateIds }, projectId } }),
  ]);
  if (rfis !== rfiIds.length || candidates !== candidateIds.length) {
    return void res.status(404).json({ error: "one or more of these RFIs or findings are not in this project" });
  }
  const row = await prisma.rfiPackage.create({
    data: { projectId, createdById: currentUser(req).id, items: unique as unknown as Prisma.InputJsonValue },
  });
  await rfiPackageQueue.add("package", { packageId: row.id }, { jobId: `rfi-package-${row.id}` });
  res.status(202).json(await toPackageDto(row));
});

rfiPackagesRouter.get("/:packageId", async (req, res) => {
  const { projectId, packageId } = packageParams.parse(req.params);
  const row = await prisma.rfiPackage.findFirstOrThrow({ where: { id: packageId, projectId } });
  res.json(await toPackageDto(row));
});
