-- Generated RFIs: a scan finds gaps in the drawings, the AI words each one as
-- a question, and a person keeps or dismisses it.
--
-- The split of work is the design. The CHECKS decide that something is
-- missing, from the project's own data — a sheet referenced and not in the set,
-- a mark on a plan with no row in its schedule, a note that says TBD. The model
-- only WORDS a finding it is handed. A model asked "what is missing from these
-- drawings" produces a confident, plausible list with no way to tell which
-- items are real, and an RFI that is not real costs an engineer an afternoon.

-- ADD VALUE is transaction-safe on PostgreSQL 12+ as long as the new value is
-- not used in the same transaction. Nothing below uses it.
ALTER TYPE "UsageKind" ADD VALUE IF NOT EXISTS 'rfi';

CREATE TYPE "RfiScanStatus" AS ENUM ('queued', 'running', 'completed', 'failed');
CREATE TYPE "RfiCandidateStatus" AS ENUM ('pending', 'accepted', 'dismissed');
CREATE TYPE "RfiConfidence" AS ENUM ('high', 'medium', 'low');

-- Where an RFI came from. Every row that exists today was typed by a person.
ALTER TABLE "rfis" ADD COLUMN "source" TEXT NOT NULL DEFAULT 'manual';
ALTER TABLE "rfis" ADD COLUMN "checkType" TEXT;

CREATE TABLE "rfi_scans" (
  "id"            TEXT NOT NULL,
  "projectId"     TEXT NOT NULL,
  "status"        "RfiScanStatus" NOT NULL DEFAULT 'queued',
  "requestedById" TEXT,
  "jobId"         TEXT,
  "byCheck"       JSONB,
  -- Which checks did NOT run and why. A scan that found nothing and a scan
  -- that could not look are the same empty list without this.
  "notes"         JSONB,
  "findings"      INTEGER NOT NULL DEFAULT 0,
  "modelWorded"   INTEGER NOT NULL DEFAULT 0,
  "error"         TEXT,
  "startedAt"     TIMESTAMP(3),
  "finishedAt"    TIMESTAMP(3),
  "createdAt"     TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT "rfi_scans_pkey" PRIMARY KEY ("id")
);

-- A finding that is not yet an RFI, and has no number: numbers are quoted in
-- correspondence and never reused, so they are spent only on findings a
-- person kept. UNIQUE on the fingerprint makes a re-scan idempotent — the same
-- gap is one row, and a dismissed one stays dismissed.
CREATE TABLE "rfi_candidates" (
  "id"             TEXT NOT NULL,
  "projectId"      TEXT NOT NULL,
  "scanId"         TEXT,
  "fingerprint"    TEXT NOT NULL,
  "checkType"      TEXT NOT NULL,
  "confidence"     "RfiConfidence" NOT NULL,
  "subject"        TEXT NOT NULL,
  "question"       TEXT NOT NULL,
  "questionSource" TEXT NOT NULL,
  "evidence"       JSONB NOT NULL,
  "status"         "RfiCandidateStatus" NOT NULL DEFAULT 'pending',
  "rfiId"          TEXT,
  "decidedById"    TEXT,
  "decidedAt"      TIMESTAMP(3),
  "createdAt"      TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updatedAt"      TIMESTAMP(3) NOT NULL,
  CONSTRAINT "rfi_candidates_pkey" PRIMARY KEY ("id")
);

CREATE INDEX "rfi_scans_projectId_createdAt_idx" ON "rfi_scans"("projectId", "createdAt");
CREATE UNIQUE INDEX "rfi_candidates_rfiId_key" ON "rfi_candidates"("rfiId");
CREATE UNIQUE INDEX "rfi_candidates_projectId_fingerprint_key" ON "rfi_candidates"("projectId", "fingerprint");
CREATE INDEX "rfi_candidates_projectId_status_idx" ON "rfi_candidates"("projectId", "status");

ALTER TABLE "rfi_scans" ADD CONSTRAINT "rfi_scans_projectId_fkey"
  FOREIGN KEY ("projectId") REFERENCES "projects"("id") ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "rfi_scans" ADD CONSTRAINT "rfi_scans_requestedById_fkey"
  FOREIGN KEY ("requestedById") REFERENCES "users"("id") ON DELETE SET NULL ON UPDATE CASCADE;

ALTER TABLE "rfi_candidates" ADD CONSTRAINT "rfi_candidates_projectId_fkey"
  FOREIGN KEY ("projectId") REFERENCES "projects"("id") ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "rfi_candidates" ADD CONSTRAINT "rfi_candidates_scanId_fkey"
  FOREIGN KEY ("scanId") REFERENCES "rfi_scans"("id") ON DELETE SET NULL ON UPDATE CASCADE;
ALTER TABLE "rfi_candidates" ADD CONSTRAINT "rfi_candidates_rfiId_fkey"
  FOREIGN KEY ("rfiId") REFERENCES "rfis"("id") ON DELETE SET NULL ON UPDATE CASCADE;
ALTER TABLE "rfi_candidates" ADD CONSTRAINT "rfi_candidates_decidedById_fkey"
  FOREIGN KEY ("decidedById") REFERENCES "users"("id") ON DELETE SET NULL ON UPDATE CASCADE;
