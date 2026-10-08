import { QueueEvents } from "bullmq";
import { QUEUES } from "@cdip/shared";
import { prisma } from "./db.js";
import { keyFor } from "./llm.js";
import { rfiFullScanQueue } from "./queues.js";
import { redis } from "./redis.js";
import {
  autoStartDecision,
  autoStartOf,
  budgetToTokens,
  MAX_TOKENS_CEILING,
  type WorkerEstimate,
} from "./rfiFullScanRules.js";
import { reviewModelOptions } from "./rfiReviewPlanner.js";

/**
 * One-click "Find RFIs in drawings" (Phase 5, docs/rfi-full-scan.md).
 *
 * The scan button stores the project's spend limit on the step-2 plan it
 * creates. Once the worker has written that plan, this decides ONCE whether
 * it may start by itself — under the limit it is started exactly as the Start
 * button would (default model, batch mode, the limit as its budget); over it,
 * or with no price or no key, the plan waits for a person and the screen says
 * why. The decision is written on the plan, so it is never taken twice.
 *
 * Called from three places, because none of them is guaranteed: the rfi-scan
 * queue's "completed" event (works with no browser open), and the two GET
 * routes the screen polls (works if the API was down when the job finished).
 * The start itself is the same conditional planned → queued claim the Start
 * route uses, so any number of callers start at most one run.
 */
export async function maybeAutoStart(fullScanId: string): Promise<void> {
  const scan = await prisma.rfiFullScan.findUnique({ where: { id: fullScanId } });
  if (!scan || scan.status !== "planned") return;
  const state = autoStartOf(scan.autoStart);
  if (!state || state.decision) return;
  // The plan is written at about 90% of the code-check scan, BEFORE its
  // findings are saved — and the run reads those findings to recognise a
  // problem both found (agreement.py). So wait for the scan to finish.
  const codeScan = await prisma.rfiScan.findFirst({ where: { fullScanId }, orderBy: { createdAt: "desc" }, select: { status: true } });
  if (codeScan && codeScan.status !== "completed") return;

  const models = reviewModelOptions();
  const { provider, model } = models.default;
  const estimate = (scan.estimate ?? null) as WorkerEstimate | null;
  const tilesTotal = await prisma.rfiFullScanTile.count({ where: { scanId: scan.id, status: { not: "skipped" } } });
  const decided = autoStartDecision({
    limitUsd: state.limitUsd,
    estimate,
    model,
    tilesTotal,
    keyPresent: Boolean(process.env[keyFor(provider)]),
  });
  const record = { limitUsd: state.limitUsd, ...decided, provider, model, decidedAt: new Date().toISOString() };

  if (decided.decision !== "started") {
    await prisma.rfiFullScan.updateMany({ where: { id: scan.id, status: "planned" }, data: { autoStart: record } });
    console.log(`[rfi-full-scan] ${scan.id.slice(0, 8)} not started by itself: ${decided.reason}`);
    return;
  }
  const tokens = budgetToTokens(state.limitUsd, model, estimate);
  if (tokens === null) return; // priced above, so unreachable; never start without a ceiling
  const claimed = await prisma.rfiFullScan.updateMany({
    where: { id: scan.id, status: "planned", idempotencyKey: null },
    data: {
      status: "queued",
      stage: "queued",
      progress: 0,
      provider,
      model,
      useBatch: true,
      limits: { maxTotalTokens: Math.min(tokens, MAX_TOKENS_CEILING), budgetUsd: state.limitUsd },
      idempotencyKey: `auto-${scan.id}`,
      heartbeatAt: new Date(),
      autoStart: record,
    },
  });
  if (claimed.count === 0) return; // another caller (or a person) got there first
  await rfiFullScanQueue.add("run", { scanId: scan.id, mode: "run" }, { jobId: `rfi-full-scan-run-${scan.id}` });
  console.log(`[rfi-full-scan] ${scan.id.slice(0, 8)} started by itself on ${provider}/${model}: ${decided.reason}`);
}

/** The same, for whichever step-2 plan a code-check scan prepared. */
export async function maybeAutoStartForScan(scan: { status: string; fullScanId: string | null } | null): Promise<void> {
  if (!scan?.fullScanId || scan.status !== "completed") return;
  try {
    await maybeAutoStart(scan.fullScanId);
  } catch (err) {
    console.warn(`[rfi-full-scan] one-click start failed: ${(err as Error).message}`);
  }
}

/**
 * Start the plan the moment the code-check job finishes, browser or no
 * browser. One listener per API process; extra ones (several instances) are
 * harmless because the start is a conditional claim.
 */
export function listenForFinishedScans(): QueueEvents | null {
  try {
    // A blocking reader needs a connection of its own.
    const events = new QueueEvents(QUEUES.rfiScan, { connection: redis.duplicate() });
    events.on("completed", ({ jobId }) => {
      void (async () => {
        const scan = await prisma.rfiScan.findFirst({ where: { jobId }, select: { status: true, fullScanId: true } });
        await maybeAutoStartForScan(scan);
      })().catch((err) => console.warn(`[rfi-full-scan] one-click listener: ${(err as Error).message}`));
    });
    events.on("error", (err) => console.warn(`[rfi-full-scan] one-click listener: ${err.message}`));
    return events;
  } catch (err) {
    console.warn(`[rfi-full-scan] one-click listener not started: ${(err as Error).message}`);
    return null;
  }
}
