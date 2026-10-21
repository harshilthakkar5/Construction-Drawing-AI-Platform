// What the queue says about one job, for deciding whether work is dead.
// Pure, so the rules built on it can be tested without Redis.

/** A job's BullMQ state and when a worker started it; null when the job is
 * not in the queue at all (finished and removed, or never added). */
export type QueueJob = { state: string; processedOn: number | null } | null;

/** States in which a job is still going to run, or is running. Waiting behind
 * other jobs is not dead, however long ago the work was requested. */
const ALIVE = new Set(["waiting", "delayed", "prioritized", "waiting-children", "active"]);

export function jobAlive(job: QueueJob): boolean {
  return job !== null && ALIVE.has(job.state);
}
