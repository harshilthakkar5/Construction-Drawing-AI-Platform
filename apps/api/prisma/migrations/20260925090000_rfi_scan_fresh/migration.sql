-- "Rescan from scratch": the worker reads this flag off the scan row.
ALTER TABLE "rfi_scans" ADD COLUMN "fresh" BOOLEAN NOT NULL DEFAULT false;
