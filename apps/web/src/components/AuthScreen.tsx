import { useEffect, useState } from "react";
import type { UserDto } from "@cdip/shared";
import { api, authToken } from "../api";
import { BlueprintArt } from "./BlueprintArt";
import { Logo } from "./Logo";

/**
 * Split auth layout: form on the left, an animated blueprint on the right
 * (a different drawing per mode). Phase 5 RBAC underneath — the bearer token
 * is kept in localStorage and attached to every API call.
 */
export function AuthScreen({ onSignedIn }: { onSignedIn: (user: UserDto) => void }) {
  const [mode, setMode] = useState<AuthMode>("login");
  // A reset link lands on the app root as ?reset=<token>.
  const [resetToken, setResetToken] = useState<string | null>(null);
  const [email, setEmail] = useState("");
  const [firstName, setFirstName] = useState("");
  const [lastName, setLastName] = useState("");
  const [company, setCompany] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [agreed, setAgreed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const isRegister = mode === "register";

  useEffect(() => {
    const token = new URLSearchParams(window.location.search).get("reset");
    if (!token) return;
    setResetToken(token);
    setMode("reset");
    // Strip the token from the address bar: it is a credential, and leaving it
    // there puts it in browser history, screenshots and shared links.
    const url = new URL(window.location.href);
    url.searchParams.delete("reset");
    window.history.replaceState({}, "", url.toString());
  }, []);

  function switchMode(next: AuthMode) {
    setMode(next);
    setError(null);
    setPassword("");
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = isRegister
        ? await api.register({
            email,
            password,
            firstName: firstName.trim(),
            lastName: lastName.trim() || undefined,
            company: company.trim() || undefined,
          })
        : await api.login({ email, password });
      authToken.set(res.token);
      onSignedIn(res.user);
    } catch (err) {
      setError(friendlyError((err as Error).message, isRegister));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid h-full grid-cols-1 lg:grid-cols-2">
      <div className="relative flex flex-col overflow-y-auto bg-surface px-6 py-8 sm:px-12">
        <Logo />

        <div className="mx-auto flex w-full max-w-sm flex-1 flex-col justify-center py-10">
          <h1 className="text-center text-[28px] font-bold tracking-tight text-ink">
            {HEADINGS[mode].title}
          </h1>
          <p className="mt-1.5 text-center text-sm text-ink-muted">{HEADINGS[mode].subtitle}</p>

          {mode === "forgot" && <ForgotPasswordForm onBack={() => switchMode("login")} />}
          {mode === "reset" && (
            <ResetPasswordForm
              token={resetToken}
              onSignedIn={onSignedIn}
              onExpired={() => switchMode("forgot")}
            />
          )}

          {(mode === "login" || mode === "register") && (
          <form className="mt-8 space-y-4" onSubmit={submit}>
            {isRegister && (
              <>
                <Field label="First Name">
                  <Input
                    icon={<UserIcon />}
                    placeholder="Enter your first name"
                    value={firstName}
                    onChange={setFirstName}
                    autoComplete="given-name"
                    required
                  />
                </Field>
                <Field label="Last Name">
                  <Input
                    icon={<UserIcon />}
                    placeholder="Enter your last name"
                    value={lastName}
                    onChange={setLastName}
                    autoComplete="family-name"
                  />
                </Field>
                <Field label="Company Name (Optional)">
                  <Input
                    icon={<BuildingIcon />}
                    placeholder="Enter company name (optional)"
                    value={company}
                    onChange={setCompany}
                    autoComplete="organization"
                  />
                </Field>
              </>
            )}

            <Field label="Email">
              <Input
                icon={<MailIcon />}
                type="email"
                placeholder="Enter your email"
                value={email}
                onChange={setEmail}
                autoComplete="email"
                required
              />
            </Field>

            <Field
              label="Password"
              aside={
                !isRegister && (
                  <button
                    type="button"
                    className="text-sm text-brand-700 underline underline-offset-4"
                    onClick={() => switchMode("forgot")}
                  >
                    Forgot your password?
                  </button>
                )
              }
            >
              <Input
                icon={<LockIcon />}
                type={showPassword ? "text" : "password"}
                placeholder="Enter your password"
                value={password}
                onChange={setPassword}
                autoComplete={isRegister ? "new-password" : "current-password"}
                minLength={8}
                required
                trailing={
                  <button
                    type="button"
                    className="text-ink-muted hover:text-ink"
                    onClick={() => setShowPassword((v) => !v)}
                    aria-label={showPassword ? "Hide password" : "Show password"}
                  >
                    <EyeIcon off={showPassword} />
                  </button>
                }
              />
            </Field>

            {isRegister && (
              <label className="flex items-start gap-2 text-sm text-ink-soft">
                <input
                  type="checkbox"
                  className="mt-0.5 h-4 w-4 rounded border-hairline accent-brand-700"
                  checked={agreed}
                  onChange={(e) => setAgreed(e.target.checked)}
                  required
                />
                <span>
                  I agree to the <span className="text-brand-700">terms of service</span> and{" "}
                  <span className="text-brand-700">privacy policy</span>
                </span>
              </label>
            )}

            {error && (
              <p className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
                {error}
              </p>
            )}

            <button
              className="w-full rounded-md bg-brand-700 py-2.5 text-sm font-semibold text-white transition hover:bg-brand-600 disabled:opacity-60"
              disabled={busy || (isRegister && !agreed)}
            >
              {busy ? "Please wait…" : isRegister ? "Register" : "Login"}
            </button>
          </form>
          )}

          {(mode === "login" || mode === "register") && (
            <p className="mt-6 text-center text-sm text-ink-soft">
              {isRegister ? "Already have an account? " : "Don't have an account? "}
              <button
                type="button"
                className="font-medium text-ink underline underline-offset-4"
                onClick={() => switchMode(isRegister ? "login" : "register")}
              >
                {isRegister ? "Login" : "Sign up"}
              </button>
            </p>
          )}
        </div>
      </div>

      <div className="hidden lg:block">
        <BlueprintArt variant={isRegister ? "elevation" : "plan"} />
      </div>
    </div>
  );
}

type AuthMode = "login" | "register" | "forgot" | "reset";

const HEADINGS: Record<AuthMode, { title: string; subtitle: string }> = {
  login: {
    title: "Login to your account",
    subtitle: "Enter your email below to login to your account",
  },
  register: {
    title: "Create an account",
    subtitle: "Enter your details to create a new account",
  },
  forgot: {
    title: "Reset your password",
    subtitle: "We'll email you a link to choose a new one",
  },
  reset: {
    title: "Choose a new password",
    subtitle: "Pick something you haven't used here before",
  },
};

/**
 * Request a reset link.
 *
 * The confirmation is deliberately vague — "if that address has an account" —
 * and is shown whether or not the address is registered. Saying "no such user"
 * would turn this form into a way to test which addresses hold accounts, which
 * is the same reason the API answers identically either way.
 */
function ForgotPasswordForm({ onBack }: { onBack: () => void }) {
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.forgotPassword(email.trim());
      setSent(true);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (sent) {
    return (
      <div className="mt-8 space-y-4">
        <p className="rounded-md bg-green-50 px-3 py-2.5 text-sm text-green-800" role="status">
          If that address has an account, a reset link is on its way. It expires in an hour
          and works once.
        </p>
        <p className="text-sm text-ink-muted">
          Nothing arrived? Check spam, or{" "}
          <button
            type="button"
            className="text-brand-700 underline underline-offset-4"
            onClick={() => setSent(false)}
          >
            try another address
          </button>
          .
        </p>
        <button
          type="button"
          className="w-full rounded-md border border-hairline py-2.5 text-sm font-medium text-ink-soft transition hover:bg-page"
          onClick={onBack}
        >
          Back to login
        </button>
      </div>
    );
  }

  return (
    <form className="mt-8 space-y-4" onSubmit={submit}>
      <Field label="Email">
        <Input
          icon={<MailIcon />}
          type="email"
          placeholder="Enter your email"
          value={email}
          onChange={setEmail}
          autoComplete="email"
          required
          autoFocus
        />
      </Field>

      {error && (
        <p className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
          {error}
        </p>
      )}

      <button
        className="w-full rounded-md bg-brand-700 py-2.5 text-sm font-semibold text-white transition hover:bg-brand-600 disabled:opacity-60"
        disabled={busy || !email.trim()}
      >
        {busy ? "Sending…" : "Send reset link"}
      </button>
      <button
        type="button"
        className="w-full rounded-md border border-hairline py-2.5 text-sm font-medium text-ink-soft transition hover:bg-page"
        onClick={onBack}
      >
        Back to login
      </button>
    </form>
  );
}

/**
 * Redeem a reset link. The token is validated before the form is shown, so an
 * expired link says so immediately instead of after the user has typed a new
 * password twice.
 */
function ResetPasswordForm({
  token,
  onSignedIn,
  onExpired,
}: {
  token: string | null;
  onSignedIn: (user: UserDto) => void;
  onExpired: () => void;
}) {
  const [checking, setChecking] = useState(true);
  const [valid, setValid] = useState(false);
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) {
      setChecking(false);
      return;
    }
    api
      .checkResetToken(token)
      .then((r) => setValid(r.valid))
      .catch(() => setValid(false))
      .finally(() => setChecking(false));
  }, [token]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!token) return;
    setBusy(true);
    setError(null);
    try {
      const res = await api.resetPassword(token, password);
      authToken.set(res.token);
      onSignedIn(res.user);
    } catch (err) {
      setError((err as Error).message.replace(/^POST \S+ → \d+ /, ""));
      setBusy(false);
    }
  }

  if (checking) {
    return <p className="mt-8 text-center text-sm text-ink-muted">Checking your link…</p>;
  }

  if (!valid) {
    return (
      <div className="mt-8 space-y-4">
        <p className="rounded-md bg-red-50 px-3 py-2.5 text-sm text-red-700" role="alert">
          This reset link has expired or was already used. Links last an hour and work once.
        </p>
        <button
          type="button"
          className="w-full rounded-md bg-brand-700 py-2.5 text-sm font-semibold text-white transition hover:bg-brand-600"
          onClick={onExpired}
        >
          Send a new link
        </button>
      </div>
    );
  }

  const tooShort = password.length > 0 && password.length < 8;
  const mismatch = confirm.length > 0 && confirm !== password;

  return (
    <form className="mt-8 space-y-4" onSubmit={submit}>
      <Field label="New password">
        <Input
          icon={<LockIcon />}
          type={show ? "text" : "password"}
          placeholder="At least 8 characters"
          value={password}
          onChange={setPassword}
          autoComplete="new-password"
          minLength={8}
          required
          autoFocus
          trailing={
            <button
              type="button"
              className="text-ink-muted hover:text-ink"
              onClick={() => setShow((v) => !v)}
              aria-label={show ? "Hide password" : "Show password"}
            >
              <EyeIcon off={show} />
            </button>
          }
        />
      </Field>
      <Field label="Confirm new password">
        <Input
          icon={<LockIcon />}
          type={show ? "text" : "password"}
          placeholder="Type it again"
          value={confirm}
          onChange={setConfirm}
          autoComplete="new-password"
          required
        />
      </Field>

      {(tooShort || mismatch || error) && (
        <p className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
          {error ?? (tooShort ? "Use at least 8 characters." : "The two passwords don't match.")}
        </p>
      )}

      <button
        className="w-full rounded-md bg-brand-700 py-2.5 text-sm font-semibold text-white transition hover:bg-brand-600 disabled:opacity-60"
        disabled={busy || password.length < 8 || confirm !== password}
      >
        {busy ? "Saving…" : "Set new password"}
      </button>
      <p className="text-center text-xs text-ink-muted">
        Setting a new password signs out every other device.
      </p>
    </form>
  );
}

