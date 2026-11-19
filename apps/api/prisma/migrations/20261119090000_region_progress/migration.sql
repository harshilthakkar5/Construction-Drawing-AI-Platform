-- The step a title-block read is on ({stage, done, total}), so "Re-read
-- sheets" can show a real progress bar and the Stop button has a place to look.
ALTER TABLE "sheet_regions" ADD COLUMN "progress" JSONB;
