import { createApp } from "./app.js";
import { missingPrismaModels, PRISMA_STALE_MESSAGE } from "./db.js";
import { env } from "./env.js";
import { processDocumentQueue, summarizeProjectQueue } from "./queues.js";
import { observeQueues, startTelemetry } from "./telemetry.js";

await startTelemetry();
observeQueues([processDocumentQueue, summarizeProjectQueue]);

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
