import type { CSSProperties } from "react";

/**
 * The right half of the auth screens: a steel frame drawn in isometric on a
 * blueprint grid. Each floor draws itself in turn, then the frame drifts slowly
 * under a scan line. No words: it is background. Login and sign-up drift from
 * opposite sides so the two pages don't look identical.
 */
const BAYS_X = 6;
const BAYS_Y = 2;
const FLOORS = 8;
const BAY = 60;
const STOREY = 46;

/** Isometric projection of a frame point (x, y in bays, z in storeys). */
function pt(x: number, y: number, z: number): string {
  const sx = (x - y) * BAY * 0.866;
  const sy = (x + y) * BAY * 0.5 - z * STOREY;
  return `${sx.toFixed(1)} ${sy.toFixed(1)}`;
}
const line = (a: [number, number, number], b: [number, number, number]) => `M${pt(...a)}L${pt(...b)}`;

/** One path per storey: the columns up to it and the beams around it, so floors draw in sequence. */
function storey(z: number): string {
  const d: string[] = [];
  for (let x = 0; x <= BAYS_X; x++)
    for (let y = 0; y <= BAYS_Y; y++) {
      if (z > 0) d.push(line([x, y, z - 1], [x, y, z]));
      if (x < BAYS_X) d.push(line([x, y, z], [x + 1, y, z]));
      if (y < BAYS_Y) d.push(line([x, y, z], [x, y + 1, z]));
    }
  return d.join("");
}

/** X-bracing in a few bays of the two visible faces. */
function bracing(): string {
  const d: string[] = [];
  for (let z = 0; z < FLOORS - 1; z++) {
    for (const x of [1, 4]) {
      d.push(line([x, BAYS_Y, z], [x + 1, BAYS_Y, z + 1]), line([x + 1, BAYS_Y, z], [x, BAYS_Y, z + 1]));
    }
    if (z % 2 === 0) d.push(line([BAYS_X, 1, z], [BAYS_X, 2, z + 1]), line([BAYS_X, 2, z], [BAYS_X, 1, z + 1]));
  }
  return d.join("");
}

const delay = (s: number) => ({ animationDelay: `${s}s` }) as CSSProperties;

export function AuthArt({ variant }: { variant: "login" | "register" }) {
  const floors = Array.from({ length: FLOORS }, (_, z) => storey(z));
  return (
    <div className="bg-blueprint text-blueprint-line relative h-full w-full overflow-hidden" aria-hidden>
      {/* graph paper */}
      <svg className="absolute inset-0 size-full">
        <defs>
          <pattern id="auth-grid" width="28" height="28" patternUnits="userSpaceOnUse">
            <path d="M28 0H0V28" fill="none" stroke="currentColor" strokeOpacity="0.07" />
          </pattern>
          <pattern id="auth-grid-lg" width="140" height="140" patternUnits="userSpaceOnUse">
            <path d="M140 0H0V140" fill="none" stroke="currentColor" strokeOpacity="0.13" />
          </pattern>
          <radialGradient id="auth-glow" cx="60%" cy="45%" r="60%">
            <stop offset="0" stopColor="currentColor" stopOpacity="0.16" />
            <stop offset="1" stopColor="currentColor" stopOpacity="0" />
          </radialGradient>
        </defs>
        <rect width="100%" height="100%" fill="url(#auth-glow)" />
        <rect width="100%" height="100%" fill="url(#auth-grid)" />
        <rect width="100%" height="100%" fill="url(#auth-grid-lg)" />
      </svg>

      {/* the frame */}
      <svg
        viewBox="-360 -420 760 720"
        preserveAspectRatio="xMidYMid slice"
        className="auth-drift absolute inset-0 size-full"
        style={{ transformOrigin: variant === "login" ? "65% 45%" : "35% 55%" }}
        fill="none"
        stroke="currentColor"
        strokeLinecap="round"
      >
        {floors.map((d, z) => (
          <path key={z} d={d} pathLength={1} strokeWidth={z === 0 ? 1.4 : 1} strokeOpacity={0.55} className="auth-draw" style={delay(z * 0.35)} />
        ))}
        <path d={bracing()} pathLength={1} strokeWidth={0.8} strokeOpacity={0.35} className="auth-draw" style={delay(FLOORS * 0.35)} />
        {/* dimension strings along the near edge, ticks only */}
        <path
          d={`M${pt(0, BAYS_Y + 0.6, 0)}L${pt(BAYS_X, BAYS_Y + 0.6, 0)}` +
            Array.from({ length: BAYS_X + 1 }, (_, x) => `M${pt(x, BAYS_Y + 0.45, 0)}L${pt(x, BAYS_Y + 0.75, 0)}`).join("")}
          pathLength={1}
          strokeWidth={0.8}
          strokeOpacity={0.4}
          className="auth-draw"
          style={delay(1)}
        />
      </svg>

      {/* fade the bottom edge into the panel, and a slow scan line */}
      <div className="from-blueprint absolute inset-x-0 bottom-0 h-1/3 bg-gradient-to-t to-transparent" />
      <div className="auth-scan via-blueprint-line/15 absolute inset-x-0 top-0 h-24 bg-gradient-to-b from-transparent to-transparent" />
    </div>
  );
}
