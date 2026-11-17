-- The title-block step's sheet read (workers/src/sheet_info.py): title lines,
-- the sheets a page points at, sheet index entries, and the sheet's title and
-- levels. titleLines NULL makes a page pending for the next title-block read,
-- so every existing page is read once.
ALTER TABLE "pages" ADD COLUMN "titleLines" TEXT;
ALTER TABLE "pages" ADD COLUMN "sheetRefs" TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[];
ALTER TABLE "pages" ADD COLUMN "indexEntries" JSONB;
ALTER TABLE "pages" ADD COLUMN "sheetTitle" TEXT;
ALTER TABLE "pages" ADD COLUMN "levels" TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[];
