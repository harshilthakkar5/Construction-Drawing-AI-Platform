import { deletePrefix } from "./s3.js";
import { deleteProjectPoints } from "./qdrant.js";
import { processDocumentQueue, summarizeProjectQueue } from "./queues.js";
import { redis } from "./redis.js";

/**
 * Project deletion cleanup: a deleted project must disappear from EVERY
 * store, not just PostgreSQL (whose cascade handles documents/pages/chunks/
 * portions/summaries/chat rows):
 *
 *   1. Object storage — every object under projects/{id}/ (original PDFs,
 *      page PNGs, thumbnails, extracted text).
 *   2. Qdrant — every embedding point with payload project_id.
 *   3. Redis — retrieval caches (retrieval:{id}:*) and the summaries cache.
 *
 * Each stage is attempted independently and logged; a failing stage is
 * reported but doesn't abort the others (the DB row is already gone, so
 * leftovers are unreachable — the log line is the signal to re-run cleanup).
 */
export async function purgeProjectData(projectId: string): Promise<void> {
  const tag = `[cleanup ${projectId.slice(0, 8)}]`;

  // Drop queued/failed jobs for this project first: without this they keep
  // retrying against rows that no longer exist (foreign-key violations).
  try {
    let removed = 0;
    for (const queue of [processDocumentQueue, summarizeProjectQueue]) {
      const jobs = [
        ...(await queue.getWaiting(0, 999)),
        ...(await queue.getDelayed(0, 999)),
        ...(await queue.getFailed(0, 999)),
      ];
      for (const job of jobs) {
        if ((job.data as { projectId?: string }).projectId !== projectId) continue;
        await job.remove().then(
          () => removed++,
          () => {},
        );
      }
    }
    console.log(`${tag} queues: removed ${removed} pending jobs`);
  } catch (err) {
    console.error(`${tag} queue cleanup FAILED`, err);
  }

  try {
    const removed = await deletePrefix(`projects/${projectId}/`);
    console.log(`${tag} storage: deleted ${removed} objects under projects/${projectId}/`);
  } catch (err) {
    console.error(`${tag} storage cleanup FAILED`, err);
  }

  try {
    await deleteProjectPoints(projectId);
    console.log(`${tag} qdrant: deleted all points for project`);
  } catch (err) {
    console.error(`${tag} qdrant cleanup FAILED`, err);
  }

  try {
    const keys: string[] = [`cache:summaries:${projectId}`];
    let cursor = "0";
    do {
      const [next, batch] = await redis.scan(cursor, "MATCH", `retrieval:${projectId}:*`, "COUNT", 500);
      cursor = next;
      keys.push(...batch);
    } while (cursor !== "0");
    if (keys.length > 0) await redis.del(...keys);
    console.log(`${tag} redis: deleted ${keys.length} cache keys`);
  } catch (err) {
    console.error(`${tag} redis cleanup FAILED`, err);
  }
}
