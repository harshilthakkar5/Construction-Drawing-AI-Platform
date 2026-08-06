import { z } from "zod";

/**
 * Validation for the title-block region — the box the user drags over one page
 * and the worker then applies to every page of the project
 * (docs/region-based-classification.md).
 *
 * The box is stored as ratios of the RENDERED page (0-1), which is what makes
 * it survive mixed page sizes and rotations. Kept here, away from the route, so
 * the rules are unit-tested: a box that is off-page or has no area would
 * produce an empty scrape on every sheet, and the failure would only show up
 * as "everything is unclassified" an hour later.
 */

/** Ratio comparisons come from float division in the browser; allow the last bit. */
const EPSILON = 1e-9;

export const regionBoxSchema = z
  .object({
    relX: z.number().min(0).max(1),
    relY: z.number().min(0).max(1),
    relW: z.number().gt(0).max(1),
    relH: z.number().gt(0).max(1),
  })
  .refine((r) => r.relX + r.relW <= 1 + EPSILON && r.relY + r.relH <= 1 + EPSILON, {
    message: "region falls off the page",
  });

export const regionSaveSchema = regionBoxSchema.and(
  z.object({
    sampleDocumentId: z.string().uuid().optional(),
    samplePageNumber: z.number().int().min(1).optional(),
  }),
);

export const regionPreviewSchema = regionBoxSchema.and(
  z.object({ sampleSize: z.number().int().min(1).max(20).optional() }),
);

export type RegionBoxInput = z.infer<typeof regionBoxSchema>;

/** Default number of sample pages a preview scrapes. */
export const DEFAULT_PREVIEW_SAMPLE = 5;

/**
 * A box smaller than this fraction of the page in either direction is almost
 * certainly a stray click rather than a title block. The browser already
 * ignores drags under 8px, but a saved region reaches every page of every
 * document, so it is worth refusing here too.
 */
export const MIN_BOX_RATIO = 0.002;

export function isPlausibleBox(box: RegionBoxInput): boolean {
  return box.relW >= MIN_BOX_RATIO && box.relH >= MIN_BOX_RATIO;
}
