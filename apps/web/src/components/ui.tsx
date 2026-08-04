import { CardArt, type ArtMotif } from "./CardArt";

/** Shared page furniture: header, card, stat tile, form controls. */

export function PageHeader({
  title,
  subtitle,
  action,
}: {
  title: string;
  subtitle?: React.ReactNode;
  action?: React.ReactNode;
}) {
  return (
    <div className="mb-6 flex flex-wrap items-start justify-between gap-3">
      <div>
        <h1 className="text-[32px] font-bold leading-tight tracking-tight text-ink">{title}</h1>
        {subtitle && <p className="mt-1 max-w-prose text-sm text-ink-soft">{subtitle}</p>}
      </div>
      {action}
    </div>
  );
}

/** The page's primary call to action (accent, icon-led). */
export function ActionButton({
  children,
  onClick,
  icon,
}: {
  children: React.ReactNode;
  onClick?: () => void;
  icon?: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex items-center gap-2 rounded-xl bg-accent-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-accent-700"
    >
      {icon}
      {children}
    </button>
  );
}

export function Card({
  title,
  subtitle,
  action,
  art,
  className = "",
  children,
}: {
  title?: string;
  subtitle?: string;
  action?: React.ReactNode;
  /** Faint corner motif — decoration only (see CardArt). */
  art?: ArtMotif;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <section
      className={`relative overflow-hidden rounded-2xl border border-hairline bg-surface p-5 shadow-[0_1px_2px_rgba(16,16,20,0.04)] ${className}`}
    >
      {art && <CardArt motif={art} />}
      {(title || action) && (
        <header className="relative mb-4 flex items-start justify-between gap-3">
          <div>
            {title && <h2 className="text-[17px] font-semibold text-ink">{title}</h2>}
            {subtitle && <p className="mt-0.5 text-xs text-ink-muted">{subtitle}</p>}
          </div>
          {action}
        </header>
      )}
      <div className="relative">{children}</div>
    </section>
  );
}

/** Icon tints — identity per tile, not a data encoding. */
const TILE_TINTS = {
  violet: "bg-violet-50 text-violet-600",
  blue: "bg-accent-50 text-accent-600",
  green: "bg-emerald-50 text-emerald-600",
  indigo: "bg-brand-50 text-brand-600",
} as const;

export function StatTile({
  label,
  value,
  hint,
  icon,
  tint = "blue",
  art,
}: {
  label: string;
  value: string;
  hint?: string;
  icon?: React.ReactNode;
  tint?: keyof typeof TILE_TINTS;
  art?: ArtMotif;
}) {
  return (
    <div className="relative overflow-hidden rounded-2xl border border-hairline bg-surface p-5 shadow-[0_1px_2px_rgba(16,16,20,0.04)]">
      {art && <CardArt motif={art} />}
      <div className="relative flex items-center gap-3">
        {icon && (
          <span className={`grid h-11 w-11 shrink-0 place-items-center rounded-xl ${TILE_TINTS[tint]}`}>
            {icon}
          </span>
        )}
        <p className="text-xs font-semibold uppercase tracking-wide text-ink-muted">{label}</p>
      </div>
      <p className="relative mt-3 text-[34px] font-bold leading-none tracking-tight text-ink">
        {value}
      </p>
      {hint && <p className="relative mt-2 text-xs text-ink-muted">{hint}</p>}
    </div>
  );
}

export function TextField({
  label,
  value,
  onChange,
  type = "text",
  placeholder,
  required,
  autoComplete,
  minLength,
  disabled,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  type?: string;
  placeholder?: string;
  required?: boolean;
  autoComplete?: string;
  minLength?: number;
  disabled?: boolean;
}) {
  return (
    <label className="block">
      <span className="block pb-1.5 text-sm font-medium text-ink">{label}</span>
      <input
        className="w-full rounded-md border border-hairline bg-surface px-3 py-2 text-sm text-ink outline-none placeholder:text-ink-muted focus:border-brand-500 focus:ring-2 focus:ring-brand-100 disabled:bg-page disabled:text-ink-muted"
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        required={required}
        autoComplete={autoComplete}
        minLength={minLength}
        disabled={disabled}
      />
    </label>
  );
}

export function TextArea({
  label,
  value,
  onChange,
  placeholder,
  rows = 6,
  required,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  rows?: number;
  required?: boolean;
}) {
  return (
    <label className="block">
      <span className="block pb-1.5 text-sm font-medium text-ink">{label}</span>
      <textarea
        className="w-full rounded-md border border-hairline bg-surface px-3 py-2 text-sm text-ink outline-none placeholder:text-ink-muted focus:border-brand-500 focus:ring-2 focus:ring-brand-100"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        rows={rows}
        required={required}
      />
    </label>
  );
}

export function PrimaryButton({
  children,
  disabled,
  onClick,
  type = "submit",
  className = "",
}: {
  children: React.ReactNode;
  disabled?: boolean;
  onClick?: () => void;
  type?: "submit" | "button";
  className?: string;
}) {
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className={`rounded-md bg-brand-700 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-brand-600 disabled:opacity-60 ${className}`}
    >
      {children}
    </button>
  );
}

/** Small inline banner for form results. */
export function Notice({ tone, children }: { tone: "ok" | "error"; children: React.ReactNode }) {
  return (
    <p
      role={tone === "error" ? "alert" : "status"}
      className={`rounded-md px-3 py-2 text-sm ${
        tone === "ok" ? "bg-green-50 text-green-800" : "bg-red-50 text-red-700"
      }`}
    >
      {children}
    </p>
  );
}
