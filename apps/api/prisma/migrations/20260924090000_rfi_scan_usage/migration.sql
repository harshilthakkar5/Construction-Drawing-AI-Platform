-- Per-scan token accounting for the RFI wording step (RFI_THINKING).
ALTER TABLE "rfi_scans" ADD COLUMN "usage" JSONB;
