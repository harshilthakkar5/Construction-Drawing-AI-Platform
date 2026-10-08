-- One "Find RFIs" pass: the code-check scan plans the AI comparison itself,
-- reading each PDF once. rfi_scans.fullScanId names the plan it writes.
ALTER TABLE "rfi_scans" ADD COLUMN "fullScanId" TEXT;
ALTER TABLE "rfi_scans" ADD CONSTRAINT "rfi_scans_fullScanId_fkey"
  FOREIGN KEY ("fullScanId") REFERENCES "rfi_full_scans"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- An area the code settled at plan time (one sheet blank there, or one sheet
-- an exact copy of the other): stored with the reason, never sent to the AI.
ALTER TABLE "rfi_full_scan_tiles" DROP CONSTRAINT "rfi_full_scan_tiles_status_check";
ALTER TABLE "rfi_full_scan_tiles" ADD CONSTRAINT "rfi_full_scan_tiles_status_check"
  CHECK ("status" IN ('pending', 'done', 'failed', 'skipped'));
