/**
 * Chart colors, validated as a set against the card surface (#ffffff).
 *
 * `STATUS_ORDER` is the order the document-status donut paints in — the order
 * is the colorblind-safety mechanism, not decoration: green sits next to
 * yellow and blue next to red, never green next to red. Worst adjacent pair is
 * ΔE 6.3 under protanopia, which is inside the 6–8 floor band, so the donut
 * ALWAYS ships secondary encoding: a surface gap between segments, a direct
 * percentage label, and a legend naming each slice. Yellow is below 3:1 against
 * white, which the same visible labels relieve.
 *
 * Reordering these arrays without re-running the validator breaks that.
 */
export const STATUS_COLORS: Record<string, string> = {
  completed: "#0ca30c",
  processing: "#eda100",
  uploaded: "#2a78d6",
  failed: "#d03b3b",
  empty: "#83838f",
};

/** Paint order for the status donut (see the note above). */
export const STATUS_ORDER = ["completed", "processing", "uploaded", "failed"] as const;

/** Single-hue sequential ramp for magnitude bars (light → dark). */
export const SEQUENTIAL = [
  "#86b6ef",
  "#6da7ec",
  "#5598e7",
  "#3987e5",
  "#2a78d6",
  "#256abf",
  "#1c5cab",
  "#184f95",
] as const;

/** The accent used for single-series trends. */
export const ACCENT = "#2563eb";

/** Chart chrome. */
export const GRID = "#e6e6ec";
export const AXIS_TEXT = "#83838f";

/** Sequential step for rank i of n, darkest first (biggest bar = darkest). */
export function sequentialStep(index: number, total: number): string {
  if (total <= 1) return SEQUENTIAL[4]!;
  const from = SEQUENTIAL.length - 1;
  const to = 1;
  const step = Math.round(from - ((from - to) * index) / (total - 1));
  return SEQUENTIAL[Math.max(0, Math.min(SEQUENTIAL.length - 1, step))]!;
}
