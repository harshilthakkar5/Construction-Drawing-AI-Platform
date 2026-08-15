import { useEffect, useState } from "react";
import type { UserDto } from "@cdip/shared";
import {
  Building2Icon,
  EyeIcon,
  EyeOffIcon,
  LockIcon,
  MailIcon,
  UserIcon,
} from "lucide-react";
import { api, authToken } from "@/api";
import { BlueprintArt } from "@/components/BlueprintArt";
import { Logo } from "@/components/Logo";
import { ModeToggle } from "@/components/theme-provider";
import { Button } from "@/components/ui/button";
import { Input as TextInput } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";

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
      <div className="bg-background relative flex flex-col overflow-y-auto px-6 py-8 sm:px-12">
        <div className="flex items-center justify-between">
          <Logo />
          <ModeToggle />
        </div>

        <div className="mx-auto flex w-full max-w-sm flex-1 flex-col justify-center py-10">
          <h1 className="text-center text-[28px] font-bold tracking-tight">
            {HEADINGS[mode].title}
          </h1>
          <p className="mt-1.5 text-center text-sm text-muted-foreground">{HEADINGS[mode].subtitle}</p>

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
                    icon={<UserIcon className="size-4" />}
                    placeholder="Enter your first name"
                    value={firstName}
                    onChange={setFirstName}
                    autoComplete="given-name"
                    required
                  />
                </Field>
                <Field label="Last Name">
                  <Input
                    icon={<UserIcon className="size-4" />}
                    placeholder="Enter your last name"
                    value={lastName}
                    onChange={setLastName}
                    autoComplete="family-name"
                  />
                </Field>
                <Field label="Company Name (Optional)">
                  <Input
                    icon={<Building2Icon className="size-4" />}
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
                icon={<MailIcon className="size-4" />}
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
                  <Button
                    type="button"
                    variant="link"
                    className="h-auto p-0 text-sm"
                    onClick={() => switchMode("forgot")}
                  >
                    Forgot your password?
                  </Button>
                )
              }
            >
              <Input
                icon={<LockIcon className="size-4" />}
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
                    className="text-muted-foreground hover:text-foreground"
                    onClick={() => setShowPassword((v) => !v)}
                    aria-label={showPassword ? "Hide password" : "Show password"}
                  >
                    {showPassword ? (
                      <EyeOffIcon className="size-4" />
                    ) : (
                      <EyeIcon className="size-4" />
                    )}
                  </button>
                }
              />
            </Field>

            {isRegister && (
              <label className="flex items-start gap-2 text-sm text-muted-foreground">
                <input
                  type="checkbox"
                  className="mt-0.5 h-4 w-4 rounded border-border accent-primary"
                  checked={agreed}
                  onChange={(e) => setAgreed(e.target.checked)}
                  required
                />
                <span>
                  I agree to the <span className="text-primary">terms of service</span> and{" "}
                  <span className="text-primary">privacy policy</span>
                </span>
              </label>
            )}

            {error && (
              <p className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive" role="alert">
                {error}
              </p>
            )}

            <Button type="submit" className="w-full" disabled={busy || (isRegister && !agreed)}>
              {busy ? "Please wait…" : isRegister ? "Register" : "Login"}
            </Button>
          </form>
          )}

          {(mode === "login" || mode === "register") && (
            <p className="mt-6 text-center text-sm text-muted-foreground">
              {isRegister ? "Already have an account? " : "Don't have an account? "}
              <Button
                type="button"
                variant="link"
                className="h-auto p-0 font-medium"
                onClick={() => switchMode(isRegister ? "login" : "register")}
              >
                {isRegister ? "Login" : "Sign up"}
              </Button>
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
        <p className="rounded-md bg-success/10 px-3 py-2.5 text-sm text-success" role="status">
          If that address has an account, a reset link is on its way. It expires in an hour
          and works once.
        </p>
        <p className="text-sm text-muted-foreground">
          Nothing arrived? Check spam, or{" "}
          <Button
            type="button"
            variant="link"
            className="h-auto p-0"
            onClick={() => setSent(false)}
          >
            try another address
          </Button>
          .
        </p>
        <Button type="button" variant="outline" className="w-full" onClick={onBack}>
          Back to login
        </Button>
      </div>
    );
  }

  return (
    <form className="mt-8 space-y-4" onSubmit={submit}>
      <Field label="Email">
        <Input
          icon={<MailIcon className="size-4" />}
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
        <p className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive" role="alert">
          {error}
        </p>
      )}

      <Button type="submit" className="w-full" disabled={busy || !email.trim()}>
        {busy ? "Sending…" : "Send reset link"}
      </Button>
      <Button type="button" variant="outline" className="w-full" onClick={onBack}>
        Back to login
      </Button>
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
      setError(friendlyError((err as Error).message, false));
      setBusy(false);
    }
  }

  if (checking) {
    return <p className="mt-8 text-center text-sm text-muted-foreground">Checking your link…</p>;
  }

  if (!valid) {
    return (
      <div className="mt-8 space-y-4">
        <p className="rounded-md bg-destructive/10 px-3 py-2.5 text-sm text-destructive" role="alert">
          This reset link has expired or was already used. Links last an hour and work once.
        </p>
        <Button type="button" className="w-full" onClick={onExpired}>
          Send a new link
        </Button>
      </div>
    );
  }

  const tooShort = password.length > 0 && password.length < 8;
  const mismatch = confirm.length > 0 && confirm !== password;

  return (
    <form className="mt-8 space-y-4" onSubmit={submit}>
      <Field label="New password">
        <Input
          icon={<LockIcon className="size-4" />}
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
              className="text-muted-foreground hover:text-foreground"
              onClick={() => setShow((v) => !v)}
              aria-label={show ? "Hide password" : "Show password"}
            >
              {show ? <EyeOffIcon className="size-4" /> : <EyeIcon className="size-4" />}
            </button>
          }
        />
      </Field>
      <Field label="Confirm new password">
        <Input
          icon={<LockIcon className="size-4" />}
          type={show ? "text" : "password"}
          placeholder="Type it again"
          value={confirm}
          onChange={setConfirm}
          autoComplete="new-password"
          required
        />
      </Field>

      {(tooShort || mismatch || error) && (
        <p className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive" role="alert">
          {error ?? (tooShort ? "Use at least 8 characters." : "The two passwords don't match.")}
        </p>
      )}

      <Button
        type="submit"
        className="w-full"
        disabled={busy || password.length < 8 || confirm !== password}
      >
        {busy ? "Saving…" : "Set new password"}
      </Button>
      <p className="text-center text-xs text-muted-foreground">
        Setting a new password signs out every other device.
      </p>
    </form>
  );
}

