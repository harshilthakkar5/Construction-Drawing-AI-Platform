import { redis } from "./redis.js";
import { hashToken, isWellFormed, newToken, TOKEN_TTL_SECONDS } from "./tokens.js";

export { hashToken, isWellFormed, tokensMatch, TOKEN_TTL_MINUTES } from "./tokens.js";

/**
 * Password-reset tokens.
 *
 * The token is a 32-byte random string handed to the user by email. What is
 * STORED is its SHA-256 hash, never the token itself: a leaked Redis dump then
 * yields nothing usable, exactly as with password hashes. Redis gives the
 * expiry for free, and consuming is an atomic GETDEL so a link cannot be
 * redeemed twice even if two requests race.
 *
 * The request endpoint must answer identically whether or not the address is
 * registered — otherwise it becomes an account-enumeration oracle — so the
 * throttle below is keyed on the email regardless of whether a user exists.
 */

/** Resets requested per email inside the window before we stop sending. */
const MAX_REQUESTS = 3;
const THROTTLE_WINDOW_SECONDS = 15 * 60;

const tokenKey = (hash: string) => `pwreset:${hash}`;
const throttleKey = (email: string) => `pwreset-throttle:${email}`;

/**
 * True when this address has already asked too many times recently. Counting
 * happens here so an attacker can't use the endpoint to flood someone's inbox,
 * and the count is per address rather than per IP because that is what the
 * mailbox owner experiences.
 */
export async function throttled(email: string): Promise<boolean> {
  const key = throttleKey(email.toLowerCase());
  const count = await redis.incr(key);
  if (count === 1) await redis.expire(key, THROTTLE_WINDOW_SECONDS);
  return count > MAX_REQUESTS;
}

export async function createResetToken(userId: string): Promise<string> {
  const token = newToken();
  await redis.set(tokenKey(hashToken(token)), userId, "EX", TOKEN_TTL_SECONDS);
  return token;
}

/** The user id a token points at, without consuming it (pre-flight check). */
export async function peekResetToken(token: string): Promise<string | null> {
  if (!isWellFormed(token)) return null;
  return redis.get(tokenKey(hashToken(token)));
}

/**
 * Redeem a token. Atomic: the key is deleted as it is read, so a token works
 * exactly once even under concurrent requests. Returns the user id, or null
 * when the token is unknown, already used, or expired.
 */
export async function consumeResetToken(token: string): Promise<string | null> {
  if (!isWellFormed(token)) return null;
  const key = tokenKey(hashToken(token));
  // GETDEL (Redis ≥ 6.2); fall back to a transaction on older servers.
  try {
    return await redis.getdel(key);
  } catch {
    const [[, value]] = (await redis.multi().get(key).del(key).exec()) as [
      [Error | null, string | null],
      [Error | null, number],
    ];
    return value;
  }
}
