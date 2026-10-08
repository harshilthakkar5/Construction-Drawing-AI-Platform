import { Router } from "express";
import { z } from "zod";
import type { RfiFullScanDto } from "@cdip/shared";
import { currentUser } from "../auth.js";
import { prisma } from "../db.js";
import { keyFor } from "../llm.js";
import { rfiFullScanQueue } from "../queues.js";
import { summaryLimiter } from "../rateLimit.js";
import {
  budgetToTokens,
  canResume,
  fullScanAvailability,
  pricesByModel,
  scanIsActive,
  shownStatus,
  spentOf,
  toFullScanDto,
  type ScanRow,
  type WorkerEstimate,
} from "../rfiFullScanRules.js";
import { reviewModelOptions } from "../rfiReviewPlanner.js";

/**
 * The full AI scan (docs/rfi-full-scan.md): every pair of sheets that should
 * agree — the same level drawn by two disciplines, an enlarged plan and the
 * plan it enlarges — shown to a model AREA BY AREA, each possible problem
 * checked again close up, and only what survives the code's rules saved as a
 * finding for a person to review. Never an RFI by itself.
 *
 * Two steps, like the targeted review: PLAN (no model call — the worker reads
 * every page, pairs and lines them up, and counts tokens) and START (the
 * model calls, under a ceiling the person approved). A run that stops —
 * budget reached, worker died — keeps every tile it finished and RESUMES.
 */
export const rfiFullScanRouter = Router({ mergeParams: true });

const projectParam = z.object({ projectId: z.string().uuid() });
const scanParams = projectParam.extend({ scanId: z.string().uuid() });

const MAX_TOKENS_CEILING = 200_000_000;
const startBody = z.object({
  provider: z.enum(["claude", "gemini"]).optional(),
  model: z.string().trim().min(1).max(80).optional(),
  useBatch: z.boolean().default(true),
  budgetUsd: z.number().positive().max(10_000).optional(),
  maxTotalTokens: z.number().int().min(10_000).max(MAX_TOKENS_CEILING).optional(),
});
const resumeBody = z.object({
  budgetUsd: z.number().positive().max(10_000).optional(),
  maxTotalTokens: z.number().int().min(10_000).max(MAX_TOKENS_CEILING).optional(),
});

rfiFullScanRouter.use((_req, res, next) => {
  if (fullScanAvailability() === "off") return void res.status(404).json({ error: "the full AI scan is turned off (RFI_FULL_SCAN=off)" });
  next();
});

async function tileCounts(scanIds: string[]): Promise<Map<string, RfiFullScanDto["tiles"]>> {
  const out = new Map<string, RfiFullScanDto["tiles"]>();
  if (!scanIds.length) return out;
  const rows = await prisma.rfiFullScanTile.groupBy({ by: ["scanId", "status"], where: { scanId: { in: scanIds } }, _count: { _all: true } });
  for (const row of rows) {
    const t = out.get(row.scanId) ?? { total: 0, done: 0, failed: 0, settled: 0 };
    // Settled by the code at plan time: never sent, so never "to do".
    if (row.status === "skipped") {
      t.settled = (t.settled ?? 0) + row._count._all;
      out.set(row.scanId, t);
      continue;
    }
    t.total += row._count._all;
    if (row.status === "done") t.done += row._count._all;
    if (row.status === "failed") t.failed += row._count._all;
    out.set(row.scanId, t);
  }
  return out;
}

async function spent(scanIds: string[]): Promise<Map<string, RfiFullScanDto["spent"]>> {
  if (!scanIds.length) return new Map();
  const rows = await prisma.usageEvent.findMany({
    where: { reviewRunId: { in: scanIds } },
    select: { reviewRunId: true, model: true, stage: true, inputTokens: true, outputTokens: true, cacheReadTokens: true, cacheWriteTokens: true },
  });
  return new Map(scanIds.map((id) => [id, spentOf(rows.filter((r) => r.reviewRunId === id))]));
}

async function dtos(scans: ScanRow[]): Promise<RfiFullScanDto[]> {
  const ids = scans.map((s) => s.id);
  const [tiles, money] = await Promise.all([tileCounts(ids), spent(ids)]);
  const now = new Date();
  const defaultModel = reviewModelOptions().default.model;
  return scans.map((s) =>
    toFullScanDto(s, tiles.get(s.id) ?? { total: 0, done: 0, failed: 0 }, money.get(s.id) ?? spentOf([]), now, defaultModel),
  );
}

async function scanFor(projectId: string, scanId: string): Promise<ScanRow & { idempotencyKey: string | null }> {
  return prisma.rfiFullScan.findFirstOrThrow({ where: { id: scanId, projectId } });
}

