import { useState } from "react";
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
  const [mode, setMode] = useState<"login" | "register">("login");
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

  function switchMode(next: "login" | "register") {
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
            {isRegister ? "Create an account" : "Login to your account"}
          </h1>
          <p className="mt-1.5 text-center text-sm text-ink-muted">
            {isRegister
              ? "Enter your details to create a new account"
              : "Enter your email below to login to your account"}
          </p>

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
                  <span
                    className="text-sm text-ink-soft"
                    title="Password reset isn't wired up yet — ask an admin to reset it."
                  >
                    Forgot your password?
                  </span>
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
        </div>
      </div>

      <div className="hidden lg:block">
        <BlueprintArt variant={isRegister ? "elevation" : "plan"} />
      </div>
    </div>
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
