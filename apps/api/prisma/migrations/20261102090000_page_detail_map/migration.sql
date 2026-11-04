-- The project map: each page's titled details and section/detail symbols
-- (workers/src/detail_map.py), read once per detailMapVersion.
ALTER TABLE "pages" ADD COLUMN "detailMap" JSONB;
ALTER TABLE "pages" ADD COLUMN "detailMapVersion" INTEGER;
