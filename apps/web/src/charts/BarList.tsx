import { ACCENT } from "./palette";

/**
 * Horizontal magnitude bars — the form for "compare magnitude, low → high"
 * with long category names (disciplines). One hue: length carries magnitude
 * and every row is directly labelled with its value, so color has no work to
 * do beyond marking the bar.
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
    return <p className="text-muted-foreground py-8 text-center text-sm">{emptyLabel}</p>;
  }
  const max = Math.max(...rows.map((r) => r.value), 1);

  return (
    <ul className="space-y-3.5">
      {rows.map((row) => (
        <li key={row.label}>
          <div className="flex items-baseline justify-between gap-3 pb-1">
            <span className="truncate text-sm" title={row.label}>
              {row.label}
            </span>
            <span className="shrink-0 text-sm font-medium tabular-nums">
              {row.value.toLocaleString()}
              {unit ? <span className="text-muted-foreground ml-1 text-xs">{unit}</span> : null}
            </span>
          </div>
          {/* 4px rounded data-end, anchored to the baseline at left */}
          {/* 4px rounded data-end on a full-width track */}
          <div className="bg-muted h-2 w-full overflow-hidden rounded-full">
            <div
              className="h-full rounded-full transition-[width] duration-500"
              style={{
                width: `${Math.max(2, (row.value / max) * 100)}%`,
                background: ACCENT,
              }}
            />
          </div>
        </li>
      ))}
    </ul>
  );
}
