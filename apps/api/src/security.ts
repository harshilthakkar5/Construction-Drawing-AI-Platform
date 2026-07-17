import { randomBytes, scrypt as scryptCb, timingSafeEqual } from "node:crypto";
import { promisify } from "node:util";

/**
 * Pure security primitives (unit-tested; no DB/Redis imports):
 * - scrypt password hashing (node:crypto — no extra deps), "salt:hash" hex.
 * - project RBAC role resolution (owner/member).
 */

const scrypt = promisify(scryptCb);

export async function hashPassword(password: string): Promise<string> {
  const salt = randomBytes(16).toString("hex");
  const hash = (await scrypt(password, salt, 64)) as Buffer;
  return `${salt}:${hash.toString("hex")}`;
}

export async function verifyPassword(password: string, stored: string): Promise<boolean> {
  const [salt, expectedHex] = stored.split(":");
  if (!salt || !expectedHex) return false;
  const hash = (await scrypt(password, salt, 64)) as Buffer;
  const expected = Buffer.from(expectedHex, "hex");
  return hash.length === expected.length && timingSafeEqual(hash, expected);
}

export type ProjectAccess = "owner" | "member" | "none";

/** Role resolution. Projects created before auth existed (ownerId NULL) grant
 * owner access to any authenticated user — documented in README. */
export function resolveProjectAccess(
  project: { ownerId: string | null },
  membership: { role: "owner" | "member" } | null,
  userId: string,
): ProjectAccess {
  if (project.ownerId === null) return "owner";
  if (project.ownerId === userId) return "owner";
  if (membership) return membership.role;
  return "none";
}
