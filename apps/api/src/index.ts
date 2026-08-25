import cluster from "node:cluster";
import { availableParallelism } from "node:os";
import { createApp } from "./app.js";
import { missingPrismaModels, PRISMA_STALE_MESSAGE } from "./db.js";
import { env } from "./env.js";
import {
  processDocumentQueue,
  scrapeRegionQueue,
  summarizePortionQueue,
  summarizeProjectQueue,
} from "./queues.js";
import { observeQueues, startTelemetry } from "./telemetry.js";

/**
 * Process model.
 *
 * Node runs one thread, so a single API process serves every request on one
 * core. `API_CLUSTER_WORKERS` forks more:
 *
 *   unset / 1   one process, exactly as before — the default, so nothing
 *               changes for anyone who does not opt in
 *   N > 1       N HTTP workers sharing the listening socket
 *   "auto"      one per available core
 *
 * This is for a single machine. On App Platform or DOKS, run more INSTANCES
 * instead and leave this at 1 — the rate limiter, sessions and caches all live
 * in Redis precisely so several processes can share them, whether they are
 * siblings on one box or containers on different ones.
 *
 * Telemetry has to be split deliberately (see below), which is the only reason
 * this file knows about clustering at all.
 */
function workerCount(): number {
  const raw = (process.env.API_CLUSTER_WORKERS ?? "1").trim().toLowerCase();
  if (raw === "auto") return Math.max(1, availableParallelism());
  const parsed = Number(raw);
  return Number.isFinite(parsed) && parsed >= 1 ? Math.floor(parsed) : 1;
}

const workers = workerCount();
const metricsPort = Number(process.env.OTEL_METRICS_PORT ?? 9464);

function reportStaleModels() {
  const stale = missingPrismaModels();
  if (stale.length > 0) {
    console.error(
      `[startup] Prisma client is missing ${stale.join(", ")} — ` +
        `/dashboard and /support will return 503. ${PRISMA_STALE_MESSAGE}`,
    );
  }
}

async function serve(metricsOn: number | null) {
  if (metricsOn !== null) await startTelemetry(metricsOn);
  const app = createApp();
  app.listen(env.PORT, () => {
    const who = cluster.isWorker ? `worker ${cluster.worker?.id}` : "single process";
    console.log(`API listening on http://localhost:${env.PORT} (${who})`);
  });
}

if (workers === 1) {
  // Unchanged single-process path: one exporter, queue depth observed here.
  await startTelemetry(metricsPort);
  observeQueues([
    processDocumentQueue,
    scrapeRegionQueue,
    summarizePortionQueue,
    summarizeProjectQueue,
  ]);
  reportStaleModels();
  await serve(null);
} else if (cluster.isPrimary) {
  reportStaleModels();
  // Queue depth is a property of the QUEUE, not of a process. Observed once,
  // in the primary: polled in every worker it would be N identical Redis
  // scrapes, and N series Prometheus has to be told to deduplicate.
  await startTelemetry(metricsPort);
  observeQueues([
    processDocumentQueue,
    scrapeRegionQueue,
    summarizePortionQueue,
    summarizeProjectQueue,
  ]);
  console.log(
    `API primary: forking ${workers} workers; queue metrics on :${metricsPort}/metrics`,
  );
  for (let i = 0; i < workers; i++) cluster.fork();

  cluster.on("exit", (worker, code, signal) => {
    // A crashed worker is replaced; the socket stays open throughout, so
    // requests in flight on the other workers never notice.
    console.error(
      `[cluster] worker ${worker.id} exited (${signal || code}) — restarting`,
    );
    cluster.fork();
  });
} else {
  // Each worker exports on its OWN port, because HTTP metrics are recorded
  // where the request is served and the primary never sees one. Prometheus
  // must scrape the primary (queue depth) plus every worker port; see
  // monitoring/prometheus.yml.
  await serve(metricsPort + (cluster.worker?.id ?? 0));
}
