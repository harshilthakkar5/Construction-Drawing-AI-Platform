-- Per-page discipline: lets one discipline map to a single portion even when
-- its pages are non-contiguous (chunks/summaries group by this, not by range).
ALTER TABLE "pages" ADD COLUMN "discipline" TEXT;
