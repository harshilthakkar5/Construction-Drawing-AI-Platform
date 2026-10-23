import building from "@/assets/brand/auth-building.jpg";

/**
 * The right half of the auth screens: the client's building photo, toned to the
 * theme's blueprint colour, drifting slowly under a drawing grid with a scan line.
 * Login and sign-up drift from opposite sides so the two pages don't look identical.
 */
export function AuthArt({ variant }: { variant: "login" | "register" }) {
  return (
    <div className="bg-blueprint relative h-full w-full overflow-hidden">
      <img
        src={building}
        alt=""
        draggable={false}
        className="auth-drift absolute inset-0 size-full object-cover opacity-80 grayscale contrast-125"
        style={{ transformOrigin: variant === "login" ? "70% 40%" : "30% 60%" }}
      />
      {/* tone the photo to the palette, then darken the bottom for the caption */}
      <div className="bg-blueprint absolute inset-0 mix-blend-color" />
      <div className="from-blueprint via-blueprint/30 absolute inset-0 bg-gradient-to-t to-transparent" />

      {/* drawing grid and a slow scan line, the "aligned" half of the picture */}
      <svg className="absolute inset-0 size-full" aria-hidden>
        <defs>
          <pattern id="auth-grid" width="28" height="28" patternUnits="userSpaceOnUse">
            <path d="M28 0H0V28" fill="none" stroke="white" strokeOpacity="0.06" />
          </pattern>
          <pattern id="auth-grid-lg" width="140" height="140" patternUnits="userSpaceOnUse">
            <path d="M140 0H0V140" fill="none" stroke="white" strokeOpacity="0.12" />
          </pattern>
        </defs>
        <rect width="100%" height="100%" fill="url(#auth-grid)" />
        <rect width="100%" height="100%" fill="url(#auth-grid-lg)" />
      </svg>
      <div className="auth-scan absolute inset-x-0 top-0 h-24 bg-gradient-to-b from-transparent via-white/10 to-transparent" />

      <p className="absolute right-10 bottom-8 left-10 text-sm leading-relaxed text-white/70">
        Every answer traced back to the sheet, page and region it came from.
      </p>
    </div>
  );
}
