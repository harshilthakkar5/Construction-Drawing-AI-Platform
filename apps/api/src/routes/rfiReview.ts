import { Router } from "express";
import { z } from "zod";
import {
  RFI_REVIEW_CHECK_IDS,
  RFI_REVIEW_DEPTHS,
  RFI_REVIEW_THINKING,
  type RfiReviewDepth,
  type RfiReviewThinking,
} from "@cdip/shared";
import { currentUser } from "../auth.js";
import { prisma } from "../db.js";
import { rfiReviewQueue } from "../queues.js";
import { summaryLimiter } from "../rateLimit.js";
import { PlanError, planReview, toReviewDto } from "../rfiReviewPlanner.js";
import { reviewIsActive, scopeHash, staleReason, type ReviewScope } from "../rfiReviewRules.js";

/**
 * Targeted RFI review (docs/rfi-targeted-review.md): a person names a sheet or
 * sheets to compare, PLANS the review — which checks, which evidence, roughly
 * what it costs — and only then STARTS it. The plan is a persisted row, so the
 * scope the person approved is the scope the worker reads; nothing about it
 * travels in the job payload but the run id.
 *
 * What a review finds lands in rfi_candidates beside the scan's findings and
 * is accepted or dismissed through the same routes (rfiGenerated.ts). It never
 * issues an RFI by itself.
 */
export const rfiReviewRouter = Router({ mergeParams: true });

const projectParam = z.object({ projectId: z.string().uuid() });
const runParams = projectParam.extend({ runId: z.string().uuid() });

const sheetValue = z.string().trim().min(1).max(40);
const planBody = z.object({
  target: z.discriminatedUnion("type", [
    z.object({ type: z.literal("sheet"), value: sheetValue }),
    // Each side is resolved on its own and keeps its own evidence — a
    // comparison never collapses into one search for all the words.
    z.object({ type: z.literal("compare"), values: z.array(sheetValue).min(2).max(4) }),
  ]),
  checkMode: z.enum(["auto", "custom"]).default("auto"),
  checkIds: z.array(z.enum(RFI_REVIEW_CHECK_IDS as [string, ...string[]])).max(RFI_REVIEW_CHECK_IDS.length).default([]),
  depth: z
    .enum(Object.keys(RFI_REVIEW_DEPTHS) as [RfiReviewDepth, ...RfiReviewDepth[]])
    .default("standard"),
  thinking: z.enum(RFI_REVIEW_THINKING as unknown as [RfiReviewThinking, ...RfiReviewThinking[]]).default("medium"),
  excludePageIds: z.array(z.string().uuid()).max(50).default([]),
});

async function candidateCounts(runIds: string[]): Promise<Map<string, number>> {
  if (!runIds.length) return new Map();
  const rows = await prisma.rfiCandidate.groupBy({
    by: ["reviewRunId"],
    where: { reviewRunId: { in: runIds } },
    _count: { _all: true },
  });
  return new Map(rows.map((r) => [r.reviewRunId as string, r._count._all]));
}

rfiReviewRouter.get("/", async (req, res) => {
  const { projectId } = projectParam.parse(req.params);
  const runs = await prisma.rfiReviewRun.findMany({
    where: { projectId, status: { not: "cancelled" } },
    orderBy: { createdAt: "desc" },
    take: 20,
  });
  const counts = await candidateCounts(runs.map((r) => r.id));
  res.json(runs.map((r) => toReviewDto(r, counts.get(r.id) ?? 0)));
});

rfiReviewRouter.get("/:runId", async (req, res) => {
  const { projectId, runId } = runParams.parse(req.params);
  const run = await prisma.rfiReviewRun.findFirstOrThrow({ where: { id: runId, projectId } });
  const counts = await candidateCounts([run.id]);
  res.json(toReviewDto(run, counts.get(run.id) ?? 0));
});

