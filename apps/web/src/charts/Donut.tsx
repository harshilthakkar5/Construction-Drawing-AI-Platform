import { useState } from "react";

/**
 * Part-to-whole donut. Every slice carries a legend entry AND a direct
 * percentage label, and segments are separated by a surface-colored gap —
 * that secondary encoding is what makes the palette safe under CVD (see
 * charts/palette.ts), so don't strip it.
 *
 * Slices under ~6% would collide with their neighbours' labels, so their
 * percentage moves to the legend only.
 */
export interface Slice {
  key: string;
  label: string;
  value: number;
  color: string;
}

const SIZE = 176;
const R_OUTER = 80;
const R_INNER = 50;
const GAP_DEGREES = 1.6; // ≈2px of surface between segments at this radius

const polar = (cx: number, cy: number, r: number, deg: number) => {
  const rad = ((deg - 90) * Math.PI) / 180;
  return [cx + r * Math.cos(rad), cy + r * Math.sin(rad)] as const;
};

function arcPath(startDeg: number, endDeg: number): string {
  const c = SIZE / 2;
  const large = endDeg - startDeg > 180 ? 1 : 0;
  const [x1, y1] = polar(c, c, R_OUTER, startDeg);
  const [x2, y2] = polar(c, c, R_OUTER, endDeg);
  const [x3, y3] = polar(c, c, R_INNER, endDeg);
  const [x4, y4] = polar(c, c, R_INNER, startDeg);
  return [
    `M${x1} ${y1}`,
    `A${R_OUTER} ${R_OUTER} 0 ${large} 1 ${x2} ${y2}`,
    `L${x3} ${y3}`,
    `A${R_INNER} ${R_INNER} 0 ${large} 0 ${x4} ${y4}`,
    "Z",
  ].join(" ");
}

export function Donut({
  slices,
  centerLabel,
  centerValue,
}: {
  slices: Slice[];
  centerLabel: string;
  centerValue: string;
}) {
  const [hovered, setHovered] = useState<string | null>(null);
  const present = slices.filter((s) => s.value > 0);
  const total = present.reduce((sum, s) => sum + s.value, 0);

  if (!total) {
    return (
      <div className="grid h-[176px] place-items-center text-sm text-ink-muted">No data yet</div>
    );
  }

  let cursor = 0;
  const arcs = present.map((slice) => {
    const share = slice.value / total;
    const sweep = share * 360;
    // A lone 100% slice must not be split by a gap — it would render as a
    // ring with a visible seam and a 358.4° arc.
    const gap = present.length === 1 ? 0 : GAP_DEGREES;
    const start = cursor + gap / 2;
    const end = cursor + sweep - gap / 2;
    cursor += sweep;
    return { slice, share, start, end: Math.max(start + 0.2, end), mid: start + (end - start) / 2 };
  });

  return (
    <div className="flex flex-wrap items-center gap-6">
      <svg
        width={SIZE}
        height={SIZE}
        viewBox={`0 0 ${SIZE} ${SIZE}`}
        role="img"
        aria-label={`${centerLabel}: ${present
          .map((s) => `${s.label} ${Math.round((s.value / total) * 100)}%`)
          .join(", ")}`}
      >
        {arcs.map(({ slice, share, start, end, mid }) => {
          const [lx, ly] = polar(SIZE / 2, SIZE / 2, (R_OUTER + R_INNER) / 2, mid);
          const dim = hovered !== null && hovered !== slice.key;
          // A 360° arc starts and ends at the same point, which SVG renders as
          // nothing — draw the lone slice as a stroked circle instead.
          const whole = end - start >= 359.9;
          return (
            <g
              key={slice.key}
              onMouseEnter={() => setHovered(slice.key)}
              onMouseLeave={() => setHovered(null)}
            >
              <title>{`${slice.label}: ${slice.value} (${Math.round(share * 100)}%)`}</title>
              {whole ? (
                <circle
                  cx={SIZE / 2}
                  cy={SIZE / 2}
                  r={(R_OUTER + R_INNER) / 2}
                  fill="none"
                  stroke={slice.color}
                  strokeWidth={R_OUTER - R_INNER}
                  opacity={dim ? 0.35 : 1}
                  className="transition-opacity"
                />
              ) : (
                <path
                  d={arcPath(start, end)}
                  fill={slice.color}
                  opacity={dim ? 0.35 : 1}
                  className="transition-opacity"
                />
              )}
              {share >= 0.06 && (
                <text
                  x={lx}
                  y={ly}
                  textAnchor="middle"
                  dominantBaseline="central"
                  fontSize="11"
                  fontWeight="600"
                  fill="#ffffff"
                >
                  {Math.round(share * 100)}%
                </text>
              )}
            </g>
          );
        })}
        <text
          x={SIZE / 2}
          y={SIZE / 2 - 6}
          textAnchor="middle"
          fontSize="20"
          fontWeight="700"
          fill="#101014"
        >
          {centerValue}
        </text>
        <text x={SIZE / 2} y={SIZE / 2 + 13} textAnchor="middle" fontSize="10" fill="#83838f">
          {centerLabel}
        </text>
      </svg>

      <ul className="min-w-[120px] flex-1 space-y-1.5">
        {present.map((slice) => (
          <li
            key={slice.key}
            className="flex items-center gap-2 text-sm"
            onMouseEnter={() => setHovered(slice.key)}
            onMouseLeave={() => setHovered(null)}
          >
            <span
              className="h-2.5 w-2.5 shrink-0 rounded-full"
              style={{ background: slice.color }}
              aria-hidden
            />
            <span className="min-w-0 flex-1 truncate text-ink-soft">{slice.label}</span>
            <span className="tabular-nums font-medium text-ink">{slice.value}</span>
            <span className="w-9 text-right tabular-nums text-ink-muted">
              {Math.round((slice.value / total) * 100)}%
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
