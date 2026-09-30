import { Router } from "express";
import { z } from "zod";
import {
  RFI_REVIEW_CATALOGUE_VERSION,
  RFI_REVIEW_CHECKS,
  RFI_REVIEW_CHECK_IDS,
  RFI_REVIEW_DEPTHS,
  RFI_REVIEW_INPUT_LIMITS,
  RFI_REVIEW_THINKING,
  RFI_REVIEW_THINKING_LIMITS,
  type RfiReviewDepth,
  type RfiReviewThinking,
} from "@cdip/shared";
import { currentUser } from "../auth.js";
import { prisma } from "../db.js";
import { keyFor } from "../llm.js";
import { rfiReviewQueue } from "../queues.js";
import { summaryLimiter } from "../rateLimit.js";
import { PlanError, compareRuns, planReview, reestimate, reviewModelOptions, toReviewDto, type RunRow } from "../rfiReviewPlanner.js";
import { buildReviewReport, renderReviewReportPdf, type ManifestItem, type ReportKind } from "../rfiReviewReport.js";
import { reviewIsActive, scopeHash, staleReason, type ReviewScope } from "../rfiReviewRules.js";
import { getObjectBytes } from "../s3.js";

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
const modelSettings = {
  provider: z.enum(["claude", "gemini"]).optional(),
  model: z.string().trim().min(1).max(80).optional(),
  maxInputTokens: z.number().int().min(RFI_REVIEW_INPUT_LIMITS.min).max(RFI_REVIEW_INPUT_LIMITS.max).optional(),
  maxThinkingTokens: z.number().int().min(RFI_REVIEW_THINKING_LIMITS.min).max(RFI_REVIEW_THINKING_LIMITS.max).nullable().optional(),
  thinking: z.enum(RFI_REVIEW_THINKING as unknown as [RfiReviewThinking, ...RfiReviewThinking[]]).default("medium"),
};
const planBody = z.object({
  target: z.discriminatedUnion("type", [
    z.object({ type: z.literal("sheet"), value: sheetValue }),
    // An element is a MARK ("C-6", "PC1"), optionally narrowed to a level
    // or an area named in the sheet titles.
    z.object({
      type: z.literal("element"),
      value: z.string().trim().min(1).max(30),
      level: z.string().trim().min(1).max(20).optional(),
      area: z.string().trim().min(1).max(40).optional(),
    }),
    // Each side is resolved on its own and keeps its own evidence — a
    // comparison never collapses into one search for all the words.
    z.object({ type: z.literal("compare"), values: z.array(sheetValue).min(2).max(4) }),
  ]),
  checkMode: z.enum(["auto", "custom", "all_original"]).default("auto"),
  checkIds: z.array(z.enum(RFI_REVIEW_CHECK_IDS as [string, ...string[]])).max(RFI_REVIEW_CHECK_IDS.length).default([]),
  depth: z
    .enum(Object.keys(RFI_REVIEW_DEPTHS) as [RfiReviewDepth, ...RfiReviewDepth[]])
    .default("standard"),
  ...modelSettings,
  excludePageIds: z.array(z.string().uuid()).max(50).default([]),
});
const estimateBody = z.object(modelSettings);
const reportQuery = z.object({ kind: z.enum(["draft", "accepted"]).default("draft") });

async function candidateCounts(runIds: string[]): Promise<Map<string, number>> {
  if (!runIds.length) return new Map();
  const rows = await prisma.rfiCandidate.groupBy({
    by: ["reviewRunId"],
    where: { reviewRunId: { in: runIds } },
    _count: { _all: true },
  });
  return new Map(rows.map((r) => [r.reviewRunId as string, r._count._all]));
}

/** What the plan form offers: the catalogue, depths, limits and the models a
 * review can run on — with, per model, whether a numeric thinking limit
 * means anything to it. */
rfiReviewRouter.get("/options", (_req, res) => {
  res.json({
    catalogueVersion: RFI_REVIEW_CATALOGUE_VERSION,
    checks: RFI_REVIEW_CHECKS,
    depths: RFI_REVIEW_DEPTHS,
    inputLimits: RFI_REVIEW_INPUT_LIMITS,
    thinkingLimits: RFI_REVIEW_THINKING_LIMITS,
    ...reviewModelOptions(),
  });
});

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

