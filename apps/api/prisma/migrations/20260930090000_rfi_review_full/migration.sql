-- AlterEnum
ALTER TYPE "RfiReviewStatus" ADD VALUE 'partial';

-- AlterTable
ALTER TABLE "documents" ADD COLUMN     "rfiExclusionReason" TEXT;

-- AlterTable
ALTER TABLE "rfi_review_runs" ADD COLUMN     "catalogueVersion" TEXT NOT NULL DEFAULT '',
ADD COLUMN     "checkPlan" JSONB,
ADD COLUMN     "checkResults" JSONB,
ADD COLUMN     "coverage" JSONB,
ADD COLUMN     "evidenceManifest" JSONB,
ADD COLUMN     "idempotencyKey" TEXT,
ADD COLUMN     "inventory" JSONB,
ADD COLUMN     "limits" JSONB,
ADD COLUMN     "planningUsage" JSONB,
ADD COLUMN     "sourceRevisions" JSONB;

-- AlterTable
ALTER TABLE "usage_events" ADD COLUMN     "attempt" INTEGER,
ADD COLUMN     "reviewRunId" TEXT,
ADD COLUMN     "stage" TEXT;

-- CreateIndex
CREATE UNIQUE INDEX "rfi_review_runs_projectId_idempotencyKey_key" ON "rfi_review_runs"("projectId", "idempotencyKey");

-- CreateIndex
CREATE INDEX "usage_events_reviewRunId_idx" ON "usage_events"("reviewRunId");


-- A historical RFI is the ANSWER KEY, never review input. Documents already
-- uploaded whose filename says RFI are kept out of review scope; the Docs tab
-- can put one back. Same rule as apps/api/src/rfiSources.ts looksLikeRfi().
UPDATE "documents"
   SET "includeInRfiAnalysis" = false,
       "rfiExclusionReason" = 'Looks like an RFI (filename), so it is kept out of RFI review input'
 WHERE "includeInRfiAnalysis" = true
   AND "filename" ~* '(^|[^a-z])(rfi|request[ _-]*for[ _-]*information)([^a-z]|$)';