async function one(projectId: string, scanId: string): Promise<RfiFullScanDto> {
  return (await dtos([await scanFor(projectId, scanId)]))[0]!;
}

/** The project's scans, newest first, and whether the feature is in beta —
 * which the screen must say, because its accuracy is not yet measured. */
rfiFullScanRouter.get("/", async (req, res) => {
  const { projectId } = projectParam.parse(req.params);
  const scans = await prisma.rfiFullScan.findMany({ where: { projectId }, orderBy: { createdAt: "desc" }, take: 10 });
  const models = reviewModelOptions();
  const latest = scans[0];
  res.json({
    availability: fullScanAvailability(),
    models,
    // The latest plan priced on every offered model, for the Start form.
    prices: latest?.status === "planned" ? pricesByModel((latest.estimate ?? null) as WorkerEstimate | null, models.options) : {},
    scans: await dtos(scans),
  });
});

rfiFullScanRouter.get("/:scanId", async (req, res) => {
  const { projectId, scanId } = scanParams.parse(req.params);
  res.json(await one(projectId, scanId));
});

/** Plan a scan. Spends no model call; one scan at a time per project, since
 * two would read the same pages twice and race on the same findings. */
rfiFullScanRouter.post("/", summaryLimiter, async (req, res) => {
  const { projectId } = projectParam.parse(req.params);
  const now = new Date();
  const open = await prisma.rfiFullScan.findMany({ where: { projectId, status: { in: ["planning", "queued", "running"] } } });
  if (open.some((s) => scanIsActive(s, now))) {
    return void res.status(409).json({ error: "a full scan of this project is already being planned or run" });
  }
  const scan = await prisma.rfiFullScan.create({ data: { projectId, createdById: currentUser(req).id, status: "planning", stage: "queued" } });
  await rfiFullScanQueue.add("plan", { scanId: scan.id, mode: "plan" }, { jobId: `rfi-full-scan-plan-${scan.id}` });
  console.log(`[rfi-full-scan] ${scan.id.slice(0, 8)} planning for project ${projectId.slice(0, 8)}`);
  res.status(202).json(await one(projectId, scan.id));
});

/** The token ceiling for a run, from a dollar budget or tokens given
 * directly. A dollar budget on a model with no known price is refused: there
 * is nothing honest to convert it with. */
function ceiling(
  body: { budgetUsd?: number; maxTotalTokens?: number },
  model: string,
  estimate: WorkerEstimate | null,
): { limits: { maxTotalTokens: number; budgetUsd: number | null } } | { error: string } {
  if (body.maxTotalTokens) return { limits: { maxTotalTokens: body.maxTotalTokens, budgetUsd: body.budgetUsd ?? null } };
  if (!body.budgetUsd) return { error: "set a budget (budgetUsd) or a token ceiling (maxTotalTokens) before starting" };
  const tokens = budgetToTokens(body.budgetUsd, model, estimate);
  if (tokens === null) return { error: `${model} has no known price, so a dollar budget cannot be converted; give maxTotalTokens instead` };
  return { limits: { maxTotalTokens: Math.min(tokens, MAX_TOKENS_CEILING), budgetUsd: body.budgetUsd } };
}

/**
 * Start a planned scan. IDEMPOTENT, like the targeted review: the
 * `Idempotency-Key` header (or the scan id) names the start, a repeat returns
 * the scan it already started, and planned → queued is a CONDITIONAL update so
 * only one request can enqueue. The worker re-checks before its first call
 * that the drawings are still the ones planned, and stops `stale` if not.
 */
