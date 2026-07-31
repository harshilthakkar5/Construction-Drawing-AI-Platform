/**
 * ArcAligned AI wordmark. The glyph is an arc aligned to a drawing grid — a
 * quarter-round sweep over a plan grid, the shape the product is named for.
 */
export function Logo({ compact = false }: { compact?: boolean }) {
  return (
    <span className="flex items-center gap-2.5">
      <svg width="34" height="34" viewBox="0 0 34 34" aria-hidden className="shrink-0">
        <rect x="1" y="1" width="32" height="32" rx="9" className="fill-brand-700" />
        {/* plan grid */}
        <g stroke="white" strokeOpacity="0.32" strokeWidth="1">
          <path d="M11 6.5V27.5M22 6.5V27.5M6.5 12H27.5M6.5 22H27.5" />
        </g>
        {/* the aligned arc */}
        <path
          d="M9 25a16 16 0 0 1 16-16"
          fill="none"
          stroke="white"
          strokeWidth="2.6"
          strokeLinecap="round"
        />
        <circle cx="9" cy="25" r="2.1" fill="white" />
      </svg>
      {!compact && (
        <span className="leading-none">
          <span className="block text-[15px] font-semibold tracking-tight text-ink">
            ArcAligned
          </span>
          <span className="mt-0.5 block text-[11px] font-semibold uppercase tracking-[0.28em] text-ink-soft">
            AI
          </span>
        </span>
      )}
    </span>
  );
}
