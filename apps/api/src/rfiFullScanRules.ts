import type {
  RfiFullScanAvailability,
  RfiFullScanCatalogueDto,
  RfiFullScanDto,
  RfiFullScanEstimateDto,
  RfiFullScanPairDto,
  RfiFullScanStatus,
} from "@cdip/shared";
import { estimateCostUsd, pricingConfidence, rateFor } from "./usage.js";

/**
 * The full AI scan's rules, pure so they are tested without a database
 * (routes/rfiFullScan.ts does the I/O). The worker counts TOKENS
 * (workers/src/fullscan_plan.py `estimate`); everything priced in dollars is
 * priced here, on the model the person chose — the same split the targeted
 * review uses, so one price table decides every RFI cost on screen.
 */

/** RFI_FULL_SCAN: `off` hides the feature, `beta` (default) shows it with the
 * screen saying accuracy is not yet measured, `on` once benchmarks/rfi_eval.py
 * has passed on real RFIs. Planning spends no model call in any mode. */
export function fullScanAvailability(env: string | undefined = process.env.RFI_FULL_SCAN): RfiFullScanAvailability {
  const value = (env ?? "beta").trim().toLowerCase();
  return value === "off" || value === "on" ? value : "beta";
}

/** The worker's usage stage for discovery sent through the provider's batch
 * API — billed at half. The ledger records the model's plain name (as the
 * summaries' batch path does), so the half is applied here, by stage. */
export const BATCH_STAGE = "discovery_batch";

/** A scan whose worker stopped beating for this long is dead: shown failed and
 * offered a resume, which picks up every tile not yet looked at. */
export const STALE_FULL_SCAN_MS = 10 * 60 * 1000;

export interface WorkerEstimate {
  calls: number;
  images: number;
  inputTokens: number;
  outputTokens: number;
  verifyCalls: { low: number; high: number };
  verifyInputTokens: { low: number; high: number };
  verifyOutputTokens: { low: number; high: number };
}

/** The estimate on screen: the worker's tokens, priced for a model. Unknown
 * pricing stays unknown (null), never a default rate passed off as a price. */
export function priceEstimate(estimate: WorkerEstimate | null, model: string | null, useBatch: boolean): RfiFullScanEstimateDto | null {
  if (!estimate) return null;
  const tokens = {
    calls: estimate.calls,
    images: estimate.images,
    inputTokens: estimate.inputTokens,
    outputTokens: estimate.outputTokens,
    verifyCalls: estimate.verifyCalls,
  };
  if (!model || pricingConfidence(model) === "unknown") return { ...tokens, costUsd: null };
  const cost = (input: number, output: number) =>
    estimateCostUsd({ model, inputTokens: input, outputTokens: output, cacheReadTokens: 0, cacheWriteTokens: 0 });
  const discovery = cost(estimate.inputTokens, estimate.outputTokens) * (useBatch ? 0.5 : 1);
  return {
    ...tokens,
    costUsd: {
      low: round(discovery + cost(estimate.verifyInputTokens.low, estimate.verifyOutputTokens.low)),
      high: round(discovery + cost(estimate.verifyInputTokens.high, estimate.verifyOutputTokens.high)),
    },
  };
}

/**
 * A dollar budget as the token ceiling the worker enforces. Priced at the FULL
 * rate of the estimate's own input/output mix, never the batch half: a budget
 * is a promise about the bill, so it is converted the way that cannot overrun
 * it. Null when the model has no known price — then a budget in dollars means
 * nothing, and the caller must give tokens.
 */
export function budgetToTokens(budgetUsd: number, model: string, estimate: WorkerEstimate | null): number | null {
  if (pricingConfidence(model) === "unknown" || budgetUsd <= 0) return null;
  const rate = rateFor(model);
  const input = Math.max(1, estimate?.inputTokens ?? 3);
  const output = Math.max(0, estimate?.outputTokens ?? 1);
  const perToken = (input * rate.input + output * rate.output) / (input + output) / 1_000_000;
  return perToken > 0 ? Math.floor(budgetUsd / perToken) : null;
}

export interface SpentRow {
  model: string;
  stage: string | null;
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  cacheWriteTokens: number;
}

/** What a scan has spent, from its own usage rows. Null cost when any row's
 * model has no known price: a partial sum shown as the total would understate. */