/** Turns the raw `POST /auth/... → 401 {...}` into something a person reads. */
function friendlyError(message: string, isRegister: boolean): string {
  if (message.includes("401")) return "Wrong email or password.";
  if (message.includes("409")) return "That email is already registered — try logging in.";
  if (message.includes("validation failed")) {
    return isRegister ? "Check your details — the password needs 8+ characters." : "Check your details.";
  }
  if (message.includes("Failed to fetch")) return "Can't reach the API. Is it running?";
  return message;
}

function Field({
  label,
  aside,
  children,
}: {
  label: string;
  aside?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="flex items-center justify-between pb-1.5">
        <span className="text-sm font-medium text-ink">{label}</span>
        {aside}
      </span>
      {children}
    </label>
  );
}

function Input({
  icon,
  trailing,
  value,
  onChange,
  ...rest
}: {
  icon?: React.ReactNode;
  trailing?: React.ReactNode;
  value: string;
  onChange: (v: string) => void;
} & Omit<React.InputHTMLAttributes<HTMLInputElement>, "value" | "onChange">) {
  return (
    <span className="flex items-center gap-2 rounded-md border border-hairline bg-surface px-3 py-2 focus-within:border-brand-500 focus-within:ring-2 focus-within:ring-brand-100">
      {icon && <span className="text-ink-muted">{icon}</span>}
      <input
        className="min-w-0 flex-1 bg-transparent text-sm text-ink outline-none placeholder:text-ink-muted"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        {...rest}
      />
      {trailing}
    </span>
  );
}

