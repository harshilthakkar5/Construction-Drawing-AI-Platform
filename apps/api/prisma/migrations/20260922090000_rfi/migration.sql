-- Phase 1 of RFIs: the log itself. No AI anywhere in this migration.
--
-- An RFI is a question pinned to a place in the drawings, with a lifecycle and
-- a formal answer. The answer is human-authored by construction: an RFI
-- response is a contractual instruction that someone builds from, so no model
-- writes into rfis.answer. Later phases may draft the QUESTION and attach
-- supporting chunks; that is a different column and a different risk.

CREATE TYPE "RfiStatus" AS ENUM ('draft', 'open', 'answered', 'closed', 'voided');
CREATE TYPE "RfiPriority" AS ENUM ('low', 'normal', 'high', 'critical');

-- The number allocator. Incremented inside the create transaction rather than
-- derived from count(): two concurrent creates both read the same count and
-- both write number N. A duplicate RFI number is worse than a missing one --
-- it is already quoted in someone's email by the time anyone notices -- and
-- the UNIQUE below is the backstop that turns the race into a failed insert
-- instead of a corrupted log.
ALTER TABLE "projects" ADD COLUMN "rfiCounter" INTEGER NOT NULL DEFAULT 0;

CREATE TABLE "rfis" (
  "id"           TEXT NOT NULL,
  "projectId"    TEXT NOT NULL,
  "number"       INTEGER NOT NULL,
  "subject"      TEXT NOT NULL,
  "question"     TEXT NOT NULL,
  "status"       "RfiStatus" NOT NULL DEFAULT 'draft',
  "priority"     "RfiPriority" NOT NULL DEFAULT 'normal',
  -- The Discipline vocabulary, same as pages.discipline and projects.roles.
  -- TEXT for the same reason those are: the list lives in @cdip/shared and
  -- adding one must not require a migration.
  "discipline"   TEXT,
  "dueAt"        TIMESTAMP(3),
  "createdById"  TEXT,
  "assignedToId" TEXT,
  "answer"       TEXT,
  "answeredById" TEXT,
  "answeredAt"   TIMESTAMP(3),
  "closedAt"     TIMESTAMP(3),
  "createdAt"    TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updatedAt"    TIMESTAMP(3) NOT NULL,
  CONSTRAINT "rfis_pkey" PRIMARY KEY ("id")
);

-- Where the question was asked. Pinned to (documentId, pageNumber, bbox) and
-- never to a chunkId: replace_page_chunks re-mints chunk uuids on every
-- ingest, so a chunk-pinned RFI breaks the next time its page is processed.
--
-- sheetNumber and combinedPageNumber are SNAPSHOTS, not joins. The document FK
-- is ON DELETE SET NULL precisely so an RFI outlives the drawing it was asked
-- about: it degrades to a readable text reference rather than vanishing with
-- the document, because the RFI is the contractual record and the pin is only
-- a convenience for finding it again.
CREATE TABLE "rfi_locations" (
  "id"                 TEXT NOT NULL,
  "rfiId"              TEXT NOT NULL,
  "documentId"         TEXT,
  "pageNumber"         INTEGER NOT NULL,
  "combinedPageNumber" INTEGER,
  "bbox"               JSONB,
  "sheetNumber"        TEXT,
  -- FR-4: a newer revision superseded the pinned document. The RFI is NOT
  -- repointed automatically -- the geometry may have moved, and that move may
  -- be the very thing the RFI is about.
  "drawingRevised"     BOOLEAN NOT NULL DEFAULT false,
  "supersededById"     TEXT,
  "createdAt"          TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT "rfi_locations_pkey" PRIMARY KEY ("id")
);

-- Append-only audit trail. `kind` is TEXT and not an enum, which is the
-- opposite of the choice made for RfiStatus one table up: a status is a closed
-- set that three layers branch on, while event kinds grow with every phase
-- that touches an RFI and nothing branches on them. The vocabulary lives in
-- RFI_EVENT_KINDS in @cdip/shared.
CREATE TABLE "rfi_events" (
  "id"        TEXT NOT NULL,
  "rfiId"     TEXT NOT NULL,
  "actorId"   TEXT,
  "kind"      TEXT NOT NULL,
  "detail"    JSONB,
  "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT "rfi_events_pkey" PRIMARY KEY ("id")
);

CREATE UNIQUE INDEX "rfis_projectId_number_key" ON "rfis"("projectId", "number");
CREATE INDEX "rfis_projectId_status_idx" ON "rfis"("projectId", "status");
CREATE INDEX "rfis_assignedToId_idx" ON "rfis"("assignedToId");
CREATE INDEX "rfi_locations_rfiId_idx" ON "rfi_locations"("rfiId");
CREATE INDEX "rfi_locations_documentId_idx" ON "rfi_locations"("documentId");
CREATE INDEX "rfi_events_rfiId_createdAt_idx" ON "rfi_events"("rfiId", "createdAt");

ALTER TABLE "rfis" ADD CONSTRAINT "rfis_projectId_fkey"
  FOREIGN KEY ("projectId") REFERENCES "projects"("id") ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "rfis" ADD CONSTRAINT "rfis_createdById_fkey"
  FOREIGN KEY ("createdById") REFERENCES "users"("id") ON DELETE SET NULL ON UPDATE CASCADE;
ALTER TABLE "rfis" ADD CONSTRAINT "rfis_assignedToId_fkey"
  FOREIGN KEY ("assignedToId") REFERENCES "users"("id") ON DELETE SET NULL ON UPDATE CASCADE;
ALTER TABLE "rfis" ADD CONSTRAINT "rfis_answeredById_fkey"
  FOREIGN KEY ("answeredById") REFERENCES "users"("id") ON DELETE SET NULL ON UPDATE CASCADE;

ALTER TABLE "rfi_locations" ADD CONSTRAINT "rfi_locations_rfiId_fkey"
  FOREIGN KEY ("rfiId") REFERENCES "rfis"("id") ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "rfi_locations" ADD CONSTRAINT "rfi_locations_documentId_fkey"
  FOREIGN KEY ("documentId") REFERENCES "documents"("id") ON DELETE SET NULL ON UPDATE CASCADE;

ALTER TABLE "rfi_events" ADD CONSTRAINT "rfi_events_rfiId_fkey"
  FOREIGN KEY ("rfiId") REFERENCES "rfis"("id") ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "rfi_events" ADD CONSTRAINT "rfi_events_actorId_fkey"
  FOREIGN KEY ("actorId") REFERENCES "users"("id") ON DELETE SET NULL ON UPDATE CASCADE;
