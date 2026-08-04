import { useState } from "react";
import { ACCENT, AXIS_TEXT, GRID } from "./palette";

/**
 * Single-series trend over time (daily token spend). One series, so no legend
 * — the card title names it; a crosshair + tooltip carries the per-day values
 * instead of labelling every point.
 */
export interface TrendPoint {
  date: string;
  value: number;
  /** Shown in the tooltip under the value (e.g. the dollar estimate). */
  note?: string;
}

const W = 640;
const H = 250;
const PAD = { top: 12, right: 10, bottom: 24, left: 40 };

/** 0 / 10K / 20K … — a round step just above the series max. */
function axisTicks(max: number): number[] {
  const raw = max / 4;
  const magnitude = 10 ** Math.floor(Math.log10(raw || 1));
  const nice = [1, 2, 2.5, 5, 10].find((m) => m * magnitude >= raw) ?? 10;
  const step = Math.max(1, nice * magnitude);
  const ticks: number[] = [];
  for (let v = 0; v <= step * 4; v += step) ticks.push(v);
  return ticks;
}

const tickLabel = (v: number) => {
  if (v >= 1_000_000) return `${+(v / 1_000_000).toFixed(1)}M`;
  if (v >= 1000) return `${+(v / 1000).toFixed(v % 1000 ? 1 : 0)}K`;
  return String(v);
};

export function TrendArea({ points, formatValue }: { points: TrendPoint[]; formatValue: (v: number) => string }) {
  const [active, setActive] = useState<number | null>(null);
  if (points.length === 0) return null;

  const ticks = axisTicks(Math.max(...points.map((p) => p.value), 1));
  const max = ticks[ticks.length - 1]!;
  const innerW = W - PAD.left - PAD.right;
  const innerH = H - PAD.top - PAD.bottom;
  const x = (i: number) =>
    PAD.left + (points.length === 1 ? innerW / 2 : (i / (points.length - 1)) * innerW);
  const y = (v: number) => PAD.top + innerH - (v / max) * innerH;

  const line = points.map((p, i) => `${i === 0 ? "M" : "L"}${x(i)} ${y(p.value)}`).join(" ");
  const area = `${line} L${x(points.length - 1)} ${PAD.top + innerH} L${x(0)} ${PAD.top + innerH} Z`;
  const current = active === null ? null : points[active];

  return (
    <div className="relative">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full"
        role="img"
        aria-label={`Daily token usage over the last ${points.length} days`}
        onMouseLeave={() => setActive(null)}
      >
        {/* recessive gridlines, one per axis tick */}
        {ticks.map((t) => (
          <g key={t}>
            <line
              x1={PAD.left}
              x2={W - PAD.right}
              y1={y(t)}
              y2={y(t)}
              stroke={GRID}
              strokeWidth="1"
            />
            <text
              x={PAD.left - 8}
              y={y(t)}
              textAnchor="end"
              dominantBaseline="middle"
              fontSize="10"
              fill={AXIS_TEXT}
            >
              {tickLabel(t)}
            </text>
          </g>
        ))}

        <defs>
          <linearGradient id="trend-fill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={ACCENT} stopOpacity="0.22" />
            <stop offset="100%" stopColor={ACCENT} stopOpacity="0.02" />
          </linearGradient>
        </defs>
        <path d={area} fill="url(#trend-fill)" />
        <path d={line} fill="none" stroke={ACCENT} strokeWidth="2" strokeLinejoin="round" />

        {current && (
          <>
            <line
              x1={x(active!)}
              x2={x(active!)}
              y1={PAD.top}
              y2={PAD.top + innerH}
              stroke={ACCENT}
              strokeOpacity="0.4"
              strokeWidth="1"
            />
            {/* 2px surface ring so the marker reads over the area fill */}
            <circle cx={x(active!)} cy={y(current.value)} r="5" fill={ACCENT} stroke="#fff" strokeWidth="2" />
          </>
        )}

        {/* hit targets — wider than the marks */}
        {points.map((p, i) => (
          <rect
            key={p.date}
            x={x(i) - innerW / points.length / 2}
            y={0}
            width={innerW / points.length}
            height={H}
            fill="transparent"
            onMouseEnter={() => setActive(i)}
          />
        ))}

        {points.map((p, i) =>
          i === 0 || i === points.length - 1 || i === Math.floor(points.length / 2) ? (
            <text
              key={`t-${p.date}`}
              x={x(i)}
              y={H - 6}
              textAnchor={i === 0 ? "start" : i === points.length - 1 ? "end" : "middle"}
              fontSize="10"
              fill={AXIS_TEXT}
            >
              {p.date.slice(5)}
            </text>
          ) : null,
        )}
      </svg>

      {current && (
        <div
          className="pointer-events-none absolute top-0 rounded-md border border-hairline bg-surface px-2.5 py-1.5 text-xs shadow-sm"
          style={{ left: `${(x(active!) / W) * 100}%`, transform: "translateX(-50%)" }}
        >
          <div className="font-medium text-ink">{formatValue(current.value)}</div>
          <div className="text-ink-muted">{current.date}</div>
          {current.note && <div className="text-ink-muted">{current.note}</div>}
        </div>
      )}
    </div>
  );
}
