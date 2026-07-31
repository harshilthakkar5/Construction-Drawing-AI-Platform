-- User profile fields (registration collects first/last/company; `name`
-- stays the display name and is derived from them).
ALTER TABLE "users" ADD COLUMN "firstName" TEXT;
ALTER TABLE "users" ADD COLUMN "lastName" TEXT;
ALTER TABLE "users" ADD COLUMN "company" TEXT;
ALTER TABLE "users" ADD COLUMN "updatedAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP;

-- Token accounting: one row per model call, so the dashboard can report spend
-- per project and per stage (chat / summary / classification / embedding).
CREATE TYPE "UsageKind" AS ENUM ('chat', 'summary', 'classification', 'embedding');

CREATE TABLE "usage_events" (
    "id" TEXT NOT NULL,
    "projectId" TEXT,
    "kind" "UsageKind" NOT NULL,
    "model" TEXT NOT NULL,
    "inputTokens" INTEGER NOT NULL DEFAULT 0,
    "outputTokens" INTEGER NOT NULL DEFAULT 0,
    "cacheReadTokens" INTEGER NOT NULL DEFAULT 0,
    "cacheWriteTokens" INTEGER NOT NULL DEFAULT 0,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "usage_events_pkey" PRIMARY KEY ("id")
);

CREATE INDEX "usage_events_projectId_idx" ON "usage_events"("projectId");
CREATE INDEX "usage_events_createdAt_idx" ON "usage_events"("createdAt");

-- SetNull, not Cascade: deleting a project must not erase its historical spend.
ALTER TABLE "usage_events" ADD CONSTRAINT "usage_events_projectId_fkey"
    FOREIGN KEY ("projectId") REFERENCES "projects"("id") ON DELETE SET NULL ON UPDATE CASCADE;

CREATE TABLE "support_tickets" (
    "id" TEXT NOT NULL,
    "userId" TEXT,
    "name" TEXT NOT NULL,
    "email" TEXT NOT NULL,
    "subject" TEXT NOT NULL,
    "message" TEXT NOT NULL,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "support_tickets_pkey" PRIMARY KEY ("id")
);

CREATE INDEX "support_tickets_userId_idx" ON "support_tickets"("userId");

ALTER TABLE "support_tickets" ADD CONSTRAINT "support_tickets_userId_fkey"
    FOREIGN KEY ("userId") REFERENCES "users"("id") ON DELETE SET NULL ON UPDATE CASCADE;
