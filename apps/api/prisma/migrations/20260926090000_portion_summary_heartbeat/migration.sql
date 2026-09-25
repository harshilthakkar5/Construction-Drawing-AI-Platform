-- A running summary's heartbeat, and the size the last run asked for.
ALTER TABLE "portions" ADD COLUMN "summaryHeartbeatAt" TIMESTAMP(3);
ALTER TABLE "portions" ADD COLUMN "summaryDetail" TEXT;
