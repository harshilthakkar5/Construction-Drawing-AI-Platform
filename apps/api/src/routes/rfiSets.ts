import { Router } from "express";
import { z } from "zod";
import {
  RFI_REVIEW_SHEETS_GUARD,
  RFI_SET_MAX_ELEMENTS,
  RFI_SET_NAME_CHARS,
  reviewThinkingCapability,
  rfiSettingsOf,
  type RfiReviewEstimateDto,
  type RfiSetDetailDto,
  type RfiSetDto,
  type RfiSetItem,
} from "@cdip/shared";
import { currentUser } from "../auth.js";
import { prisma } from "../db.js";
import { summaryLimiter } from "../rateLimit.js";
import { buildLibrary, library } from "../rfiLibrary.js";
import { planReview, reviewModelOptions, toReviewDto } from "../rfiReviewPlanner.js";
import { setCost, setPlanRequest } from "../rfiSets.js";
import { toCandidateDto } from "../rfiStore.js";
import { customElementsOf } from "./rfiLibrary.js";
import { planBody, queuedReviewJobs, startReview } from "./rfiReview.js";

/**
 * Named RFIs ("Create RFI"): a person names one, picks the sheets and the
 * library elements with their questions, and the RFI plans one targeted review
 * per element. Its findings are those reviews' findings. Planning spends no
 * review-model call and shows the summed price; Start starts every review.
 */
export const rfiSetsRouter = Router({ mergeParams: true });

const projectParam = z.object({ projectId: z.string().uuid() });
const setParams = projectParam.extend({ setId: z.string().uuid() });
const createBody = z.object({
  name: z.string().trim().min(1).max(RFI_SET_NAME_CHARS),
  sheets: z.array(z.string().trim().min(1).max(40)).min(1).max(RFI_REVIEW_SHEETS_GUARD),
  items: z
    .array(z.object({ elementId: z.string().min(1).max(40), questionIds: z.array(z.string().min(1).max(60)).min(1).max(60) }))
    .min(1)
    .max(RFI_SET_MAX_ELEMENTS),
});

type SetRow = { id: string; name: string; sheets: unknown; items: unknown; createdAt: Date };

async function libraryFor(projectId: string) {
  const { file, version } = library();
  const [customs, elements] = await Promise.all([
    prisma.rfiCustomQuestion.findMany({ where: { projectId }, select: { id: true, elementId: true, parentId: true, category: true, text: true } }),
    customElementsOf(projectId),
  ]);
  return buildLibrary(file, version, customs, elements);
}

async function summaries(sets: SetRow[]): Promise<RfiSetDto[]> {
  const runs = await prisma.rfiReviewRun.findMany({
    where: { setId: { in: sets.map((s) => s.id) }, status: { not: "cancelled" } },
    select: { id: true, setId: true, status: true, estimate: true },
  });
  const counts = await prisma.rfiCandidate.groupBy({
    by: ["reviewRunId", "status"],
    where: { reviewRunId: { in: runs.map((r) => r.id) } },
    _count: { _all: true },
  });
  return sets.map((s) => {
    const mine = runs.filter((r) => r.setId === s.id);
    const ids = new Set(mine.map((r) => r.id));
    const found = counts.filter((c) => c.reviewRunId && ids.has(c.reviewRunId));
    const byStatus: Record<string, number> = {};
    for (const r of mine) byStatus[r.status] = (byStatus[r.status] ?? 0) + 1;
    return {
      id: s.id,
      name: s.name,
      sheets: s.sheets as string[],
      items: s.items as RfiSetItem[],
      createdAt: s.createdAt.toISOString(),
      runs: byStatus,
      pending: found.filter((c) => c.status === "pending").reduce((n, c) => n + c._count._all, 0),
      findings: found.reduce((n, c) => n + c._count._all, 0),
      ...setCost(mine.filter((r) => r.status !== "stale").map((r) => r.estimate as RfiReviewEstimateDto | null)),
    };
  });
}

async function detail(projectId: string, setId: string, errors: RfiSetDetailDto["planErrors"] = []): Promise<RfiSetDetailDto> {
  const set = await prisma.rfiSet.findFirstOrThrow({ where: { id: setId, projectId } });
  const [summary] = await summaries([set]);
  const runs = await prisma.rfiReviewRun.findMany({
    where: { setId, status: { not: "cancelled" } },
    orderBy: { createdAt: "asc" },
  });
  const candidates = await prisma.rfiCandidate.findMany({
    where: { reviewRunId: { in: runs.map((r) => r.id) } },
    orderBy: [{ confidence: "asc" }, { createdAt: "asc" }],
  });
  const jobs = await queuedReviewJobs(runs);
  const perRun = new Map<string, number>();
  for (const c of candidates) perRun.set(c.reviewRunId!, (perRun.get(c.reviewRunId!) ?? 0) + 1);
  return {
    ...summary!,
    reviews: runs.map((r) => toReviewDto(r, perRun.get(r.id) ?? 0, new Date(), jobs.get(r.id))),
    candidates: candidates.map(toCandidateDto),
    planErrors: errors,
  };
}

