/**
 * The right half of the auth screens: a still drawing of a steel tower going up,
 * seen from the ground — its frame narrowing into the sky, scaffolding on the
 * lower floors, a crane beside it — fading into mist under one headline. Drawn in
 * the palette's `blueprint-line` colour, so it follows the theme. No motion.
 */
type P = [number, number];
const lerp = (a: P, b: P, t: number): P => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];
const seg = (a: P, b: P) => `M${a[0].toFixed(1)} ${a[1].toFixed(1)}L${b[0].toFixed(1)} ${b[1].toFixed(1)}`;
const poly = (...pts: P[]) => `M${pts.map((p) => p.map((v) => v.toFixed(1)).join(" ")).join("L")}Z`;
const add = (a: P, d: P): P => [a[0] + d[0], a[1] + d[1]];

/** A face between top-left, top-right, bottom-left, bottom-right. */
type Quad = [tl: P, tr: P, bl: P, br: P];
const FLOORS = 34;
/** Floors crowd together towards the top, as they do looking up. */
const level = (j: number) => (j / FLOORS) ** 1.35;
const at = ([tl, tr, bl, br]: Quad, u: number, v: number) => lerp(lerp(tl, bl, v), lerp(tr, br, v), u);

function slabs(q: Quad, from = 0, to = FLOORS) {
  const d: string[] = [];
  for (let j = from; j <= to; j++) d.push(seg(at(q, 0, level(j)), at(q, 1, level(j))));
  return d.join("");
}
/** Columns, run a little past the top slab: the frame is still being erected. */
function columns(q: Quad, bays: number, over = 0.035) {
  const d: string[] = [];
  for (let i = 0; i <= bays; i++) d.push(seg(at(q, i / bays, -over), at(q, i / bays, 1)));
  return d.join("");
}
/** X-braces in the chosen bays, `rise` floors tall. */
function braces(q: Quad, bays: number[], of: number, rise: number) {
  const d: string[] = [];
  for (const i of bays)
    for (let j = 2; j + rise <= FLOORS; j += rise) {
      const [a, b, c, e] = [at(q, i / of, level(j)), at(q, (i + 1) / of, level(j)), at(q, i / of, level(j + rise)), at(q, (i + 1) / of, level(j + rise))];
      d.push(seg(a, e), seg(b, c));
    }
  return d.join("");
}

// Base corners sit below the frame (lost in the mist); the top converges into the sky.
const LEFT: Quad = [[205, 92], [300, 62], [60, 980], [300, 1030]];
const RIGHT: Quad = [[300, 62], [418, 96], [300, 1030], [575, 960]];

// Scaffolding stands just proud of the right face, over its lower floors.
const OUT: P = [12, 7];
const SCAFFOLD_TOP = 16;
function scaffold() {
  const d: string[] = [];
  const bays = 12;
  for (let i = 0; i <= bays; i++) d.push(seg(add(at(RIGHT, i / bays, level(SCAFFOLD_TOP)), OUT), add(at(RIGHT, i / bays, 1), OUT)));
  for (let j = SCAFFOLD_TOP; j < FLOORS; j++) {
    for (const k of [0, 0.5]) {
      const v = level(j + k);
      d.push(seg(add(at(RIGHT, 0, v), OUT), add(at(RIGHT, 1, v), OUT)));
    }
    // one diagonal per lift, alternating direction: the usual zig-zag of a scaffold face
    for (let i = 0; i < bays; i += 2) {
      const flip = (i / 2 + j) % 2 === 0;
      d.push(seg(add(at(RIGHT, (i + (flip ? 0 : 1)) / bays, level(j)), OUT), add(at(RIGHT, (i + (flip ? 1 : 0)) / bays, level(j + 1)), OUT)));
    }
  }
  return d.join("");
}

// A tower crane beside the building: a lattice mast, the jib and its counter-jib.
function crane() {
  const d: string[] = [];
  const [x0, x1, top, bottom] = [474, 488, 128, 960];
  d.push(seg([x0, top], [x0, bottom]), seg([x1, top], [x1, bottom]));
  for (let y = top; y < bottom; y += 16) d.push(seg([x0, y], [x1, y + 16]), seg([x0, y], [x1, y]));
  const [jibL, jibR, y0, y1] = [150, 600, 128, 140];
  d.push(seg([jibL, y1], [jibR, y1]), seg([jibL + 30, y0], [jibR, y0]), seg([jibL, y1], [jibL + 30, y0]));
  for (let x = jibL + 30; x < jibR; x += 18) d.push(seg([x, y0], [x + 9, y1]), seg([x + 9, y1], [x + 18, y0]));
  d.push(seg([x0, top], [481, 92]), seg([x1, top], [481, 92]), seg([481, 92], [jibL + 40, y0]), seg([481, 92], [590, y0]));
  d.push(seg([265, y1], [265, 330]), seg([258, 330], [272, 330]));
  return d.join("");
}

export function AuthArt() {
  return (
    <div className="bg-blueprint text-blueprint-line relative h-full w-full overflow-hidden">
      <svg viewBox="0 0 600 900" preserveAspectRatio="xMidYMin slice" className="absolute inset-0 size-full" fill="none" stroke="currentColor" aria-hidden>
        <defs>
          <radialGradient id="auth-sky" cx="50%" cy="20%" r="70%">
            <stop offset="0" stopColor="currentColor" stopOpacity="0.16" />
            <stop offset="1" stopColor="currentColor" stopOpacity="0" />
          </radialGradient>
          {/* light falls on the left face; the right one turns away into shade */}
          <linearGradient id="auth-lit" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0" stopColor="currentColor" stopOpacity="0.16" />
            <stop offset="1" stopColor="currentColor" stopOpacity="0.07" />
          </linearGradient>
        </defs>
        <rect width="600" height="900" fill="url(#auth-sky)" stroke="none" />

        <path d={crane()} strokeOpacity={0.22} strokeWidth={0.8} />

        <path d={poly(LEFT[0], LEFT[1], LEFT[3], LEFT[2])} fill="url(#auth-lit)" stroke="none" />
        <path d={poly(RIGHT[0], RIGHT[1], RIGHT[3], RIGHT[2])} fill="currentColor" fillOpacity={0.035} stroke="none" />

        <g strokeWidth={0.7} strokeOpacity={0.18}>
          <path d={columns(LEFT, 14, 0)} />
          <path d={columns(RIGHT, 18, 0)} />
        </g>
        <g strokeWidth={1.1} strokeOpacity={0.5}>
          <path d={slabs(LEFT)} />
          <path d={slabs(RIGHT)} />
          <path d={columns(LEFT, 7)} />
          <path d={columns(RIGHT, 9)} />
        </g>
        <path d={braces(LEFT, [0, 6], 7, 4) + braces(RIGHT, [4], 9, 4)} strokeWidth={0.8} strokeOpacity={0.3} />
        <path d={seg(LEFT[1], LEFT[3])} strokeWidth={1.6} strokeOpacity={0.75} />

        <path d={scaffold()} strokeWidth={0.7} strokeOpacity={0.4} />
      </svg>

      {/* mist at the foot of the tower, deep enough to carry the headline */}
      <div className="from-blueprint via-blueprint/80 absolute inset-x-0 bottom-0 h-1/2 bg-gradient-to-t to-transparent" />

      <h2 className="absolute right-10 bottom-12 left-10 max-w-md text-4xl leading-tight font-bold tracking-tight text-white xl:text-5xl">
        Find the RFI before the field does.
      </h2>
    </div>
  );
}
