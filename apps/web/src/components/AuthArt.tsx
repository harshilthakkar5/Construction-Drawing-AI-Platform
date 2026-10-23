import type { CSSProperties } from "react";

/**
 * The right half of the auth screens: a blueprint sheet of a steel building, a
 * braced tower, a cantilevered wing, a low braced block and a podium, with its
 * grid bubbles, a connection detail and callouts. The drawing draws itself in
 * layers, then drifts under a scan line, below one headline. Everything is
 * generated here in the palette's `blueprint-line` colour, so it follows the
 * theme and stays sharp at any size. Login and sign-up drift from opposite sides.
 */
type P = [number, number];
const lerp = (a: P, b: P, t: number): P => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];
const seg = (a: P, b: P) => `M${a[0].toFixed(1)} ${a[1].toFixed(1)}L${b[0].toFixed(1)} ${b[1].toFixed(1)}`;
type Quad = [tl: P, tr: P, bl: P, br: P];
const rect = (x0: number, y0: number, x1: number, y1: number): Quad => [[x0, y0], [x1, y0], [x0, y1], [x1, y1]];

/** A framed face between four corners: its columns and its floors. */
function face([tl, tr, bl, br]: Quad, cols: number, rows: number): string {
  const d: string[] = [];
  for (let i = 0; i <= cols; i++) d.push(seg(lerp(tl, tr, i / cols), lerp(bl, br, i / cols)));
  for (let j = 0; j <= rows; j++) d.push(seg(lerp(tl, bl, j / rows), lerp(tr, br, j / rows)));
  return d.join("");
}

/** X-bracing in the bays `pick` chooses. */
function braces([tl, tr, bl, br]: Quad, cols: number, rows: number, pick: (i: number, j: number) => boolean): string {
  const at = (i: number, j: number) => lerp(lerp(tl, bl, j / rows), lerp(tr, br, j / rows), i / cols);
  const d: string[] = [];
  for (let i = 0; i < cols; i++)
    for (let j = 0; j < rows; j++)
      if (pick(i, j)) d.push(seg(at(i, j), at(i + 1, j + 1)), seg(at(i + 1, j), at(i, j + 1)));
  return d.join("");
}

const TOWER = rect(330, 30, 530, 450);
const TOWER_SIDE: Quad = [[530, 30], [585, 55], [530, 450], [585, 440]];
const WING: Quad = [[125, 185], [340, 232], [148, 372], [340, 360]];
const WING_ROOF: Quad = [[175, 160], [362, 206], [125, 185], [340, 232]];
const BLOCK = rect(560, 290, 780, 450);
const BLOCK_TOP: Quad = [[585, 275], [802, 275], [560, 290], [780, 290]];
const PODIUM = rect(180, 450, 650, 545);
const CORE = rect(315, 430, 470, 525);
const CANTILEVER: Quad = [[720, 238], [862, 252], [730, 280], [862, 276]];

/** Drawn strokes, in the order they draw: [path, opacity, width, delay in seconds]. */
const LAYERS: [string, number, number, number][] = [
  [seg([40, 450], [880, 450]), 0.6, 1.3, 0],
  [face(PODIUM, 12, 3), 0.35, 1, 0.2],
  [Array.from({ length: 13 }, (_, i) => 180 + i * 39.2).map((x) => seg([x, 545], [x, 560]) + seg([x - 7, 560], [x + 7, 560])).join(""), 0.3, 1, 0.3],
  [face(TOWER, 5, 16), 0.55, 1, 0.4],
  [face(TOWER_SIDE, 2, 16), 0.3, 1, 0.6],
  [face(WING, 6, 6) + face(WING_ROOF, 6, 1), 0.5, 1, 0.8],
  [face(BLOCK, 6, 5) + face(BLOCK_TOP, 6, 1), 0.5, 1, 1],
  [braces(TOWER, 5, 16, (i, j) => (i === 0 || i === 4) && j % 2 === 0) + braces(TOWER_SIDE, 2, 16, (_, j) => j % 3 === 0), 0.3, 0.8, 1.2],
  [braces(WING, 6, 6, (i, j) => (i + j) % 3 === 0) + braces(BLOCK, 6, 5, (i) => i % 2 === 0), 0.28, 0.8, 1.35],
  [face(CORE, 4, 3) + seg([340, 450], [470, 330]), 0.65, 1.1, 1.5],
  // connection detail, top right: column, beam flanges, gusset and brace
  [
    seg([790, 70], [790, 175]) + seg([802, 70], [802, 175]) +
      seg([700, 106], [790, 106]) + seg([700, 112], [790, 112]) + seg([700, 128], [790, 128]) + seg([700, 134], [790, 134]) +
      seg([770, 134], [790, 160]) + seg([770, 134], [790, 134]) +
      seg([802, 136], [846, 178]) + seg([802, 146], [838, 182]),
    0.45, 1, 1.6,
  ],
  [face(CANTILEVER, 5, 2), 0.35, 0.9, 1.7],
  // faint details under the headline
  [face(rect(60, 640, 170, 790), 3, 6) + braces(rect(60, 640, 170, 790), 3, 6, (i, j) => (i + j) % 2 === 0), 0.14, 0.8, 1.8],
  [face([[400, 640], [520, 655], [410, 780], [520, 770]], 4, 5), 0.14, 0.8, 1.9],
  [seg([700, 640], [700, 800]) + seg([712, 640], [712, 800]) + seg([712, 700], [840, 700]) + seg([712, 712], [840, 712]) + seg([712, 724], [760, 780]), 0.14, 0.8, 2],
];