async function runFor(projectId: string, runId: string): Promise<RunRow> {
  return prisma.rfiReviewRun.findFirstOrThrow({ where: { id: runId, projectId } });
}

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

/** What a planned scope would cost on another model or with other limits.
 * Nothing is stored: to use that model, plan again with it. */
rfiReviewRouter.post("/:runId/estimate", async (req, res) => {
  const { projectId, runId } = runParams.parse(req.params);
  const body = estimateBody.parse(req.body ?? {});
  const run = await runFor(projectId, runId);
  try {
    res.json(reestimate(run, body));
  } catch (err) {
    if (err instanceof PlanError) return void res.status(err.status).json({ error: err.message, ...err.details });
    throw err;
  }
});

/**
 * Start an approved plan. Refused — with an instruction to re-plan — when the
 * evidence it approved is no longer what the project holds: a document was
 * revised or excluded, or re-ingested so its chunks were re-minted. Analysing
 * the old evidence would produce findings about drawings that no longer exist.
 *
 * IDEMPOTENT. A double click, a retried request or two tabs must not pay
 * twice: the `Idempotency-Key` header (or the run id itself when none is
 * sent) names the start, a repeat returns the run it already started, and the
 * planned → queued move is a CONDITIONAL update, so only one request can win
 * it and enqueue.
 */
rfiReviewRouter.post("/:runId/start", summaryLimiter, async (req, res) => {
  const { projectId, runId } = runParams.parse(req.params);
  const key = z.string().trim().min(8).max(100).safeParse(req.get("Idempotency-Key") ?? runId);
  if (!key.success) return void res.status(400).json({ error: "Idempotency-Key must be 8-100 characters" });
  const run = await runFor(projectId, runId);

  const repeat = await prisma.rfiReviewRun.findFirst({ where: { projectId, idempotencyKey: key.data } });
  if (repeat) {
    if (repeat.id !== run.id) return void res.status(409).json({ error: "this Idempotency-Key already started a different review" });
    return void res.status(200).json(toReviewDto(repeat, 0));
  }
  // A concurrent request with the same key may have won between our reads;
  // every refusal below first checks whether it was OUR start that won.
  const refuse = async (status: number, body: Record<string, unknown>) => {
    const latest = await runFor(projectId, runId);
    if (latest.idempotencyKey === key.data) return void res.status(200).json(toReviewDto(latest, 0));
    res.status(status).json(body);
  };
  if (run.status !== "planned") {
    return refuse(409, { error: `this review is already ${run.status}` });
  }
  if (run.provider && !process.env[keyFor(run.provider === "gemini" ? "gemini" : "claude")]) {
    return void res.status(503).json({ error: `no API key is configured for ${run.provider}; set ${keyFor(run.provider === "gemini" ? "gemini" : "claude")} or plan with the other provider` });
  }
  const scope = run.scope as unknown as ReviewScope;
  const settings = run.limits ? { provider: run.provider, model: run.model, limits: run.limits } : {};
  if (scopeHash(scope, run.checkIds as string[], run.depth, settings) !== run.scopeHash) {
    return void res.status(409).json({ error: "this plan's stored scope does not match its hash", replan: true });
  }

  const documentIds = [...new Set(scope.pages.map((p) => p.documentId))];
  const [liveDocs, liveChunks] = await Promise.all([
    prisma.document.findMany({
      where: { id: { in: documentIds }, projectId, supersededAt: null, includeInRfiAnalysis: true },
      select: { id: true, revision: true },
    }),
    prisma.chunk.findMany({
      where: { id: { in: scope.chunks.map((c) => c.chunkId) } },
      select: { id: true },
    }),
  ]);
  const stale = staleReason(
    scope,
    {
      documentIds: new Set(liveDocs.map((d) => d.id)),
      chunkIds: new Set(liveChunks.map((c) => c.id)),
      revisions: new Map(liveDocs.map((d) => [d.id, d.revision])),
    },
    (run.sourceRevisions ?? {}) as Record<string, number>,
  );
  if (stale) {
    if ((await runFor(projectId, runId)).idempotencyKey === key.data) return refuse(409, {});
    await prisma.rfiReviewRun.updateMany({ where: { id: run.id, status: "planned" }, data: { status: "stale", error: stale } });
    return void res.status(409).json({ error: `${stale} — plan the review again`, replan: true });
  }

  // The same approved scope twice at once would pay twice for one answer.
  const twin = await prisma.rfiReviewRun.findMany({
    where: { projectId, scopeHash: run.scopeHash, status: { in: ["queued", "running"] }, id: { not: run.id } },
  });
  if (twin.some((t) => reviewIsActive(t, new Date()))) {
    return refuse(409, { error: "this exact review is already running" });
  }

  let claimed;
  try {
    claimed = await prisma.rfiReviewRun.updateMany({
      where: { id: run.id, status: "planned", idempotencyKey: null },
      data: { status: "queued", stage: "planning", progress: 0, idempotencyKey: key.data },
    });
  } catch {
    claimed = { count: 0 }; // the key's unique index lost a race
  }
  if (claimed.count === 0) return refuse(409, { error: "this review was started by another request" });
  const job = await rfiReviewQueue.add("review", { runId: run.id }, { jobId: `rfi-review-${run.id}` });
  const updated = await prisma.rfiReviewRun.update({ where: { id: run.id }, data: { jobId: job.id ?? null } });
  console.log(`[rfi-review] ${run.id.slice(0, 8)} queued for project ${projectId.slice(0, 8)} on ${run.provider}/${run.model}`);
  res.status(202).json(toReviewDto(updated, 0));
});

