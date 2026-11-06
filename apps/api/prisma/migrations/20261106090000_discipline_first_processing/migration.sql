-- Discipline-first processing. Everything that exists already was fully
-- processed, so it is marked that way and its projects keep processing every
-- new page, exactly as before.
ALTER TABLE "projects" ADD COLUMN "processAll" BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE "projects" ADD COLUMN "processDisciplines" TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[];
ALTER TABLE "pages" ADD COLUMN "fullyProcessed" BOOLEAN NOT NULL DEFAULT false;
UPDATE "projects" SET "processAll" = true;
UPDATE "pages" SET "fullyProcessed" = true;
