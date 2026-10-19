-- A chat answer a person sent to "Needs your review" (Phase 5). It names the
-- message it came from, like every other origin names its run.
ALTER TABLE "rfi_candidates" ADD COLUMN "chatMessageId" TEXT;
ALTER TABLE "rfi_candidates" DROP CONSTRAINT "rfi_candidates_origin_check";
ALTER TABLE "rfi_candidates" ADD CONSTRAINT "rfi_candidates_origin_check"
  CHECK ("origin" IN ('deterministic_scan', 'targeted_review', 'full_scan', 'chat'));
ALTER TABLE "rfi_candidates" ADD CONSTRAINT "rfi_candidates_chat_check"
  CHECK ("origin" <> 'chat' OR "chatMessageId" IS NOT NULL);