/** Two runs' cost and tokens side by side, with the reasons the comparison
 * may not be like for like. */
rfiReviewRouter.get("/:runId/compare/:otherId", async (req, res) => {
  const { projectId, runId, otherId } = runParams.extend({ otherId: z.string().uuid() }).parse(req.params);
  const [base, other] = await Promise.all([runFor(projectId, runId), runFor(projectId, otherId)]);
  res.json(compareRuns(base, other));
});

async function reportFor(projectId: string, runId: string, kind: ReportKind) {
  const run = await runFor(projectId, runId);
  const candidates = await prisma.rfiCandidate.findMany({
    where: { reviewRunId: run.id },
    orderBy: [{ confidence: "asc" }, { createdAt: "asc" }],
    include: { rfi: { select: { number: true } } },
  });
  const manifest = (Array.isArray(run.evidenceManifest) ? run.evidenceManifest : []) as unknown as ManifestItem[];
  return buildReviewReport(
    toReviewDto(run, candidates.length),
    candidates.map((c) => ({ ...c, rfiNumber: c.rfi?.number ?? null })),
    manifest,
    kind,
  );
}

/** The report as data — the same content as the PDF, for a person's own tools. */
rfiReviewRouter.get("/:runId/report.json", async (req, res) => {
  const { projectId, runId } = runParams.parse(req.params);
  const { kind } = reportQuery.parse(req.query);
  const report = await reportFor(projectId, runId, kind);
  res.setHeader("Content-Disposition", `attachment; filename="rfi-review-${runId.slice(0, 8)}-${kind}.json"`);
  res.json(report);
});

rfiReviewRouter.get("/:runId/report.pdf", async (req, res) => {
  const { projectId, runId } = runParams.parse(req.params);
  const { kind } = reportQuery.parse(req.query);
  const report = await reportFor(projectId, runId, kind);
  // Only keys under this run's own prefix are read — the manifest is a row the
  // worker wrote, and a key outside it must not become a way to read any object.
  const prefix = `projects/${projectId}/rfi-reviews/${runId}/`;
  const pdf = await renderReviewReportPdf(report, (key) => (key.startsWith(prefix) ? getObjectBytes(key) : Promise.resolve(null)));
  res.setHeader("Content-Type", "application/pdf");
  res.setHeader("Content-Disposition", `attachment; filename="rfi-review-${runId.slice(0, 8)}-${kind}.pdf"`);
  res.send(Buffer.from(pdf));
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
