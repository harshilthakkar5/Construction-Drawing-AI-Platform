import { useEffect } from "react";
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

// --- Feedback: spinners, loading states, modals ---------------------------

/** Inline activity indicator. Sized in `em` so it matches its text. */
export function Spinner({ className = "" }: { className?: string }) {
  return (
    <svg
      className={`h-[1em] w-[1em] animate-spin ${className}`}
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden
    >
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path
        className="opacity-75"
        fill="currentColor"
        d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z"
      />
    </svg>
  );
}

/**
 * Whole-page loading state, used while a view's primary query is in flight.
 * `role="status"` so a screen reader announces the wait rather than silence.
 */
export function PageLoading({ label = "Loading…" }: { label?: string }) {
  return (
    <div
      role="status"
      className="grid min-h-[40vh] place-items-center gap-3 text-sm text-ink-muted"
    >
      <div className="flex items-center gap-2">
        <Spinner className="text-brand-600" />
        <span>{label}</span>
      </div>
    </div>
  );
}

/** Grey placeholder block, for list/card skeletons. */
export function Skeleton({ className = "" }: { className?: string }) {
  return <div className={`animate-pulse rounded bg-page ${className}`} aria-hidden />;
}

/**
 * Modal shell: backdrop click and Escape both cancel, the panel stops
 * propagation, and focus is trapped only loosely (single-purpose dialogs).
 */
export function Modal({
  title,
  onClose,
  children,
  footer,
  width = "max-w-md",
}: {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
  footer?: React.ReactNode;
  width?: string;
}) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      onClick={onClose}
      role="presentation"
    >
      <div
        className={`w-full ${width} rounded-lg bg-surface p-4 shadow-xl`}
        onClick={(event) => event.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <h2 className="font-semibold text-ink">{title}</h2>
        {children}
        {footer && <div className="mt-4 flex justify-end gap-2">{footer}</div>}
      </div>
    </div>
  );
}

/** Secondary/neutral modal button. */
export function GhostButton({
  children,
  onClick,
  disabled,
}: {
  children: React.ReactNode;
  onClick?: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="rounded-md border border-hairline px-3 py-1.5 text-xs text-ink-soft transition hover:bg-page disabled:opacity-60"
    >
      {children}
    </button>
  );
}

/**
 * Confirmation before an action that is hard or impossible to undo.
 *
 * `tone="danger"` colours the confirm button red and is meant for deletions —
 * where the dialog's job is to state plainly WHAT disappears, not just to ask
 * "are you sure". The button stays disabled while the mutation runs so a
 * double-click can't fire it twice, and a failure is shown in place instead of
 * closing the dialog and losing the error.
 */
export function ConfirmDialog({
  title,
  message,
  confirmLabel = "Confirm",
  busyLabel = "Working…",
  tone = "default",
  busy = false,
  error,
  onConfirm,
  onCancel,
}: {
  title: string;
  message: React.ReactNode;
  confirmLabel?: string;
  busyLabel?: string;
  tone?: "default" | "danger";
  busy?: boolean;
  error?: unknown;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <Modal
      title={title}
      onClose={() => !busy && onCancel()}
      footer={
        <>
          <GhostButton onClick={onCancel} disabled={busy}>
            Cancel
          </GhostButton>
          <button
            type="button"
            onClick={onConfirm}
            disabled={busy}
            className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium text-white transition disabled:opacity-50 ${
              tone === "danger"
                ? "bg-red-600 hover:bg-red-700"
                : "bg-brand-600 hover:bg-brand-700"
            }`}
          >
            {busy && <Spinner />}
            {busy ? busyLabel : confirmLabel}
          </button>
        </>
      }
    >
      <div className="mt-3 text-sm leading-relaxed text-ink-soft">{message}</div>
      {!!error && (
        <p className="mt-3 text-xs text-red-600">{(error as Error).message}</p>
      )}
    </Modal>
  );
}
