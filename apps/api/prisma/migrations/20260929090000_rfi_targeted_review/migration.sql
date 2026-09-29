-- Targeted RFI review (docs/rfi-targeted-review.md): a planned, bounded review
-- run per target, candidates that name the run that found them, and a switch
-- keeping historical RFI PDFs out of RFI analysis.

-- CreateEnum
CREATE TYPE "RfiReviewStatus" AS ENUM ('planned', 'queued', 'running', 'ready', 'failed', 'cancelled', 'stale');

-- AlterTable
ALTER TABLE "documents" ADD COLUMN     "includeInRfiAnalysis" BOOLEAN NOT NULL DEFAULT true;

-- AlterTable
ALTER TABLE "rfi_candidates" ADD COLUMN     "origin" TEXT NOT NULL DEFAULT 'deterministic_scan',
ADD COLUMN     "priority" TEXT,
ADD COLUMN     "reasoning" TEXT,
ADD COLUMN     "reviewRunId" TEXT;

-- CreateTable
CREATE TABLE "rfi_review_runs" (
    "id" TEXT NOT NULL,
    "projectId" TEXT NOT NULL,
    "createdById" TEXT,
    "target" JSONB NOT NULL,
    "checkMode" TEXT NOT NULL DEFAULT 'auto',
    "checkIds" JSONB NOT NULL,
    "checkReasons" JSONB,
    "depth" TEXT NOT NULL DEFAULT 'standard',
    "status" "RfiReviewStatus" NOT NULL DEFAULT 'planned',
    "stage" TEXT,
    "progress" INTEGER NOT NULL DEFAULT 0,
    "provider" TEXT,
    "model" TEXT,
    "thinkingRequested" TEXT NOT NULL DEFAULT 'medium',
    "thinkingSent" JSONB,
    "scope" JSONB NOT NULL,
    "scopeHash" TEXT NOT NULL,
    "estimate" JSONB,
    "observations" JSONB,
    "reasoningOutput" JSONB,
    "usage" JSONB,
    "notes" JSONB,
    "error" TEXT,
    "jobId" TEXT,
    "heartbeatAt" TIMESTAMP(3),
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "startedAt" TIMESTAMP(3),
    "completedAt" TIMESTAMP(3),

    CONSTRAINT "rfi_review_runs_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE INDEX "rfi_review_runs_projectId_createdAt_idx" ON "rfi_review_runs"("projectId", "createdAt");

-- AddForeignKey
ALTER TABLE "rfi_candidates" ADD CONSTRAINT "rfi_candidates_reviewRunId_fkey" FOREIGN KEY ("reviewRunId") REFERENCES "rfi_review_runs"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "rfi_review_runs" ADD CONSTRAINT "rfi_review_runs_projectId_fkey" FOREIGN KEY ("projectId") REFERENCES "projects"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "rfi_review_runs" ADD CONSTRAINT "rfi_review_runs_createdById_fkey" FOREIGN KEY ("createdById") REFERENCES "users"("id") ON DELETE SET NULL ON UPDATE CASCADE;


-- A candidate says who found it, and a targeted one must name its run: a
-- finding with no traceable origin cannot be re-checked or explained. (A scan
-- finding's scanId stays nullable, as before: scans are SetNull on delete.)
ALTER TABLE "rfi_candidates" ADD CONSTRAINT "rfi_candidates_origin_check"
  CHECK ("origin" IN ('deterministic_scan', 'targeted_review'));
ALTER TABLE "rfi_candidates" ADD CONSTRAINT "rfi_candidates_targeted_has_run_check"
  CHECK ("origin" <> 'targeted_review' OR "reviewRunId" IS NOT NULL);
