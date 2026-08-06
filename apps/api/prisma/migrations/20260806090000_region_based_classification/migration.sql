-- Region-based discipline detection + user-approved summaries.
-- See docs/region-based-classification.md.

-- Progress of the title-block region scrape across a project's pages.
CREATE TYPE "RegionScrapeStatus" AS ENUM ('pending', 'running', 'completed', 'failed');

-- Summaries are generated only on request; `stale` keeps an already-paid-for
-- summary visible after its discipline's page set changed.
CREATE TYPE "PortionSummaryStatus" AS ENUM ('none', 'queued', 'running', 'ready', 'failed', 'stale');

-- The rectangle the user drew over the title block, in relative (0-1)
-- coordinates so it re-applies to every page regardless of size or rotation.
CREATE TABLE "sheet_regions" (
    "id" TEXT NOT NULL,
    "projectId" TEXT NOT NULL,
    "relX" DOUBLE PRECISION NOT NULL,
    "relY" DOUBLE PRECISION NOT NULL,
    "relW" DOUBLE PRECISION NOT NULL,
    "relH" DOUBLE PRECISION NOT NULL,
    "sampleDocumentId" TEXT,
    "samplePageNumber" INTEGER,
    "version" INTEGER NOT NULL DEFAULT 1,
    "scrapeStatus" "RegionScrapeStatus" NOT NULL DEFAULT 'pending',
    "scrapedPages" INTEGER NOT NULL DEFAULT 0,
    "totalPages" INTEGER NOT NULL DEFAULT 0,
    "notFoundPages" INTEGER NOT NULL DEFAULT 0,
    "lastError" TEXT,
    "lastScrapedAt" TIMESTAMP(3),
    "createdById" TEXT,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "sheet_regions_pkey" PRIMARY KEY ("id")
);

CREATE UNIQUE INDEX "sheet_regions_projectId_key" ON "sheet_regions"("projectId");

ALTER TABLE "sheet_regions" ADD CONSTRAINT "sheet_regions_projectId_fkey"
    FOREIGN KEY ("projectId") REFERENCES "projects"("id") ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "sheet_regions" ADD CONSTRAINT "sheet_regions_sampleDocumentId_fkey"
    FOREIGN KEY ("sampleDocumentId") REFERENCES "documents"("id") ON DELETE SET NULL ON UPDATE CASCADE;
ALTER TABLE "sheet_regions" ADD CONSTRAINT "sheet_regions_createdById_fkey"
    FOREIGN KEY ("createdById") REFERENCES "users"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- What the region scrape wrote for each page. regionVersion vs
-- sheet_regions.version is the staleness contract: the pages needing a
-- re-scrape are exactly those where the two differ.
ALTER TABLE "pages" ADD COLUMN "sheetRegionText" TEXT;
ALTER TABLE "pages" ADD COLUMN "sheetNumber" TEXT;
ALTER TABLE "pages" ADD COLUMN "regionMethod" TEXT;
ALTER TABLE "pages" ADD COLUMN "regionVersion" INTEGER;
ALTER TABLE "pages" ADD COLUMN "disciplineSource" TEXT;

-- Portion summary lifecycle (user-approved generation).
ALTER TABLE "portions" ADD COLUMN "pageCount" INTEGER NOT NULL DEFAULT 0;
ALTER TABLE "portions" ADD COLUMN "summaryStatus" "PortionSummaryStatus" NOT NULL DEFAULT 'none';
ALTER TABLE "portions" ADD COLUMN "summaryJobId" TEXT;
ALTER TABLE "portions" ADD COLUMN "summaryRequestedById" TEXT;
ALTER TABLE "portions" ADD COLUMN "summaryRequestedAt" TIMESTAMP(3);
ALTER TABLE "portions" ADD COLUMN "summaryCompletedAt" TIMESTAMP(3);
ALTER TABLE "portions" ADD COLUMN "summaryError" TEXT;

ALTER TABLE "portions" ADD CONSTRAINT "portions_summaryRequestedById_fkey"
    FOREIGN KEY ("summaryRequestedById") REFERENCES "users"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- Existing rows carry summaries written by the old automatic pipeline; mark
-- those as ready so they keep showing instead of looking un-generated.
UPDATE "portions" SET "summaryStatus" = 'ready' WHERE "summary" IS NOT NULL;

-- Backfill pageCount from the pages that actually carry each discipline.
UPDATE "portions" p SET "pageCount" = sub.n
FROM (
    SELECT po.id, COUNT(pg.id) AS n
    FROM "portions" po
    JOIN "documents" d ON d."projectId" = po."projectId" AND d."supersededAt" IS NULL
    JOIN "pages" pg ON pg."documentId" = d.id AND pg."discipline" = po."discipline"
    GROUP BY po.id
) sub
WHERE p.id = sub.id;

-- Portions become stable rows keyed on (projectId, discipline) so a
-- re-categorization can UPSERT them instead of delete+reinsert. Older rows can
-- hold several portions per discipline (one per contiguous run), so collapse
-- them first: keep the lowest startPage, move its chunks, drop the rest.
WITH ranked AS (
    SELECT id,
           ROW_NUMBER() OVER (PARTITION BY "projectId", "discipline" ORDER BY "startPage", id) AS rn,
           FIRST_VALUE(id) OVER (PARTITION BY "projectId", "discipline" ORDER BY "startPage", id) AS keep_id
    FROM "portions"
)
UPDATE "chunks" c SET "portionId" = r.keep_id
FROM ranked r WHERE c."portionId" = r.id AND r.rn > 1;

WITH ranked AS (
    SELECT id,
           ROW_NUMBER() OVER (PARTITION BY "projectId", "discipline" ORDER BY "startPage", id) AS rn,
           FIRST_VALUE(id) OVER (PARTITION BY "projectId", "discipline" ORDER BY "startPage", id) AS keep_id
    FROM "portions"
)
UPDATE "portions" p
SET "startPage" = LEAST(p."startPage", agg.min_start),
    "endPage"   = GREATEST(p."endPage", agg.max_end)
FROM (
    SELECT keep_id, MIN("startPage") AS min_start, MAX("endPage") AS max_end
    FROM ranked r JOIN "portions" po ON po.id = r.id
    GROUP BY keep_id
) agg
WHERE p.id = agg.keep_id;

WITH ranked AS (
    SELECT id,
           ROW_NUMBER() OVER (PARTITION BY "projectId", "discipline" ORDER BY "startPage", id) AS rn
    FROM "portions"
)
DELETE FROM "portions" p USING ranked r WHERE p.id = r.id AND r.rn > 1;

CREATE UNIQUE INDEX "portions_projectId_discipline_key" ON "portions"("projectId", "discipline");
