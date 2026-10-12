-- "Scan all drawings" compares two chosen disciplines on chosen questions
-- (docs/rfi-full-scan.md "Scoped scans"). Null keeps today's meaning:
-- every discipline, every question.
ALTER TABLE "rfi_scans" ADD COLUMN "scope" JSONB;
ALTER TABLE "rfi_full_scans" ADD COLUMN "scope" JSONB;

-- The first look's verdict per question asked, so a finished comparison can
-- say "C01: agree in 31 areas" rather than one count for every question.
ALTER TABLE "rfi_full_scan_tiles" ADD COLUMN "answers" JSONB;
