import type { Request, Response } from "express";
import rateLimit, { ipKeyGenerator, type Options } from "express-rate-limit";
import { RedisStore } from "rate-limit-redis";
import { redis } from "./redis.js";

/**
 * Rate limiting, in three tiers, because the endpoints are not equally
 * expensive to abuse.
 *
 * The store is REDIS, not the default in-memory one. Counting in memory would
 * make every limit per-process, so running two API instances (or clustering)
 * would silently double every allowance — and the app is meant to scale out.
 * Redis is already a dependency for sessions and caches.
 *
 * Keyed by authenticated user where there is one, falling back to IP. A shared
 * office NAT would otherwise let one colleague exhaust the whole building's
 * allowance, and — more to the point — the limits that matter here protect a
 * per-account spend, not a network.
 *
 * Every limit is env-tunable, and setting a limit to 0 disables that tier
 * outright, so an operator is never stuck between "too strict" and editing
 * code.
 */

const store = () =>
  new RedisStore({
    // ioredis exposes `call`; the store's own typings expect node-redis.
    sendCommand: (...args: string[]) => redis.call(...(args as [string, ...string[]])) as never,
    prefix: "rl:",
  });

/**
 * The client IP, normalized. `ipKeyGenerator` collapses an IPv6 address to its
 * /64 subnet — without it a single user is handed 2^64 addresses to rotate
 * through, which is not a rate limit at all. express-rate-limit refuses to
 * start on a raw-IP key generator for exactly that reason.
 */
const ipKey = (req: Request): string => `ip:${ipKeyGenerator(req.ip ?? "unknown")}`;

/** The authenticated user id, or the client IP for unauthenticated routes. */
function keyFor(req: Request): string {
  const user = (req as Request & { user?: { id: string } }).user;
  return user ? `u:${user.id}` : ipKey(req);
}

const number = (name: string, fallback: number) => {
  const raw = process.env[name];
  const parsed = raw === undefined ? Number.NaN : Number(raw);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : fallback;
};

function limiter(
  name: string,
  windowMs: number,
  fallbackMax: number,
  message: string,
  extra: Partial<Options> = {},
) {
  const max = number(name, fallbackMax);
  if (max === 0) {
    // Explicitly disabled — hand back a pass-through rather than a limiter of
    // zero, which would reject everything.
    return (_req: Request, _res: Response, next: () => void) => next();
  }
  return rateLimit({
    windowMs,
    limit: max,
    store: store(),
    keyGenerator: keyFor,
    standardHeaders: "draft-7",
    legacyHeaders: false,
    message: { error: message },
    ...extra,
  });
}

/**
 * Everything behind a session. Generous: the viewer fires a request per page
 * image on a 1000-page set, so this is a runaway-client backstop, not a
 * throttle anyone should meet in normal use.
 */
export const generalLimiter = limiter(
  "RATE_LIMIT_GENERAL_PER_MINUTE",
  60_000,
  600,
  "too many requests — slow down",
);

/**
 * Unauthenticated flood guard, by IP, mounted BEFORE requireAuth.
 *
 * Every other tier below sits behind authentication, so a client sending
 * invalid tokens never reaches one — a load test measured ~2300 req/s of 401s
 * against a single process, none of it counted. Rejecting a bad token is
 * cheap, but it is also free to send, and it is the whole API's socket being
 * occupied. Set well above the general limit: this is a flood guard, not a
 * throttle, and it counts requests that legitimate clients also make.
 */
export const floodLimiter = limiter(
  "RATE_LIMIT_FLOOD_PER_MINUTE",
  60_000,
  1200,
  "too many requests — slow down",
  { keyGenerator: ipKey },
);

/**
 * Auth endpoints, by IP. Deliberately strict and counted on FAILURES only, so
 * a legitimate user signing in repeatedly is unaffected while credential
 * stuffing is not. Sessions live in Redis and passwords are scrypt-hashed, but
 * neither of those slows an attacker down on its own.
 */
export const authLimiter = limiter(
  "RATE_LIMIT_AUTH_PER_15MIN",
  15 * 60_000,
  20,
  "too many authentication attempts — try again later",
  { skipSuccessfulRequests: true, keyGenerator: ipKey },
);

/**
 * Chat. This is the one that protects MONEY: every request is a Voyage
 * embedding plus an LLM call against a shared provider quota. Without it one
 * user — or one loop in a client — can spend the account's entire budget and
 * rate-limit every other user out of the product.
 */
export const chatLimiter = limiter(
  "RATE_LIMIT_CHAT_PER_MINUTE",
  60_000,
  20,
  "too many questions in a short time — wait a moment before asking again",
);

/**
 * Summary runs. Rarer and far dearer than chat — a discipline is dozens to
 * hundreds of model calls — and the button is already behind a cost
 * confirmation, so anything above a handful an hour is a stuck client rather
 * than a person.
 */
export const summaryLimiter = limiter(
  "RATE_LIMIT_SUMMARY_PER_HOUR",
  60 * 60_000,
  30,
  "too many summary runs in an hour — wait before starting another",
);