/** Turns the raw `POST /auth/... → 401 {...}` into something a person reads. */
function friendlyError(message: string, isRegister: boolean): string {
  if (message.includes("401") || message.includes("invalid credentials")) {
    return "That email or password is not right. Check both and try again.";
  }
  if (message.includes("409")) return "That email is already registered — try logging in.";
  if (message.includes("429")) return "Too many attempts. Wait a minute and try again.";
  if (message.includes("validation failed") || message.includes("400")) {
    return isRegister
      ? "Check your details — a valid email and a password of 8+ characters are required."
      : "Check your details — that does not look like a valid email address.";
  }
  if (message.includes("Failed to fetch") || message.includes("NetworkError")) {
    return "Can't reach the server. Check your connection and try again.";
  }
  if (message.includes("500") || message.includes("503")) {
    return "The server had a problem with that request. Try again in a moment.";
  }
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
        <Label asChild>
          <span>{label}</span>
        </Label>
        {aside}
      </span>
      {children}
    </label>
  );
}

/** shadcn Input with room for a leading glyph and a trailing control. */
function Input({
  icon,
  trailing,
  value,
  onChange,
  className,
  ...rest
}: {
  icon?: React.ReactNode;
  trailing?: React.ReactNode;
  value: string;
  onChange: (v: string) => void;
} & Omit<React.InputHTMLAttributes<HTMLInputElement>, "value" | "onChange">) {
  return (
    <span className="relative block">
      {icon && (
        <span className="text-muted-foreground pointer-events-none absolute top-1/2 left-3 -translate-y-1/2">
          {icon}
        </span>
      )}
      <TextInput
        className={cn(icon && "pl-9", trailing && "pr-9", className)}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        {...rest}
      />
      {trailing && (
        <span className="absolute top-1/2 right-3 -translate-y-1/2">{trailing}</span>
      )}
    </span>
  );
}
