import { cn } from "@/lib/utils";

/**
 * ArcAligned AI wordmark. The glyph is an arc aligned to a drawing grid — a
 * quarter-round sweep over a plan grid, the shape the product is named for.
 */
export function LogoMark({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 34 34"
      aria-hidden
      className={cn("bg-primary text-primary-foreground size-9 shrink-0 rounded-lg", className)}
    >
      {/* plan grid */}
      <g stroke="currentColor" strokeOpacity="0.35" strokeWidth="1">
        <path d="M11 6.5V27.5M22 6.5V27.5M6.5 12H27.5M6.5 22H27.5" />
      </g>
      {/* the aligned arc */}
      <path
        d="M9 25a16 16 0 0 1 16-16"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.6"
        strokeLinecap="round"
      />
      <circle cx="9" cy="25" r="2.1" fill="currentColor" />
    </svg>
  );
}

export function Logo({ compact = false }: { compact?: boolean }) {
  return (
    <span className="flex items-center gap-2.5">
      <LogoMark />
      {!compact && (
        <span className="text-[17px] leading-none font-bold tracking-tight">
          ArcAligned<span className="text-muted-foreground">.</span>AI
        </span>
      )}
    </span>
  );
}
