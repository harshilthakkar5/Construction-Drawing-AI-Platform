/** Shared page furniture: header, card, stat tile, form controls. */

export function PageHeader({
  title,
  subtitle,
  action,
}: {
  title: string;
  subtitle?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="mb-6 flex flex-wrap items-start justify-between gap-3">
      <div>
        <h1 className="text-3xl font-bold tracking-tight text-ink">{title}</h1>
        {subtitle && <p className="mt-1 max-w-prose text-sm text-ink-soft">{subtitle}</p>}
      </div>
      {action}
    </div>
  );
}

export function Card({
  title,
  subtitle,
  action,
  className = "",
  children,
}: {
  title?: string;
  subtitle?: string;
  action?: React.ReactNode;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <section className={`rounded-xl border border-hairline bg-surface p-4 ${className}`}>
      {(title || action) && (
        <header className="mb-4 flex items-start justify-between gap-3">
          <div>
            {title && <h2 className="text-base font-semibold text-ink">{title}</h2>}
            {subtitle && <p className="text-xs text-ink-muted">{subtitle}</p>}
          </div>
          {action}
        </header>
      )}
      {children}
    </section>
  );
}

export function StatTile({
  label,
  value,
  hint,
  icon,
}: {
  label: string;
  value: string;
  hint?: string;
  icon?: React.ReactNode;
}) {
  return (
    <div className="rounded-xl border border-hairline bg-surface p-4">
      {icon && (
        <span className="mb-3 grid h-10 w-10 place-items-center rounded-lg bg-page text-brand-700">
          {icon}
        </span>
      )}
      <p className="text-xs font-medium uppercase tracking-wide text-ink-muted">{label}</p>
      <p className="mt-1 text-3xl font-bold tracking-tight text-ink">{value}</p>
      {hint && <p className="mt-0.5 text-xs text-ink-muted">{hint}</p>}
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
