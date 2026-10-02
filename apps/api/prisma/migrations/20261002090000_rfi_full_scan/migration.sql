-- AlterTable
ALTER TABLE "pages" ADD COLUMN     "factsVersion" INTEGER,
ADD COLUMN     "gridSummary" JSONB,
ADD COLUMN     "level" TEXT,
ADD COLUMN     "scales" JSONB,
ADD COLUMN     "sheetKind" TEXT;

-- AlterTable
ALTER TABLE "rfi_candidates" ADD COLUMN     "fullScanId" TEXT;

-- CreateTable
CREATE TABLE "rfi_full_scans" (
    "id" TEXT NOT NULL,
    "projectId" TEXT NOT NULL,
    "createdById" TEXT,
    "status" TEXT NOT NULL DEFAULT 'planning',
    "stage" TEXT,
    "progress" INTEGER NOT NULL DEFAULT 0,
    "provider" TEXT,
    "model" TEXT,
    "useBatch" BOOLEAN NOT NULL DEFAULT true,
    "catalogue" JSONB,
    "pairs" JSONB,
    "skipped" JSONB,
    "estimate" JSONB,
    "limits" JSONB,
    "sourceRevisions" JSONB,
    "usage" JSONB,
    "notes" JSONB,
    "findings" INTEGER NOT NULL DEFAULT 0,
    "error" TEXT,
    "idempotencyKey" TEXT,
    "heartbeatAt" TIMESTAMP(3),
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "plannedAt" TIMESTAMP(3),
    "startedAt" TIMESTAMP(3),
    "completedAt" TIMESTAMP(3),

    CONSTRAINT "rfi_full_scans_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "rfi_full_scan_tiles" (
    "id" TEXT NOT NULL,
    "scanId" TEXT NOT NULL,
    "pairIndex" INTEGER NOT NULL,
    "tileIndex" INTEGER NOT NULL,
    "windows" JSONB NOT NULL,
    "status" TEXT NOT NULL DEFAULT 'pending',
    "issues" JSONB,
    "error" TEXT,
    "updatedAt" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "rfi_full_scan_tiles_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE INDEX "rfi_full_scans_projectId_createdAt_idx" ON "rfi_full_scans"("projectId", "createdAt");

-- CreateIndex
CREATE UNIQUE INDEX "rfi_full_scans_projectId_idempotencyKey_key" ON "rfi_full_scans"("projectId", "idempotencyKey");

-- CreateIndex
CREATE INDEX "rfi_full_scan_tiles_scanId_status_idx" ON "rfi_full_scan_tiles"("scanId", "status");

-- CreateIndex
CREATE UNIQUE INDEX "rfi_full_scan_tiles_scanId_pairIndex_tileIndex_key" ON "rfi_full_scan_tiles"("scanId", "pairIndex", "tileIndex");

-- AddForeignKey
ALTER TABLE "rfi_candidates" ADD CONSTRAINT "rfi_candidates_fullScanId_fkey" FOREIGN KEY ("fullScanId") REFERENCES "rfi_full_scans"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "rfi_full_scans" ADD CONSTRAINT "rfi_full_scans_projectId_fkey" FOREIGN KEY ("projectId") REFERENCES "projects"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "rfi_full_scans" ADD CONSTRAINT "rfi_full_scans_createdById_fkey" FOREIGN KEY ("createdById") REFERENCES "users"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "rfi_full_scan_tiles" ADD CONSTRAINT "rfi_full_scan_tiles_scanId_fkey" FOREIGN KEY ("scanId") REFERENCES "rfi_full_scans"("id") ON DELETE CASCADE ON UPDATE CASCADE;


-- A full-scan finding must name its scan, like a targeted one names its run:
-- a finding with no traceable origin cannot be re-checked or explained.
ALTER TABLE "rfi_candidates" DROP CONSTRAINT "rfi_candidates_origin_check";
ALTER TABLE "rfi_candidates" ADD CONSTRAINT "rfi_candidates_origin_check"
  CHECK ("origin" IN ('deterministic_scan', 'targeted_review', 'full_scan'));
ALTER TABLE "rfi_candidates" ADD CONSTRAINT "rfi_candidates_full_scan_has_scan"
  CHECK ("origin" <> 'full_scan' OR "fullScanId" IS NOT NULL);

ALTER TABLE "rfi_full_scans" ADD CONSTRAINT "rfi_full_scans_status_check"
  CHECK ("status" IN ('planning', 'planned', 'queued', 'running', 'ready', 'partial', 'failed', 'cancelled', 'stale'));
ALTER TABLE "rfi_full_scan_tiles" ADD CONSTRAINT "rfi_full_scan_tiles_status_check"
  CHECK ("status" IN ('pending', 'done', 'failed'));
