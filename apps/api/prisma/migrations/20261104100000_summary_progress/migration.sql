-- A running summary's stage and page count, for its progress bar.
ALTER TABLE "portions" ADD COLUMN "summaryProgress" JSONB;
