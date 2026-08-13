import { createHash, randomBytes, timingSafeEqual } from "node:crypto";

/**
 * Reset-token primitives. Deliberately free of env and Redis imports so the
 * rules can be unit-tested — the storage layer lives in ./passwordReset.ts.
 */

/** How long a reset link stays usable. */
export const TOKEN_TTL_SECONDS = 60 * 60;
export const TOKEN_TTL_MINUTES = TOKEN_TTL_SECONDS / 60;

export function newToken(): string {
  return randomBytes(32).toString("hex");
}

/**
 * What gets STORED is this hash, never the token itself: a leaked Redis dump
 * then yields nothing usable, exactly as with password hashes.
 */
export function hashToken(token: string): string {
  return createHash("sha256").update(token).digest("hex");
}

/**
 * Shape check before touching Redis: 64 lowercase hex characters. Rejects junk
 * early and keeps arbitrary strings out of the key space.
 */
export function isWellFormed(token: unknown): token is string {
  return typeof token === "string" && /^[0-9a-f]{64}$/.test(token);
}

/**
 * Constant-time comparison. Not needed for the Redis lookup (there the key IS
 * the secret's hash), but exported so no caller hand-rolls `===` on a secret.
 * Guards the length first — timingSafeEqual throws on a mismatch.
 */
export function tokensMatch(a: string, b: string): boolean {
  const left = Buffer.from(a);
  const right = Buffer.from(b);
  return left.length === right.length && timingSafeEqual(left, right);
}