const ROWS = Array.from({ length: 13 }, (_, i) => ({ y: 440 - i * 30, label: String(i + 1) }));
const COLS = "ABCDEFGHJKLMNOP".split("").map((label, i) => ({ x: 180 + i * 40, label }));

/** Callouts: [text, x, y, leader to (or null)]. */
const NOTES: [string, number, number, P | null][] = [
  ["TYPICAL ROOF", 228, 118, [262, 172]],
  ["TYPICAL MULLION", 228, 140, [300, 214]],
  ["WELDED STEEL CONNECTION DETAIL", 700, 52, null],
  ["GUSSET PL", 700, 92, [776, 140]],
  ["CANTILEVER SECTION A-A", 718, 226, null],
  ["COLUMN C12 - W14x90", 798, 318, [780, 318]],
  ["BRACED FRAME BF-3", 798, 382, [780, 382]],
  ["COLUMN C12 - W14x90", 798, 442, [780, 442]],
  ["STRUCTURAL FRAMEWORK - LVL 20-24", 60, 812, null],
];

const delay = (s: number) => ({ animationDelay: `${s}s` }) as CSSProperties;

export function AuthArt({ variant }: { variant: "login" | "register" }) {
  return (
    <div className="bg-blueprint text-blueprint-line relative h-full w-full overflow-hidden">
      {/* graph paper and glow fill the whole panel; the drawing below fits inside it */}
      <svg className="absolute inset-0 size-full" aria-hidden>
        <defs>
          <pattern id="auth-grid" width="20" height="20" patternUnits="userSpaceOnUse">
            <path d="M20 0H0V20" fill="none" stroke="currentColor" strokeOpacity="0.05" />
          </pattern>
          <radialGradient id="auth-glow" cx="50%" cy="35%" r="65%">
            <stop offset="0" stopColor="currentColor" stopOpacity="0.14" />
            <stop offset="1" stopColor="currentColor" stopOpacity="0" />
          </radialGradient>
        </defs>
        <rect width="100%" height="100%" fill="url(#auth-glow)" />
        <rect width="100%" height="100%" fill="url(#auth-grid)" />
      </svg>

      <svg
        viewBox="30 20 860 820"
        preserveAspectRatio="xMidYMin meet"
        className="auth-drift absolute inset-x-6 top-6 h-[calc(100%-3rem)] w-[calc(100%-3rem)]"
        style={{ transformOrigin: variant === "login" ? "60% 40%" : "40% 60%" }}
        fill="none"
        stroke="currentColor"
        strokeLinecap="round"
        aria-hidden
      >
        {/* grid lines and bubbles */}
        <g className="auth-fade" style={delay(0.2)} strokeWidth={0.8}>
          <path d={seg([70, 60], [70, 450])} strokeOpacity={0.35} />
          {ROWS.map(({ y }) => (
            <path key={y} d={seg([80, y], [320, y])} strokeOpacity={0.1} strokeDasharray="6 4" />
          ))}
          {COLS.map(({ x }) => (
            <path key={x} d={seg([x, 450], [x, 574])} strokeOpacity={0.14} strokeDasharray="4 4" />
          ))}
          {[...ROWS.map(({ y, label }) => ({ x: 70, y, label })), ...COLS.map(({ x, label }) => ({ x, y: 585, label }))].map((b) => (
            <g key={`${b.x}-${b.y}`}>
              <circle cx={b.x} cy={b.y} r={9} strokeOpacity={0.4} />
              <text x={b.x} y={b.y + 3} textAnchor="middle" fontSize={8} fill="currentColor" fillOpacity={0.55} stroke="none" fontFamily="var(--font-mono)">
                {b.label}
              </text>
            </g>
          ))}
        </g>

        {LAYERS.map(([d, opacity, width, at], i) => (
          <path key={i} d={d} pathLength={1} strokeOpacity={opacity} strokeWidth={width} className="auth-draw" style={delay(at)} />
        ))}

        {/* callouts */}
        <g className="auth-fade" style={delay(1.8)} fontFamily="var(--font-mono)">
          {NOTES.map(([text, x, y, to], i) => (
            <g key={i}>
              <text x={x} y={y} fontSize={text.endsWith("DETAIL") || text.includes("SECTION") ? 9 : 7} fill="currentColor" fillOpacity={0.45} stroke="none">
                {text}
              </text>
              {to && <path d={seg([x + (x > to[0] ? -4 : text.length * 4.3), y - 3], to)} strokeOpacity={0.25} strokeWidth={0.7} />}
            </g>
          ))}
        </g>
      </svg>

      {/* darken the bottom for the headline, and a slow scan line */}
      <div className="from-blueprint via-blueprint/70 absolute inset-x-0 bottom-0 h-2/5 bg-gradient-to-t to-transparent" />
      <div className="auth-scan via-blueprint-line/10 absolute inset-x-0 top-0 h-24 bg-gradient-to-b from-transparent to-transparent" />

      <h2 className="auth-fade absolute right-10 bottom-12 left-10 max-w-lg text-4xl leading-tight font-bold tracking-tight text-white xl:text-5xl" style={delay(0.6)}>
        Every question starts with the drawing.
      </h2>
    </div>
  );
}
