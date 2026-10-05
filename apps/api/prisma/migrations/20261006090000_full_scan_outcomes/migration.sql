-- Full scan: say what a run concluded, not just how many findings it saved.
ALTER TABLE "rfi_full_scans" ADD COLUMN "summary" JSONB;
ALTER TABLE "rfi_full_scan_tiles" ADD COLUMN "outcome" TEXT;
ALTER TABLE "rfi_full_scan_tiles" ADD COLUMN "outcomeNote" TEXT;
ALTER TABLE "rfi_full_scan_tiles" ADD COLUMN "dropped" JSONB;
