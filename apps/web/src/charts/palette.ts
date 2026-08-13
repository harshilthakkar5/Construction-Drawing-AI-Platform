/**
 * Chart colours. Every value is a CSS variable defined per theme in
 * index.css, so a mark keeps its contrast when the app flips to dark rather
 * than being a light-mode hex burnt into the markup.
 *
 * `STATUS_ORDER` is the order the document-status donut paints in — the order
 * is the colourblind-safety mechanism, not decoration: green sits next to
 * yellow and blue next to red, never green next to red. The donut ALWAYS
 * ships secondary encoding as well: a surface gap between segments, a direct
 * percentage label, and a legend naming each slice.
 *
 * Reordering these arrays without re-checking that separation breaks it.
 */
export const STATUS_COLORS: Record<string, string> = {
  completed: "var(--status-completed)",
  processing: "var(--status-processing)",
  uploaded: "var(--status-uploaded)",
  failed: "var(--status-failed)",
  empty: "var(--status-empty)",
};

/** Paint order for the status donut (see the note above). */
export const STATUS_ORDER = ["completed", "processing", "uploaded", "failed"] as const;

/** Categorical series, in assignment order. */
export const CATEGORICAL = [
  "var(--chart-1)",
  "var(--chart-2)",
  "var(--chart-3)",
  "var(--chart-4)",
  "var(--chart-5)",
] as const;

/** The single-series accent, used for trends and magnitude bars. */
export const ACCENT = "var(--chart-1)";

/** Chart chrome. */
export const GRID = "var(--border)";
export const AXIS_TEXT = "var(--muted-foreground)";
/** Card background — for the ring that lifts a marker off the area fill. */
export const SURFACE = "var(--card)";
