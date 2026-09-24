import type { RfiConfidence, RfiScanDto, RfiScanStatus, RfiScanUsageDto } from "@cdip/shared";

/**
 * The decisions around a scan, kept pure so they are tested without a queue
 * or a database.
 */

/**
 * How long a scan may sit queued or running before it is presumed dead.
 *
 * Without this a worker that died mid-scan would leave the row `running`
 * forever, and "a scan is already running" would disable the button for the
 * life of the project — a failure that looks exactly like a scan that is
 * merely slow. Thirty minutes is far past any real scan: the checks are a few
 * SQL reads and the wording is a handful of batched calls.
 */
export const STALE_SCAN_MS = 30 * 60 * 1000;

export function scanIsActive(
  scan: { status: RfiScanStatus; createdAt: Date; startedAt: Date | null } | null,
  now: Date,
): boolean {
  if (!scan) return false;
  if (scan.status !== "queued" && scan.status !== "running") return false;
  const since = (scan.startedAt ?? scan.createdAt).getTime();
  return now.getTime() - since < STALE_SCAN_MS;
}

const RANK: Record<RfiConfidence, number> = { high: 0, medium: 1, low: 2 };

/** "Accept all high" accepts high; "accept all medium" accepts high AND medium. */
export function meetsConfidence(confidence: RfiConfidence, minimum: RfiConfidence): boolean {
  return RANK[confidence] <= RANK[minimum];
}

/** Prices one scan's usage. Injected rather than imported so this module
 * stays pure — usage.ts reaches the database. */
export type CostOf = (row: {
  model: string;
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  cacheWriteTokens: number;
}) => number;

const count = (value: unknown): number =>
  typeof value === "number" && Number.isFinite(value) && value > 0 ? Math.round(value) : 0;
const text = (value: unknown): string | null => (typeof value === "string" && value ? value : null);

/**
 * The worker's usage JSON, read defensively: it is written by another
 * process in another language, and a malformed field must cost the dashboard
 * a number, never the whole scan list. A scan whose wording never ran (no
 * new findings, wording off) has calls 0 and is still returned — "this
 * re-scan cost nothing" is worth showing.
 */
export function scanUsage(raw: unknown, costOf: CostOf): RfiScanUsageDto | null {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const u = raw as Record<string, unknown>;
  const usage = {
    provider: text(u.provider),
    model: text(u.model),
    thinkingSetting: text(u.thinkingSetting),
    thinkingSent: Array.isArray(u.thinkingSent)
      ? u.thinkingSent.filter((t): t is string => typeof t === "string")
      : [],
    thinkingAdjusted: u.thinkingAdjusted === true,
    calls: count(u.calls),
    failedCalls: count(u.failedCalls),
    inputTokens: count(u.inputTokens),
    outputTokens: count(u.outputTokens),
    // null stays null: "the provider did not say" is not "no thinking".
    thinkingTokens: u.thinkingTokens === null || u.thinkingTokens === undefined ? null : count(u.thinkingTokens),
    cacheReadTokens: count(u.cacheReadTokens),
    cacheWriteTokens: count(u.cacheWriteTokens),
  };
  const costUsd = usage.model && usage.calls > 0 ? costOf({ ...usage, model: usage.model }) : 0;
  return { ...usage, costUsd };
}

export function toScanDto(
  row: {
  id: string;
  status: string;
  findings: number;
  modelWorded: number;
  byCheck: unknown;
  notes: unknown;
  usage?: unknown;
  error: string | null;
  startedAt: Date | null;
  finishedAt: Date | null;
  createdAt: Date;
  },
  costOf: CostOf = () => 0,
): RfiScanDto {
  const byCheck =
    row.byCheck && typeof row.byCheck === "object" && !Array.isArray(row.byCheck)
      ? (row.byCheck as Record<string, number>)
      : null;
  return {
    id: row.id,
    status: row.status as RfiScanStatus,
    findings: row.findings,
    modelWorded: row.modelWorded,
    byCheck,
    notes: Array.isArray(row.notes) ? row.notes.filter((n): n is string => typeof n === "string") : [],
    usage: scanUsage(row.usage, costOf),
    error: row.error,
    startedAt: row.startedAt?.toISOString() ?? null,
    finishedAt: row.finishedAt?.toISOString() ?? null,
    createdAt: row.createdAt.toISOString(),
  };
}
