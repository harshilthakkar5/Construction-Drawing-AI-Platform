import { Queue } from "bullmq";
import { QUEUES, type ProcessDocumentJob } from "@cdip/shared";
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
