/**
 * Page numbers → human ranges.
 *
 * A discipline's pages are routinely non-contiguous (S-sheets at 1 and 3–18
 * with an A-sheet at 2), so a portion's start/end span is not a description of
 * where its sheets actually are. These helpers turn the real page list into
 * "1, 3–18" instead.
 */
export function toRanges(pages: number[]): [number, number][] {
  const sorted = [...new Set(pages)].sort((a, b) => a - b);
  const ranges: [number, number][] = [];
  for (const page of sorted) {
    const last = ranges[ranges.length - 1];
    if (last && page === last[1] + 1) last[1] = page;
    else ranges.push([page, page]);
  }
  return ranges;
}

/** "1, 3–18". Beyond `maxParts` runs it trims to "…+N more". */
export function formatRanges(pages: number[], maxParts = 3): string {
  const parts = toRanges(pages).map(([from, to]) => (from === to ? `${from}` : `${from}–${to}`));
  if (parts.length === 0) return "—";
  if (parts.length <= maxParts) return parts.join(", ");
  return `${parts.slice(0, maxParts).join(", ")} +${parts.length - maxParts} more`;
}

/** The same list, never trimmed — for a tooltip on the trimmed label. */
export function formatRangesFull(pages: number[]): string {
  return formatRanges(pages, Number.MAX_SAFE_INTEGER);
}

/** "p." for a single page, "pp." for several — the label reads wrong otherwise. */
export function pagePrefix(pages: number[]): string {
  return pages.length === 1 ? "p." : "pp.";
}
