-- Live progress for the project scan: step, percent, a line for people, heartbeat.
ALTER TABLE "rfi_scans" ADD COLUMN "stage" TEXT;
ALTER TABLE "rfi_scans" ADD COLUMN "progress" INTEGER NOT NULL DEFAULT 0;
ALTER TABLE "rfi_scans" ADD COLUMN "detail" TEXT;
ALTER TABLE "rfi_scans" ADD COLUMN "heartbeatAt" TIMESTAMP(3);