export function spentOf(rows: SpentRow[]): RfiFullScanDto["spent"] {
  let costUsd: number | null = 0;
  let inputTokens = 0;
  let outputTokens = 0;
  for (const row of rows) {
    inputTokens += row.inputTokens;
    outputTokens += row.outputTokens;
    if (costUsd === null || pricingConfidence(row.model) === "unknown") {
      costUsd = null;
      continue;
    }
    costUsd += estimateCostUsd(row) * (row.stage === BATCH_STAGE ? 0.5 : 1);
  }
  return { inputTokens, outputTokens, costUsd: costUsd === null ? null : round(costUsd) };
}

export interface ScanRow {
  id: string;
  status: string;
  stage: string | null;
  progress: number;
  provider: string | null;
  model: string | null;
  useBatch: boolean;
  catalogue: unknown;
  pairs: unknown;
  skipped: unknown;
  estimate: unknown;
  limits: unknown;
  notes: unknown;
  findings: number;
  error: string | null;
  heartbeatAt: Date | null;
  createdAt: Date;
  plannedAt: Date | null;
  startedAt: Date | null;
  completedAt: Date | null;
}

/** Whether a worker is still on it. Keyed on the heartbeat, never a fixed
 * run time: a large set legitimately runs for hours. */
export function scanIsActive(scan: Pick<ScanRow, "status" | "heartbeatAt" | "startedAt" | "createdAt">, now: Date): boolean {
  if (!["planning", "queued", "running"].includes(scan.status)) return false;
  const since = (scan.heartbeatAt ?? scan.startedAt ?? scan.createdAt).getTime();
  return now.getTime() - since < STALE_FULL_SCAN_MS;
}

/** The status a person sees: a running scan whose worker died reads failed,
 * with the reason, instead of running forever. */
export function shownStatus(scan: ScanRow, now: Date): { status: RfiFullScanStatus; error: string | null } {
  const status = scan.status as RfiFullScanStatus;
  if (["planning", "queued", "running"].includes(status) && !scanIsActive(scan, now)) {
    return {
      status: "failed",
      error:
        status === "planning"
          ? "the worker stopped while planning; plan the scan again"
          : "the worker stopped responding; resume the scan to carry on from the last saved tile",
    };
  }
  return { status, error: scan.error };
}

/** A scan that stopped with work left — budget reached, a failed batch, a
 * dead worker — can carry on. A stale one cannot: its tiles point at pages
 * that moved, so it has to be planned again. */
export function canResume(status: RfiFullScanStatus, tiles: { total: number; done: number }): boolean {
  return (status === "partial" || status === "failed") && tiles.total > 0;
}

export function toFullScanDto(
  scan: ScanRow,
  tiles: RfiFullScanDto["tiles"],
  spent: RfiFullScanDto["spent"],
  now: Date = new Date(),
): RfiFullScanDto {
  const shown = shownStatus(scan, now);
  const limits = (scan.limits ?? null) as RfiFullScanDto["limits"];
  return {
    id: scan.id,
    status: shown.status,
    stage: scan.stage,
    progress: scan.progress,
    provider: scan.provider,
    model: scan.model,
    useBatch: scan.useBatch,
    catalogue: (scan.catalogue ?? null) as RfiFullScanCatalogueDto | null,
    pairs: (Array.isArray(scan.pairs) ? scan.pairs : []).map((p: RfiFullScanPairDto) => ({
      index: p.index,
      kind: p.kind,
      a: p.a,
      b: p.b,
      reason: p.reason,
      tiles: p.tiles,
    })),
    skipped: (Array.isArray(scan.skipped) ? scan.skipped : []) as RfiFullScanDto["skipped"],
    estimate: priceEstimate((scan.estimate ?? null) as WorkerEstimate | null, scan.model, scan.useBatch),
    limits: limits && typeof limits.maxTotalTokens === "number" ? limits : null,
    tiles,
    spent,
    findings: scan.findings,
    notes: (Array.isArray(scan.notes) ? scan.notes : []).filter((n): n is string => typeof n === "string"),
    error: shown.error,
    createdAt: scan.createdAt.toISOString(),
    plannedAt: scan.plannedAt?.toISOString() ?? null,
    startedAt: scan.startedAt?.toISOString() ?? null,
    completedAt: scan.completedAt?.toISOString() ?? null,
  };
}

function round(usd: number): number {
  return Math.round(usd * 10_000) / 10_000;
}
