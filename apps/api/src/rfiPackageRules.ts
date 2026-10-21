// Pure rules for marked-up RFI packages (no queue, no database), so they can be tested.

/** A package presumed dead: a worker started it this long ago and it has not
 * finished. Rendering a sheet takes seconds; this is a worker that stopped. */
export const STALE_PACKAGE_MS = 10 * 60 * 1000;

import { jobAlive, type QueueJob } from "./queueJobs.js";

export type PackageJob = QueueJob;

/** Whether a queued or running package is dead. Pure. Asked of the JOB, not of
 * when the package was requested: twenty packages queued at once wait their
 * turn, and the old rule (ten minutes after the request) reported every one
 * still waiting as "the worker did not finish". Dead is: gone from the queue,
 * failed there, or STARTED by a worker that long ago. `job` undefined means the
 * queue could not be asked, and the old rule stands. */
export function packageStale(status: string, createdAt: Date, job: PackageJob | undefined, now: Date): boolean {
  if (status !== "queued" && status !== "running") return false;
  if (job === undefined) return now.getTime() - createdAt.getTime() > STALE_PACKAGE_MS;
  if (!jobAlive(job)) return true;
  // Waiting behind other packages is alive; a STARTED one is dead after the limit.
  return job!.state === "active" && job!.processedOn !== null && now.getTime() - job!.processedOn > STALE_PACKAGE_MS;
}
