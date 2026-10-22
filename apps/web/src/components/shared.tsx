import { ArrowUpRightIcon, CalendarIcon, Loader2Icon, Maximize2Icon, Minimize2Icon } from "lucide-react";
import { useState } from "react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Calendar } from "@/components/ui/calendar";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Textarea } from "@/components/ui/textarea";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { LogoMark } from "@/components/Logo";
import { cn } from "@/lib/utils";

/**
 * App furniture built on the shadcn primitives in components/ui: the page
 * header, labelled form fields, inline notices and the two dialogs. Pages use
 * these instead of restating the same class strings.
 */

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
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        {subtitle && <p className="text-muted-foreground mt-1 max-w-prose text-sm">{subtitle}</p>}
      </div>
      {action}
    </div>
  );
}

/** A label + control pair; `id` wiring is handled here so callers stay short. */
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
  hint,
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
  hint?: React.ReactNode;
}) {
  const id = `field-${label.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`;
  return (
    <div className="grid gap-2">
      <Label htmlFor={id}>{label}</Label>
      <Input
        id={id}
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        required={required}
        autoComplete={autoComplete}
        minLength={minLength}
        disabled={disabled}
      />
      {hint && <p className="text-muted-foreground text-xs">{hint}</p>}
    </div>
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
  const id = `field-${label.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`;
  return (
    <div className="grid gap-2">
      <Label htmlFor={id}>{label}</Label>
      <Textarea
        id={id}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        rows={rows}
        required={required}
      />
    </div>
  );
}

/** Inline form result. */
export function Notice({ tone, children }: { tone: "ok" | "error"; children: React.ReactNode }) {
  return (
    <Alert variant={tone === "ok" ? "success" : "destructive"} role={tone === "ok" ? "status" : "alert"}>
      <AlertDescription>{children}</AlertDescription>
    </Alert>
  );
}

// --- Feedback: spinners, loading states, dialogs ---------------------------

/** Inline activity indicator, sized in `em` so it matches its text. */
export function Spinner({ className = "" }: { className?: string }) {
  return <Loader2Icon className={cn("size-[1em] animate-spin", className)} aria-hidden />;
}

/** Whole-page loading state, used while a view's primary query is in flight. */
export function PageLoading({ label = "Loading…" }: { label?: string }) {
  return (
    <div role="status" className="grid min-h-[40vh] place-content-center justify-items-center">
      <span className="relative grid size-20 place-items-center">
        {/* A single accent arc turning on a faint track, around the mark. */}
        <svg className="absolute inset-0 size-20 animate-spin" viewBox="0 0 80 80" aria-hidden>
          <circle cx="40" cy="40" r="36" fill="none" className="stroke-border" strokeWidth="3" />
          <circle
            cx="40"
            cy="40"
            r="36"
            fill="none"
            className="stroke-primary"
            strokeWidth="3"
            strokeLinecap="round"
            strokeDasharray="56 170"
          />
        </svg>
        <LogoMark className="size-10 animate-pulse" />
      </span>
      <span className="text-muted-foreground mt-4 text-sm">{label}</span>
    </div>
  );
}

/**
 * Modal shell. Escape and a backdrop click both cancel; Radix owns focus
 * trapping and scroll locking.
 */
export function Modal({
  title,
  description,
  onClose,
  children,
  footer,
  className,
}: {
  title: string;
  description?: React.ReactNode;
  onClose: () => void;
  children?: React.ReactNode;
  footer?: React.ReactNode;
  className?: string;
}) {
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className={className}>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          {description && <DialogDescription>{description}</DialogDescription>}
        </DialogHeader>
        {children}
        {footer && <DialogFooter>{footer}</DialogFooter>}
      </DialogContent>
    </Dialog>
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
    <Dialog open onOpenChange={(open) => !open && !busy && onCancel()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription asChild>
            <div className="text-sm leading-relaxed">{message}</div>
          </DialogDescription>
        </DialogHeader>
        {!!error && <p className="text-destructive text-xs">{(error as Error).message}</p>}
        <DialogFooter>
          <Button variant="outline" onClick={onCancel} disabled={busy}>
            Cancel
          </Button>
          <Button
            variant={tone === "danger" ? "destructive" : "default"}
            onClick={onConfirm}
            disabled={busy}
          >
            {busy && <Spinner />}
            {busy ? busyLabel : confirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** A sheet reference that opens it in the viewer: the sheet number in mono, the page beside it. */
export function SheetLink({
  sheet,
  page,
  onOpen,
  title,
}: {
  sheet: string | null;
  page: number | null;
  onOpen: () => void;
  title?: string;
}) {
  return (
    <button
      type="button"
      onClick={onOpen}
      disabled={page === null}
      title={title ?? (page === null ? "This sheet is no longer in the project" : `Open page ${page}`)}
      className="border-primary/40 bg-primary/5 text-primary hover:bg-primary/10 hover:border-primary focus-visible:ring-ring/50 inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 align-middle font-mono text-xs font-medium whitespace-nowrap transition-colors outline-none focus-visible:ring-[3px] disabled:pointer-events-none disabled:opacity-60"
    >
      {sheet ?? `page ${page}`}
      {sheet && page !== null && <span className="text-muted-foreground font-normal">p.{page}</span>}
      <ArrowUpRightIcon className="size-3" />
    </button>
  );
}

/** Maximize / restore a pane — the same control the chat header already had. */
export function FullViewButton({
  expanded,
  label,
  onClick,
}: {
  expanded: boolean;
  label: string;
  onClick: () => void;
}) {
  const text = expanded ? "Exit full view (Esc)" : `Full view of ${label}`;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button variant="ghost" size="icon-sm" onClick={onClick} aria-label={text} aria-pressed={expanded}>
          {expanded ? <Minimize2Icon /> : <Maximize2Icon />}
        </Button>
      </TooltipTrigger>
      <TooltipContent side="bottom">{text}</TooltipContent>
    </Tooltip>
  );
}

/** "YYYY-MM-DD" ⇄ a local Date, so a picked day never shifts with the time zone. */
const toDay = (value: string): Date | undefined => {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  return m ? new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])) : undefined;
};
const fromDay = (d: Date): string =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

/**
 * shadcn's date picker (Popover + Calendar). Takes and gives the same
 * "YYYY-MM-DD" string a native date input did, "" for no date.
 */
export function DatePicker({
  value,
  onChange,
  placeholder = "Pick a date",
  label,
  className,
  size = "default",
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  label?: string;
  className?: string;
  size?: "default" | "sm";
}) {
  const [open, setOpen] = useState(false);
  const day = toDay(value);
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          size={size}
          aria-label={label}
          className={cn("justify-start font-normal", !day && "text-muted-foreground", className)}
        >
          <CalendarIcon />
          {day ? day.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : placeholder}
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-auto p-0" align="start">
        <Calendar
          mode="single"
          selected={day}
          defaultMonth={day}
          onSelect={(d) => {
            onChange(d ? fromDay(d) : "");
            setOpen(false);
          }}
        />
        {day && (
          <div className="border-t p-2">
            <Button variant="ghost" size="sm" className="w-full" onClick={() => { onChange(""); setOpen(false); }}>
              Clear
            </Button>
          </div>
        )}
      </PopoverContent>
    </Popover>
  );
}