rfiFullScanRouter.post("/:scanId/start", summaryLimiter, async (req, res) => {
  const { projectId, scanId } = scanParams.parse(req.params);
  const key = z.string().trim().min(8).max(100).safeParse(req.get("Idempotency-Key") ?? scanId);
  if (!key.success) return void res.status(400).json({ error: "Idempotency-Key must be 8-100 characters" });
  const body = startBody.parse(req.body ?? {});
  const scan = await scanFor(projectId, scanId);

  const repeat = await prisma.rfiFullScan.findFirst({ where: { projectId, idempotencyKey: key.data } });
  if (repeat) {
    if (repeat.id !== scan.id) return void res.status(409).json({ error: "this Idempotency-Key already started a different scan" });
    return void res.status(200).json(await one(projectId, scanId));
  }
  if (scan.status !== "planned") return void res.status(409).json({ error: `this scan is ${shownStatus(scan, new Date()).status}, not planned` });
  const tiles = (await tileCounts([scan.id])).get(scan.id);
  if (!tiles?.total) return void res.status(409).json({ error: "this plan has no sheet pairs to look at — read its notes for why" });

  const models = reviewModelOptions();
  const provider = body.provider ?? models.default.provider;
  const model = body.model ?? (provider === models.default.provider ? models.default.model : models.options.find((o) => o.provider === provider)?.model);
  if (!model || !models.options.some((o) => o.provider === provider && o.model === model)) {
    return void res.status(400).json({ error: `${model ?? "that model"} is not offered for ${provider}` });
  }
  if (!process.env[keyFor(provider)]) {
    return void res.status(503).json({ error: `no API key is configured for ${provider}; set ${keyFor(provider)}` });
  }
  const limit = ceiling(body, model, (scan.estimate ?? null) as WorkerEstimate | null);
  if ("error" in limit) return void res.status(400).json({ error: limit.error });

  let claimed;
  try {
    claimed = await prisma.rfiFullScan.updateMany({
      where: { id: scan.id, status: "planned", idempotencyKey: null },
      data: {
        status: "queued",
        stage: "queued",
        progress: 0,
        provider,
        model,
        useBatch: body.useBatch,
        limits: limit.limits,
        idempotencyKey: key.data,
        heartbeatAt: new Date(),
      },
    });
  } catch {
    claimed = { count: 0 }; // the key's unique index lost a race
  }
  if (claimed.count === 0) {
    const latest = await scanFor(projectId, scanId);
    if (latest.idempotencyKey === key.data) return void res.status(200).json(await one(projectId, scanId));
    return void res.status(409).json({ error: "this scan was started by another request" });
  }
  await rfiFullScanQueue.add("run", { scanId: scan.id, mode: "run" }, { jobId: `rfi-full-scan-run-${scan.id}` });
  console.log(`[rfi-full-scan] ${scan.id.slice(0, 8)} started on ${provider}/${model}, ceiling ${limit.limits.maxTotalTokens} tokens`);
  res.status(202).json(await one(projectId, scanId));
});

/** Carry on a scan that stopped with work left. The ceiling may be raised;
 * it is never lowered below what is already spent, which would stop the run
 * before its first call. */
rfiFullScanRouter.post("/:scanId/resume", summaryLimiter, async (req, res) => {
  const { projectId, scanId } = scanParams.parse(req.params);
  const body = resumeBody.parse(req.body ?? {});
  const scan = await scanFor(projectId, scanId);
  const shown = shownStatus(scan, new Date());
  const tiles = (await tileCounts([scan.id])).get(scan.id) ?? { total: 0, done: 0, failed: 0 };
  if (!canResume(shown.status, tiles)) {
    return void res.status(409).json({ error: `a ${shown.status} scan cannot be resumed${shown.status === "stale" ? " — plan a new one" : ""}` });
  }
  if (!scan.model || !scan.provider) return void res.status(409).json({ error: "this scan was never started" });
  if (!process.env[keyFor(scan.provider === "gemini" ? "gemini" : "claude")]) {
    return void res.status(503).json({ error: `no API key is configured for ${scan.provider}` });
  }
  const previous = (scan.limits ?? null) as { maxTotalTokens: number; budgetUsd: number | null } | null;
  let limits = previous;
  if (body.budgetUsd || body.maxTotalTokens) {
    const limit = ceiling(body, scan.model, (scan.estimate ?? null) as WorkerEstimate | null);
    if ("error" in limit) return void res.status(400).json({ error: limit.error });
    const used = (await spent([scan.id])).get(scan.id)!;
    if (limit.limits.maxTotalTokens <= used.inputTokens + used.outputTokens) {
      return void res.status(400).json({ error: "that ceiling is below what this scan has already spent; raise it" });
    }
    limits = limit.limits;
  }
  const moved = await prisma.rfiFullScan.updateMany({
    where: { id: scan.id, status: scan.status },
    data: { status: "queued", stage: "queued", error: null, completedAt: null, heartbeatAt: new Date(), ...(limits ? { limits } : {}) },
  });
  if (moved.count === 0) return void res.status(409).json({ error: "this scan changed while resuming; reload it" });
  await rfiFullScanQueue.add("run", { scanId: scan.id, mode: "run" }, { jobId: `rfi-full-scan-run-${scan.id}-${Date.now()}` });
  res.status(202).json(await one(projectId, scanId));
});

/** Stop a scan. A queued or planning one stops at once; a running one between
 * calls — a call already sent is paid for either way, and every tile already
 * answered stays saved. */
rfiFullScanRouter.post("/:scanId/cancel", async (req, res) => {
  const { projectId, scanId } = scanParams.parse(req.params);
  const done = await prisma.rfiFullScan.updateMany({
    where: { id: scanId, projectId, status: { in: ["planning", "planned", "queued", "running"] } },
    data: { status: "cancelled", completedAt: new Date() },
  });
  if (done.count === 0) return void res.status(409).json({ error: "this scan has already finished" });
  res.json(await one(projectId, scanId));
});
