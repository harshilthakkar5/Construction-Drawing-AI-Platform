import { randomBytes } from "node:crypto";
import type { NextFunction, Request, Response } from "express";
import type { User } from "@prisma/client";
import { prisma } from "./db.js";
import { redis } from "./redis.js";
import { resolveProjectAccess, type ProjectAccess } from "./security.js";

/**
 * Phase 5 auth + project RBAC.
 *
 * - Passwords: scrypt via ./security.ts, stored as "salt:hash" hex.
 * - Sessions: opaque bearer tokens in Redis ("Redis for cache/sessions" per
 *   CLAUDE.md) with a sliding TTL — nothing secret is stored client-side but
 *   the random token.
 * - RBAC (owner/member): the project owner manages the project and its
 *   members; members can view, upload, and chat. Projects created before auth
 *   existed (ownerId NULL) are open to any authenticated user.
 */

const SESSION_TTL_SECONDS = 7 * 24 * 3600;
const sessionKey = (token: string) => `session:${token}`;
/** Reverse index: which sessions belong to a user, so all can be revoked. */
const userSessionsKey = (userId: string) => `user-sessions:${userId}`;

export { hashPassword, verifyPassword } from "./security.js";
export type { ProjectAccess } from "./security.js";

export async function createSession(userId: string): Promise<string> {
  const token = randomBytes(32).toString("hex");
  await redis
    .multi()
    .set(sessionKey(token), userId, "EX", SESSION_TTL_SECONDS)
    .sadd(userSessionsKey(userId), token)
    // The set can outlive its members; expiring it stops abandoned accounts
    // accumulating token lists forever. Stale members are harmless — revoking
    // deletes keys that may already be gone.
    .expire(userSessionsKey(userId), SESSION_TTL_SECONDS)
    .exec();
  return token;
}

export async function destroySession(token: string): Promise<void> {
  const userId = await redis.get(sessionKey(token));
  const pipeline = redis.multi().del(sessionKey(token));
  if (userId) pipeline.srem(userSessionsKey(userId), token);
  await pipeline.exec();
}

/**
 * Revoke every session a user has. Called after a password reset: the whole
 * point of resetting is that someone else may hold a live session, so leaving
 * those valid would defeat it.
 */
export async function destroyAllSessions(userId: string): Promise<number> {
  const key = userSessionsKey(userId);
  const tokens = await redis.smembers(key);
  const pipeline = redis.multi();
  for (const token of tokens) pipeline.del(sessionKey(token));
  pipeline.del(key);
  await pipeline.exec();
  return tokens.length;
}

export interface AuthedRequest extends Request {
  user: User;
}

/** The authenticated user, set by requireAuth. */
export function currentUser(req: Request): User {
  const user = (req as AuthedRequest).user;
  if (!user) throw new Error("requireAuth middleware missing on this route");
  return user;
}

function extractToken(req: Request): string | null {
  const header = req.headers.authorization;
  if (header?.startsWith("Bearer ")) return header.slice("Bearer ".length).trim();
  // <img>/<Document> media loads can't set headers; those GET endpoints accept
  // the session token as a query parameter instead (still redirects to a
  // short-lived presigned URL — the token never reaches object storage).
  const query = req.query.token;
  if (typeof query === "string" && query.length > 0 && req.method === "GET") return query;
  return null;
}

export async function requireAuth(req: Request, res: Response, next: NextFunction) {
  try {
    const token = extractToken(req);
    if (!token) return void res.status(401).json({ error: "authentication required" });
    const userId = await redis.get(sessionKey(token));
    if (!userId) return void res.status(401).json({ error: "invalid or expired session" });
    const user = await prisma.user.findUnique({ where: { id: userId } });
    if (!user) return void res.status(401).json({ error: "invalid or expired session" });
    await redis.expire(sessionKey(token), SESSION_TTL_SECONDS); // sliding TTL
    (req as AuthedRequest).user = user;
    next();
  } catch (err) {
    next(err);
  }
}

export async function projectAccessFor(projectId: string, userId: string): Promise<ProjectAccess | "missing"> {
  const project = await prisma.project.findUnique({
    where: { id: projectId },
    select: { ownerId: true },
  });
  if (!project) return "missing";
  const membership = await prisma.projectMember.findUnique({
    where: { projectId_userId: { projectId, userId } },
    select: { role: true },
  });
  return resolveProjectAccess(project, membership, userId);
}

/** Mounted on /projects/:projectId — every project-scoped route needs at least membership. */
export async function requireProjectMember(req: Request, res: Response, next: NextFunction) {
  try {
    const raw = req.params.projectId;
    const projectId = Array.isArray(raw) ? raw[0] : raw;
    if (!projectId) return void res.status(400).json({ error: "projectId missing" });
    const access = await projectAccessFor(projectId, currentUser(req).id);
    if (access === "missing") return void res.status(404).json({ error: "not found" });
    if (access === "none") return void res.status(403).json({ error: "not a member of this project" });
    next();
  } catch (err) {
    next(err);
  }
}

/** For owner-only operations inside handlers (project update/delete, member management). */
export async function assertProjectOwner(req: Request, projectId: string): Promise<boolean> {
  return (await projectAccessFor(projectId, currentUser(req).id)) === "owner";
}