rfiSetsRouter.get("/", async (req, res) => {
  const { projectId } = projectParam.parse(req.params);
  const sets = await prisma.rfiSet.findMany({ where: { projectId }, orderBy: { createdAt: "desc" } });
  res.json(await summaries(sets));
});

rfiSetsRouter.post("/", async (req, res) => {
  const { projectId } = projectParam.parse(req.params);
  const body = createBody.parse(req.body ?? {});
  const elementIds = body.items.map((i) => i.elementId);
  if (new Set(elementIds).size !== elementIds.length) return void res.status(400).json({ error: "an element is listed twice" });
  // Every element and question must be the library's or this project's own.
  const lib = await libraryFor(projectId);
  const review = rfiSettingsOf(null).review;
  try {
    for (const item of body.items) setPlanRequest(body.sheets, item, lib, review, false);
  } catch (err) {
    return void res.status(400).json({ error: (err as Error).message });
  }
  const set = await prisma.rfiSet.create({
    data: { projectId, name: body.name, createdById: currentUser(req).id, sheets: [...new Set(body.sheets)], items: body.items },
  });
  res.status(201).json(await detail(projectId, set.id));
});

rfiSetsRouter.get("/:setId", async (req, res) => {
  const { projectId, setId } = setParams.parse(req.params);
  res.json(await detail(projectId, setId));
});

/** Plan one review per element (no review-model call). A re-plan replaces the
 * RFI's unstarted reviews; started ones keep running. An element the planner
 * refuses is reported and the others are still planned. */
rfiSetsRouter.post("/:setId/plan", summaryLimiter, async (req, res) => {
  const { projectId, setId } = setParams.parse(req.params);
  const set = await prisma.rfiSet.findFirstOrThrow({ where: { id: setId, projectId } });
  const project = await prisma.project.findUniqueOrThrow({ where: { id: projectId }, select: { rfiSettings: true } });
  const review = rfiSettingsOf(project.rfiSettings as Parameters<typeof rfiSettingsOf>[0]).review;
  const models = reviewModelOptions();
  const provider = review.provider && review.model ? review.provider : models.default.provider;
  const model = review.provider && review.model ? review.model : models.default.model;
  const budget = reviewThinkingCapability(provider, model).budget;
  const lib = await libraryFor(projectId);

  await prisma.rfiReviewRun.updateMany({ where: { setId, status: "planned" }, data: { status: "cancelled" } });
  const errors: RfiSetDetailDto["planErrors"] = [];
  for (const item of set.items as unknown as RfiSetItem[]) {
    try {
      const request = planBody.parse(setPlanRequest(set.sheets as string[], item, lib, review, budget));
      await planReview(projectId, currentUser(req).id, request, undefined, setId);
    } catch (err) {
      // PlanError's reason, a library element since removed, or a rejected setting: this element only.
      errors.push({ elementId: item.elementId, error: (err as Error).message });
    }
  }
  res.json(await detail(projectId, setId, errors));
});

/** Start every planned review of this RFI; the same refusals as a single Start. */
rfiSetsRouter.post("/:setId/start", summaryLimiter, async (req, res) => {
  const { projectId, setId } = setParams.parse(req.params);
  await prisma.rfiSet.findFirstOrThrow({ where: { id: setId, projectId } });
  const planned = await prisma.rfiReviewRun.findMany({ where: { setId, status: "planned" }, orderBy: { createdAt: "asc" } });
  const errors: RfiSetDetailDto["planErrors"] = [];
  for (const run of planned) {
    // One key per review, so a repeated Start never starts one twice.
    const { status, body } = await startReview(projectId, run.id, `set-${run.id}`);
    if (status >= 400) {
      const elementId = (run.scope as { library?: { element?: { id?: string } } }).library?.element?.id ?? run.id;
      errors.push({ elementId, error: (body as { error?: string }).error ?? `could not start (${status})` });
    }
  }
  res.json(await detail(projectId, setId, errors));
});

/** Delete the RFI. Unstarted reviews are cancelled; started ones finish and
 * their findings stay in "Needs your review". */
rfiSetsRouter.delete("/:setId", async (req, res) => {
  const { projectId, setId } = setParams.parse(req.params);
  await prisma.rfiSet.findFirstOrThrow({ where: { id: setId, projectId } });
  await prisma.rfiReviewRun.updateMany({ where: { setId, status: "planned" }, data: { status: "cancelled" } });
  await prisma.rfiSet.delete({ where: { id: setId } });
  res.json({ deleted: setId });
});
