import { useState } from "react";
import type { UserDto } from "@cdip/shared";
import { api, authToken } from "../api";

/** Phase 5 RBAC: email/password sign-in backed by /auth. The bearer token is
 * kept in localStorage and attached to every API call (and, for media
 * elements that can't send headers, as a ?token= query parameter). */
export function AuthScreen({ onSignedIn }: { onSignedIn: (user: UserDto) => void }) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res =
        mode === "login"
          ? await api.login({ email, password })
          : await api.register({ email, name, password });
      authToken.set(res.token);
      onSignedIn(res.user);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex h-full items-center justify-center bg-gray-50">
      <form onSubmit={submit} className="w-80 rounded-lg border border-gray-200 bg-white p-6 shadow-sm">
        <h2 className="text-lg font-semibold">
          {mode === "login" ? "Sign in" : "Create an account"}
        </h2>
        <div className="mt-4 space-y-3">
          <input
            className="w-full rounded border border-gray-300 px-3 py-2 text-sm"
            type="email"
            placeholder="Email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
          />
          {mode === "register" && (
            <input
              className="w-full rounded border border-gray-300 px-3 py-2 text-sm"
              placeholder="Name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
            />
          )}
          <input
            className="w-full rounded border border-gray-300 px-3 py-2 text-sm"
            type="password"
            placeholder="Password (min 8 characters)"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            minLength={8}
            required
          />
        </div>
        {error && <p className="mt-3 text-xs text-red-600">{error}</p>}
        <button
          className="mt-4 w-full rounded bg-blue-600 px-3 py-2 text-sm font-medium text-white disabled:opacity-50"
          disabled={busy}
        >
          {busy ? "…" : mode === "login" ? "Sign in" : "Register"}
        </button>
        <button
          type="button"
          className="mt-3 w-full text-center text-xs text-blue-600 hover:underline"
          onClick={() => {
            setMode(mode === "login" ? "register" : "login");
            setError(null);
          }}
        >
          {mode === "login" ? "No account? Register" : "Have an account? Sign in"}
        </button>
      </form>
    </div>
  );
}
