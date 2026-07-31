import type { CSSProperties } from "react";

/**
 * The animated drawing on the right half of the auth screens — a blueprint
 * that draws itself, in place of a stock photo. Two variants so sign-in and
 * sign-up don't look like the same page:
 *
 *   plan      → a floor plan in progress (login)
 *   elevation → a building section with floor lines (register)
 *
 * Every animated stroke carries pathLength="1" so one dash length drives them
 * all (see .bp-draw in index.css); `--delay` staggers the sequence.
 */
export type BlueprintVariant = "plan" | "elevation";

const delay = (seconds: number) => ({ "--delay": `${seconds}s` }) as CSSProperties;

export function BlueprintArt({ variant }: { variant: BlueprintVariant }) {
  return (
    <div className="relative h-full w-full overflow-hidden bg-brand-900">
      {/* graph paper */}
      <svg className="absolute inset-0 h-full w-full" aria-hidden>
        <defs>
          <pattern id="bp-grid" width="28" height="28" patternUnits="userSpaceOnUse">
            <path d="M28 0H0V28" fill="none" stroke="white" strokeOpacity="0.07" strokeWidth="1" />
          </pattern>
          <pattern id="bp-grid-lg" width="140" height="140" patternUnits="userSpaceOnUse">
            <path
              d="M140 0H0V140"
              fill="none"
              stroke="white"
              strokeOpacity="0.12"
              strokeWidth="1"
            />
          </pattern>
        </defs>
        <rect width="100%" height="100%" fill="url(#bp-grid)" />
        <rect width="100%" height="100%" fill="url(#bp-grid-lg)" />
      </svg>

      <div className="relative flex h-full w-full items-center justify-center p-10">
        <svg
          viewBox="0 0 420 420"
          className="h-full max-h-[560px] w-full max-w-[560px]"
          fill="none"
          stroke="white"
          strokeLinecap="round"
          strokeLinejoin="round"
          role="img"
          aria-label={
            variant === "plan"
              ? "Animated floor-plan drawing"
              : "Animated building elevation drawing"
          }
        >
          {variant === "plan" ? <PlanDrawing /> : <ElevationDrawing />}
        </svg>
      </div>

      <p className="absolute bottom-8 left-10 right-10 text-sm leading-relaxed text-white/55">
        {variant === "plan"
          ? "Every answer traced back to the sheet, page and region it came from."
          : "Upload a full drawing set — we build the searchable knowledge base."}
      </p>
    </div>
  );
}

/** Login: a plan with rooms, a door swing, and a dimension string. */
function PlanDrawing() {
  return (
    <>
      <path
        className="bp-draw"
        style={delay(0.1)}
        pathLength="1"
        d="M60 90h300v250H60z"
        strokeWidth="2.4"
        strokeOpacity="0.95"
      />
      <path
        className="bp-draw"
        style={delay(0.7)}
        pathLength="1"
        d="M200 90v250M60 220h140M200 250h160"
        strokeWidth="1.8"
        strokeOpacity="0.8"
      />
      {/* door swing */}
      <path
        className="bp-draw"
        style={delay(1.3)}
        pathLength="1"
        d="M132 220a34 34 0 0 0 34-34"
        strokeWidth="1.4"
        strokeOpacity="0.55"
      />
      {/* stair run */}
      <path
        className="bp-draw"
        style={delay(1.5)}
        pathLength="1"
        d="M230 275h100M230 290h100M230 305h100M230 320h100"
        strokeWidth="1.2"
        strokeOpacity="0.5"
      />
      {/* dimension string */}
      <path
        className="bp-draw"
        style={delay(1.9)}
        pathLength="1"
        d="M60 62h300M60 56v12M200 56v12M360 56v12"
        strokeWidth="1.2"
        strokeOpacity="0.6"
      />
      <g className="bp-fade" style={delay(2.6)}>
        <text x="118" y="50" fill="white" fillOpacity="0.55" fontSize="12" stroke="none">
          14' - 0"
        </text>
        <text x="262" y="50" fill="white" fillOpacity="0.55" fontSize="12" stroke="none">
          16' - 0"
        </text>
        <text x="86" y="130" fill="white" fillOpacity="0.5" fontSize="12" stroke="none">
          A-101
        </text>
      </g>
      {/* highlighted region — the FR-19 citation box */}
      <g className="bp-fade bp-sweep" style={delay(3)}>
        <rect
          x="214"
          y="262"
          width="132"
          height="72"
          rx="3"
          fill="#f0b429"
          fillOpacity="0.16"
          stroke="#f0b429"
          strokeOpacity="0.8"
          strokeWidth="1.6"
        />
      </g>
    </>
  );
}

/** Register: an elevation with floor lines, columns and a section marker. */
function ElevationDrawing() {
  return (
    <>
      <path
        className="bp-draw"
        style={delay(0.1)}
        pathLength="1"
        d="M90 340V150l120-70 120 70v190z"
        strokeWidth="2.4"
        strokeOpacity="0.95"
      />
      {/* floor lines */}
      <path
        className="bp-draw"
        style={delay(0.7)}
        pathLength="1"
        d="M90 200h240M90 250h240M90 300h240"
        strokeWidth="1.5"
        strokeOpacity="0.75"
      />
      {/* columns */}
      <path
        className="bp-draw"
        style={delay(1.1)}
        pathLength="1"
        d="M150 340V172M210 340V150M270 340V172"
        strokeWidth="1.3"
        strokeOpacity="0.55"
      />
      {/* openings */}
      <path
        className="bp-draw"
        style={delay(1.5)}
        pathLength="1"
        d="M112 214h28v22h-28zM172 214h28v22h-28zM232 214h28v22h-28zM292 214h28v22h-28zM112 264h28v22h-28zM232 264h28v22h-28z"
        strokeWidth="1.2"
        strokeOpacity="0.6"
      />
      {/* grade line */}
      <path
        className="bp-draw"
        style={delay(1.9)}
        pathLength="1"
        d="M50 340h320"
        strokeWidth="2"
        strokeOpacity="0.8"
      />
      <path
        className="bp-draw"
        style={delay(2.1)}
        pathLength="1"
        d="M56 352l10-12M86 352l10-12M116 352l10-12M296 352l10-12M326 352l10-12M356 352l10-12"
        strokeWidth="1"
        strokeOpacity="0.45"
      />
      {/* level tags */}
      <g className="bp-fade" style={delay(2.6)}>
        <text x="342" y="204" fill="white" fillOpacity="0.55" fontSize="11" stroke="none">
          LEVEL 3
        </text>
        <text x="342" y="254" fill="white" fillOpacity="0.55" fontSize="11" stroke="none">
          LEVEL 2
        </text>
        <text x="342" y="304" fill="white" fillOpacity="0.55" fontSize="11" stroke="none">
          LEVEL 1
        </text>
        <text x="180" y="372" fill="white" fillOpacity="0.5" fontSize="12" stroke="none">
          A-201 · SOUTH
        </text>
      </g>
      <g className="bp-fade bp-sweep" style={delay(3)}>
        <circle cx="210" cy="120" r="18" stroke="#f0b429" strokeOpacity="0.8" strokeWidth="1.6" />
        <path d="M192 120h36" stroke="#f0b429" strokeOpacity="0.8" strokeWidth="1.6" />
      </g>
    </>
  );
}
