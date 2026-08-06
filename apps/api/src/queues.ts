import { Queue } from "bullmq";
import {
  QUEUES,
  type ProcessDocumentJob,
  type RegionPreviewJob,
  type ScrapeRegionJob,
  type SummarizePortionJob,
  type SummarizeProjectJob,
} from "@cdip/shared";
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

/**
 * Region scraping: apply the project's title-block box to its pages and
 * classify them. Also carries the "preview" job (a dry run on a few sample
 * pages) — same worker, different job name.
 *
 * Retries matter here: a scrape commits per page, so a retry resumes rather
 * than restarting.
 */
export const scrapeRegionQueue = new Queue<ScrapeRegionJob | RegionPreviewJob>(
  QUEUES.scrapeRegion,
  {
    connection: redis,
    defaultJobOptions: {
      attempts: 3,
      backoff: { type: "exponential", delay: 5_000 },
      removeOnComplete: { count: 200 },
      removeOnFail: false,
    },
  },
);

/** FR-10/12 on demand — one job per "Generate summary" press. Summaries cost
 * real tokens, so a failed job is not retried behind the user's back; the
 * portion is marked failed and the button becomes "Try again". */
export const summarizePortionQueue = new Queue<SummarizePortionJob>(QUEUES.summarizePortion, {
  connection: redis,
  defaultJobOptions: {
    attempts: 1,
    removeOnComplete: { count: 200 },
    removeOnFail: false,
  },
});

/** The project rollup, also user-triggered. */
export const summarizeProjectQueue = new Queue<SummarizeProjectJob>(QUEUES.summarizeProject, {
  connection: redis,
  defaultJobOptions: { attempts: 1, removeOnComplete: { count: 200 }, removeOnFail: false },
});
