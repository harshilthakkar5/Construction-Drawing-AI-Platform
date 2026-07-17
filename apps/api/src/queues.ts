import { Queue } from "bullmq";
import { QUEUES, type ProcessDocumentJob, type SummarizeProjectJob } from "@cdip/shared";
import { redis } from "./redis.js";

/**
 * Jobs are produced here and consumed by the Python workers (workers/).
 * Failed jobs retry with exponential backoff per the large-file handling rules.
 */
export const processDocumentQueue = new Queue<ProcessDocumentJob>(
  QUEUES.processDocument,
  {
    connection: redis,
    defaultJobOptions: {
      attempts: 5,
      backoff: { type: "exponential", delay: 5_000 },
      removeOnComplete: { count: 1000 },
      removeOnFail: false,
    },
  },
);

/** Produced by the worker after processing; the API only observes its depth
 * for queue metrics (Phase 5 telemetry). */
export const summarizeProjectQueue = new Queue<SummarizeProjectJob>(QUEUES.summarizeProject, {
  connection: redis,
});