/**
 * Plan a review: resolve the target, choose checks, retrieve and rank the
 * evidence, estimate the cost. Spends no REVIEW model call — only the
 * question embeddings retrieval always spends — and is rate-limited like the
 * other buttons that lead to model spend.
 */
rfiReviewRouter.post("/plan", summaryLimiter, async (req, res) => {
  const { projectId } = projectParam.parse(req.params);
  const body = planBody.parse(req.body ?? {});
  const actor = currentUser(req);
  try {
    const run = await planReview(projectId, actor.id, body);
    res.status(201).json(toReviewDto(run, 0));
  } catch (err) {
    if (err instanceof PlanError) {
      return void res.status(err.status).json({ error: err.message, ...err.details });
    }
    throw err;
  }
});

/**
 * Start an approved plan. Refused — with an instruction to re-plan — when the
 * evidence it approved is no longer what the project holds: a document was
 * revised or excluded, or re-ingested so its chunks were re-minted. Analysing
 * the old evidence would produce findings about drawings that no longer exist.
 */
rfiReviewRouter.post("/:runId/start", summaryLimiter, async (req, res) => {
  const { projectId, runId } = runParams.parse(req.params);
  const run = await prisma.rfiReviewRun.findFirstOrThrow({ where: { id: runId, projectId } });
  if (run.status !== "planned") {
    return void res.status(409).json({ error: `this review is already ${run.status}` });
  }
  const scope = run.scope as unknown as ReviewScope;
  if (scopeHash(scope, run.checkIds as string[], run.depth) !== run.scopeHash) {
    return void res.status(409).json({ error: "this plan's stored scope does not match its hash", replan: true });
  }

  const documentIds = [...new Set(scope.pages.map((p) => p.documentId))];
  const [liveDocs, liveChunks] = await Promise.all([
    prisma.document.findMany({
      where: { id: { in: documentIds }, projectId, supersededAt: null, includeInRfiAnalysis: true },
      select: { id: true },
    }),
    prisma.chunk.findMany({
      where: { id: { in: scope.chunks.map((c) => c.chunkId) } },
      select: { id: true },
    }),
  ]);
  const stale = staleReason(scope, {
    documentIds: new Set(liveDocs.map((d) => d.id)),
    chunkIds: new Set(liveChunks.map((c) => c.id)),
  });
  if (stale) {
    await prisma.rfiReviewRun.update({ where: { id: run.id }, data: { status: "stale", error: stale } });
    return void res.status(409).json({ error: `${stale} — plan the review again`, replan: true });
  }

  // The same approved scope twice at once would pay twice for one answer.
  const twin = await prisma.rfiReviewRun.findMany({
    where: { projectId, scopeHash: run.scopeHash, status: { in: ["queued", "running"] } },
  });
  if (twin.some((t) => reviewIsActive(t, new Date()))) {
    return void res.status(409).json({ error: "this exact review is already running" });
  }

  const job = await rfiReviewQueue.add("review", { runId: run.id });
  const updated = await prisma.rfiReviewRun.update({
    where: { id: run.id },
    data: { status: "queued", jobId: job.id ?? null, stage: "planning", progress: 0 },
  });
  console.log(`[rfi-review] ${run.id.slice(0, 8)} queued for project ${projectId.slice(0, 8)}`);
  res.status(202).json(toReviewDto(updated, 0));
});

/** Stop a review. A planned or queued one stops at once; a running one at
 * its next stage boundary — the worker checks between stages, and a call
 * already sent is paid for either way. */
rfiReviewRouter.post("/:runId/cancel", async (req, res) => {
  const { projectId, runId } = runParams.parse(req.params);
  const done = await prisma.rfiReviewRun.updateMany({
    where: { id: runId, projectId, status: { in: ["planned", "queued", "running"] } },
    data: { status: "cancelled" },
  });
  if (done.count === 0) {
    return void res.status(409).json({ error: "this review has already finished" });
  }
  res.json({ cancelled: true });
});