const iconProps = {
  width: 16,
  height: 16,
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.8,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
  "aria-hidden": true,
};

const MailIcon = () => (
  <svg {...iconProps}>
    <rect x="2" y="4" width="20" height="16" rx="2" />
    <path d="m2 7 10 6 10-6" />
  </svg>
);
const LockIcon = () => (
  <svg {...iconProps}>
    <rect x="4" y="10" width="16" height="11" rx="2" />
    <path d="M8 10V7a4 4 0 0 1 8 0v3" />
  </svg>
);
const UserIcon = () => (
  <svg {...iconProps}>
    <circle cx="12" cy="8" r="4" />
    <path d="M4 21a8 8 0 0 1 16 0" />
  </svg>
);
const BuildingIcon = () => (
  <svg {...iconProps}>
    <rect x="4" y="3" width="16" height="18" rx="1.5" />
    <path d="M9 7h1M14 7h1M9 11h1M14 11h1M9 15h1M14 15h1M10 21v-3h4v3" />
  </svg>
);
const EyeIcon = ({ off }: { off: boolean }) => (
  <svg {...iconProps}>
    <path d="M2 12s3.6-6 10-6 10 6 10 6-3.6 6-10 6S2 12 2 12Z" />
    <circle cx="12" cy="12" r="3" />
    {off && <path d="m4 20 16-16" />}
  </svg>
);
