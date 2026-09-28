import {
  SUMMARY_DETAIL_KEYS,
  type PortionSummaryStatus,
  type SummaryDetail,
} from "@cdip/shared";
import { z } from "zod";

/**
 * Is a discipline summary run still alive?
 *
 * The worker touches `portions.summaryHeartbeatAt` every minute while a run
 * lasts (summarize._heartbeat). Without this rule a worker that crashed or was
 * restarted mid-run left the portion `running` for ever, and the button
 * answered "a summary is already running" to every press — the only way out
 * was editing the database. RFI scans already had the same escape
 * (rfiScanRules.scanIsActive); summaries now have it too, but keyed on the
 * HEARTBEAT rather than on the start time, because a legitimate summary of a
 * 400-page discipline runs for longer than any fixed limit anyone would pick.
 */

const minutes = (name: string, fallback: number) => {
  const value = Number(process.env[name]);
  return Number.isFinite(value) && value > 0 ? value : fallback;
};

/** A running summary silent this long is dead. The heartbeat is every 60s. */
export const staleRunningMs = () => minutes("SUMMARY_STALE_MINUTES", 10) * 60_000;

/**
 * A QUEUED run has no heartbeat yet — it is waiting for a worker slot — so it
 * is timed from the press. Hours, not minutes: a busy worker with four slots
 * can legitimately hold a queue that long.
 */
export const staleQueuedMs = () => minutes("SUMMARY_QUEUED_STALE_MINUTES", 360) * 60_000;

export interface PortionRunState {
  summaryStatus: PortionSummaryStatus;
  summaryRequestedAt: Date | null;
  summaryHeartbeatAt: Date | null;
}

export function summaryRunIsDead(portion: PortionRunState, now: Date = new Date()): boolean {
  if (portion.summaryStatus === "running") {
    const last = portion.summaryHeartbeatAt ?? portion.summaryRequestedAt;
    // No timestamp at all is a row from before heartbeats existed: it cannot
    // be proven alive, and leaving it locked is the bug this fixes.
    return !last || now.getTime() - last.getTime() > staleRunningMs();
  }
  if (portion.summaryStatus === "queued") {
    const since = portion.summaryRequestedAt;
    return !since || now.getTime() - since.getTime() > staleQueuedMs();
  }
  return false;
}

export const DEAD_RUN_MESSAGE =
  "The summary run stopped without finishing (the worker restarted or crashed). " +
  "Pages it had already summarized are kept — generate it again to finish.";

/** What the UI should be told: a dead run reads as failed, with the reason. */
export function effectiveSummaryState(
  portion: PortionRunState & { summaryError: string | null },
  now: Date = new Date(),
): { summaryStatus: PortionSummaryStatus; summaryError: string | null } {
  if (summaryRunIsDead(portion, now)) {
    return { summaryStatus: "failed", summaryError: DEAD_RUN_MESSAGE };
  }
  return { summaryStatus: portion.summaryStatus, summaryError: portion.summaryError };
}

/** A stored or requested size, or null for "use the worker's default". */
export function parseDetail(raw: unknown): SummaryDetail | null {
  return typeof raw === "string" && (SUMMARY_DETAIL_KEYS as string[]).includes(raw)
    ? (raw as SummaryDetail)
    : null;
}

/**
 * Request body for any summary run: an optional size, taken from the shared
 * table so a size added there is accepted here without a second edit.
 * Unknown values are a 400 rather than a silent default — a user who picked
 * "full" and got "standard" would be charged for one and handed the other.
 */
export const detailBody = z
  .object({
    detail: z.enum(SUMMARY_DETAIL_KEYS as [SummaryDetail, ...SummaryDetail[]]).optional(),
  })
  .default({});
