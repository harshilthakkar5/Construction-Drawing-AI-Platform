import type { RfiConfidence, RfiScanDto, RfiScanStatus } from "@cdip/shared";

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

export function toScanDto(row: {
  id: string;
  status: string;
  findings: number;
  modelWorded: number;
  byCheck: unknown;
  notes: unknown;
  error: string | null;
  startedAt: Date | null;
  finishedAt: Date | null;
  createdAt: Date;
}): RfiScanDto {
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
    error: row.error,
    startedAt: row.startedAt?.toISOString() ?? null,
    finishedAt: row.finishedAt?.toISOString() ?? null,
    createdAt: row.createdAt.toISOString(),
  };
}
