-- Phase 4: the code and the AI agreeing on one problem (workers/src/agreement.py).
ALTER TABLE "rfi_candidates" ADD COLUMN "corroboration" JSONB;

-- Phase 5: a per-project spend limit under which step 2 starts by itself,
-- and the decision taken for each plan.
ALTER TABLE "projects" ADD COLUMN "rfiAutoScanUsd" DOUBLE PRECISION;
ALTER TABLE "rfi_full_scans" ADD COLUMN "autoStart" JSONB;
