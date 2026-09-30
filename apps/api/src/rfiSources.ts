/**
 * A historical RFI is the ANSWER KEY for an RFI review, never its input
 * (docs/rfi-targeted-review.md). A document that looks like one is kept out
 * of every review scope — retrieval, images and the exact checks — from the
 * moment it is uploaded, and the Docs tab can put it back.
 *
 * The rule is duplicated in exactly two places that must agree: the data
 * migration that excluded documents already uploaded
 * (prisma/migrations/20260930090000_rfi_review_full) and this function.
 * rfiSources.test.ts reads the migration and fails on a drift. The worker
 * adds a TEXT check at ingest (workers/src/rfi_sources.py), because a file
 * called "scan_0042.pdf" can still be an RFI.
 */

export const RFI_FILENAME_PATTERN = "(^|[^a-z])(rfi|request[ _-]*for[ _-]*information)([^a-z]|$)";

const RFI_FILENAME = new RegExp(RFI_FILENAME_PATTERN, "i");

export const FILENAME_EXCLUSION_REASON = "Looks like an RFI (filename), so it is kept out of RFI review input";

export function looksLikeRfi(filename: string): boolean {
  return RFI_FILENAME.test(filename);
}
