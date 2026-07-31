import { sequentialStep } from "./palette";

/**
 * Horizontal magnitude bars — the form for "compare magnitude, low → high"
 * with long category names (disciplines). One hue, more-is-darker (sequential),
 * so rank reads without a legend; every row is directly labeled with its value.
 */
export function BarList({
  rows,
  unit,
  emptyLabel = "Nothing to show yet",
}: {
  rows: { label: string; value: number }[];
  unit?: string;
  emptyLabel?: string;
}) {
  if (rows.length === 0) {
    return <p className="py-8 text-center text-sm text-ink-muted">{emptyLabel}</p>;
  }
  const max = Math.max(...rows.map((r) => r.value), 1);

  return (
    <ul className="space-y-2.5">
      {rows.map((row, i) => (
        <li key={row.label}>
          <div className="flex items-baseline justify-between gap-3 pb-1">
            <span className="truncate text-sm text-ink-soft" title={row.label}>
              {row.label}
            </span>
            <span className="shrink-0 text-sm font-medium tabular-nums text-ink">
              {row.value.toLocaleString()}
              {unit ? <span className="ml-1 text-xs text-ink-muted">{unit}</span> : null}
            </span>
          </div>
          {/* 4px rounded data-end, anchored to the baseline at left */}
          <div className="h-2 w-full overflow-hidden rounded-sm bg-page">
            <div
              className="h-full rounded-r-sm"
              style={{
                width: `${Math.max(2, (row.value / max) * 100)}%`,
                background: sequentialStep(i, rows.length),
              }}
            />
          </div>
        </li>
      ))}
    </ul>
  );
}
