import type { RfiConfidence, RfiScanDto, RfiScanQueueDto, RfiScanStatus, RfiScanUsageDto } from "@cdip/shared";

/**
 * The decisions around a scan, kept pure so they are tested without a queue
 * or a database.
 */

/**
 * How long a scan may go SILENT before it is presumed dead.
 *
 * Without this a worker that died mid-scan would leave the row `running`
 * forever, and "a scan is already running" would disable the button for the
 * life of the project — a failure that looks exactly like a scan that is
 * merely slow. It is measured from the worker's last heartbeat, not from the
 * start: reading text drawn as shapes takes 40-70s a page, so a healthy scan
 * can run an hour, and the old start-time rule would have called it dead at
 * thirty minutes while it was still working. A scan written before heartbeats
 * existed falls back to its start time.
 */
export const STALE_SCAN_MS = 30 * 60 * 1000;

export function scanIsActive(
  scan: { status: RfiScanStatus; createdAt: Date; startedAt: Date | null; heartbeatAt?: Date | null } | null,
  now: Date,
): boolean {
  if (!scan) return false;
  if (scan.status !== "queued" && scan.status !== "running") return false;
  const since = (scan.heartbeatAt ?? scan.startedAt ?? scan.createdAt).getTime();
  return now.getTime() - since < STALE_SCAN_MS;
}

/**
 * Where a not-yet-started scan stands in the worker's queue, from what
 * BullMQ reports. `state` is the job's BullMQ state (null when the job is not
 * in the queue at all); `waiting` is every waiting job's enqueue time and
 * `running` the number of active ones. A scan is "ahead" when it was queued
 * earlier — BullMQ is first in, first out.
 */
export function queuePlace(
  state: string | null,
  enqueuedAt: number | null,
  waiting: number[],
  running: number,
): RfiScanQueueDto {
  if (!state || state === "unknown") return { state: "missing", running, ahead: 0 };
  if (state === "active") return { state: "active", running, ahead: 0 };
  if (state === "waiting" || state === "prioritized" || state === "delayed") {
    const ahead = enqueuedAt === null ? 0 : waiting.filter((t) => t < enqueuedAt).length;
    return { state: "waiting", running, ahead };
  }
  return { state: "other", running, ahead: 0 };
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
  fresh?: boolean;
  error: string | null;
  stage?: string | null;
  progress?: number;
  detail?: string | null;
  heartbeatAt?: Date | null;
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
    fresh: row.fresh === true,
    error: row.error,
    stage: row.stage ?? null,
    progress: Math.max(0, Math.min(100, Math.round(row.progress ?? 0))),
    detail: row.detail ?? null,
    heartbeatAt: row.heartbeatAt?.toISOString() ?? null,
    startedAt: row.startedAt?.toISOString() ?? null,
    finishedAt: row.finishedAt?.toISOString() ?? null,
    createdAt: row.createdAt.toISOString(),
  };
}
