/**
 * Watermark line art for the dashboard cards — a faint motif in the top-right
 * corner that hints at what the card is about.
 *
 * Deliberately recessive: single-stroke, no fills, very low opacity, and
 * `aria-hidden` + `pointer-events-none` so it never competes with the data or
 * reaches the accessibility tree. If a motif ever reads as a chart mark,
 * it's too strong.
 */
export type ArtMotif =
  | "buildings"
  | "blueprint"
  | "coins"
  | "chat"
  | "trend"
  | "document"
  | "columns"
  | "funnel"
  | "folder"
  | "pie";

const stroke = {
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.5,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
};

const MOTIFS: Record<ArtMotif, React.ReactNode> = {
  buildings: (
    <>
      <path d="M8 62V26l14-8 14 8v36" {...stroke} />
      <path d="M36 62V36l16-6v32" {...stroke} />
      <path d="M52 62V44l12 5v13" {...stroke} />
      <path d="M15 34h6M15 43h6M27 34h6M27 43h6M42 42h5M42 50h5" {...stroke} />
    </>
  ),
  blueprint: (
    <>
      <path d="M6 16h56v40H6z" {...stroke} />
      <path d="M28 16v40M6 38h22M28 30h34M46 38v18" {...stroke} />
      <path d="M12 22h8M34 22h8" {...stroke} strokeWidth={1} />
    </>
  ),
  coins: (
    <>
      <ellipse cx="34" cy="22" rx="18" ry="7" {...stroke} />
      <path d="M16 22v10c0 3.9 8 7 18 7s18-3.1 18-7V22" {...stroke} />
      <path d="M16 34v10c0 3.9 8 7 18 7s18-3.1 18-7V34" {...stroke} />
    </>
  ),
  chat: (
    <>
      <path d="M8 20h34a4 4 0 0 1 4 4v14a4 4 0 0 1-4 4H22l-9 7v-7h-5a4 4 0 0 1-4-4V24a4 4 0 0 1 4-4Z" {...stroke} />
      <path d="M52 30h6a4 4 0 0 1 4 4v12a4 4 0 0 1-4 4h-3v6l-7-6" {...stroke} />
    </>
  ),
  trend: (
    <>
      <path d="M6 52 22 36l10 9 14-19 12 10" {...stroke} />
      <path d="M52 26h12v12" {...stroke} />
      <path d="M12 60V44M24 60V48M36 60V50M48 60V40" {...stroke} strokeWidth={1} />
    </>
  ),
  document: (
    <>
      <path d="M14 8h22l14 14v34a2 2 0 0 1-2 2H14a2 2 0 0 1-2-2V10a2 2 0 0 1 2-2Z" {...stroke} />
      <path d="M36 8v14h14M20 32h20M20 40h20" {...stroke} />
      <circle cx="48" cy="48" r="10" {...stroke} />
      <path d="m43 48 3.5 3.5L53 45" {...stroke} />
    </>
  ),
  columns: (
    <>
      <path d="M6 24 34 10l28 14H6Z" {...stroke} />
      <path d="M10 24v26M22 24v26M34 24v26M46 24v26M58 24v26" {...stroke} />
      <path d="M6 50h56M4 56h60" {...stroke} />
    </>
  ),
  funnel: (
    <>
      <path d="M8 12h52L40 36v18l-12 6V36L8 12Z" {...stroke} />
      <path d="M18 20h34" {...stroke} strokeWidth={1} />
    </>
  ),
  folder: (
    <>
      <path d="M6 18h18l5 6h29a2 2 0 0 1 2 2v28a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V20a2 2 0 0 1 2-2Z" {...stroke} />
      <path d="M4 30h56" {...stroke} strokeWidth={1} />
    </>
  ),
  pie: (
    <>
      <circle cx="32" cy="34" r="22" {...stroke} />
      <path d="M32 34V12M32 34l18 12" {...stroke} />
      <path d="M54 34a22 22 0 0 1-8 17" {...stroke} strokeWidth={2.5} />
    </>
  ),
};

export function CardArt({ motif }: { motif: ArtMotif }) {
  return (
    <svg
      viewBox="0 0 68 68"
      className="pointer-events-none absolute -right-3 -top-2 h-[86px] w-[86px] text-ink opacity-[0.05]"
      aria-hidden
    >
      {MOTIFS[motif]}
    </svg>
  );
}
