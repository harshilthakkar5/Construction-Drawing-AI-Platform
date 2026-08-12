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

await startTelemetry();
// Every queue, or the Grafana depth panel lies by omission — and queue depth is
// the signal for whether workers need scaling out.
observeQueues([
  processDocumentQueue,
  scrapeRegionQueue,
  summarizePortionQueue,
  summarizeProjectQueue,
]);

const staleModels = missingPrismaModels();
if (staleModels.length > 0) {
  console.error(
    `[startup] Prisma client is missing ${staleModels.join(", ")} — ` +
      `/dashboard and /support will return 503. ${PRISMA_STALE_MESSAGE}`,
  );
}

const app = createApp();

app.listen(env.PORT, () => {
  console.log(`API listening on http://localhost:${env.PORT}`);
});
