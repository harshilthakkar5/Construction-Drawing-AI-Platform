import { createApp } from "./app.js";
import { env } from "./env.js";
import { processDocumentQueue, summarizeProjectQueue } from "./queues.js";
import { observeQueues, startTelemetry } from "./telemetry.js";

await startTelemetry();
observeQueues([processDocumentQueue, summarizeProjectQueue]);

const app = createApp();

app.listen(env.PORT, () => {
  console.log(`API listening on http://localhost:${env.PORT}`);
});
