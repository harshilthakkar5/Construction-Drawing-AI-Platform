import { Router } from "express";
import { Prisma } from "@prisma/client";
import { z } from "zod";
import {
  createSession,
  currentUser,
  destroySession,
  hashPassword,
  requireAuth,
  verifyPassword,
} from "../auth.js";
import { prisma } from "../db.js";
import { sanitizeText } from "../sanitize.js";

export const authRouter = Router();

const credentialsSchema = z.object({
  email: z.string().email().max(320),
  password: z.string().min(8).max(200),
});

/**
 * Registration collects first/last/company (the sign-up form); `name` stays
 * the single display name and is derived from them. `name` may still be sent
 * directly — older clients and the API tests do.
 */
const registerSchema = credentialsSchema
  .extend({
    name: z.string().min(1).max(200).optional(),
    firstName: z.string().min(1).max(100).optional(),
    lastName: z.string().min(1).max(100).optional(),
    company: z.string().max(200).optional(),
  })
  .refine((b) => b.name || b.firstName, { message: "name or firstName is required" });

const profileSchema = z
  .object({
    firstName: z.string().min(1).max(100).optional(),
    lastName: z.string().max(100).optional(),
    company: z.string().max(200).nullable().optional(),
    email: z.string().email().max(320).optional(),
  })
  .refine((b) => Object.keys(b).length > 0, { message: "nothing to update" });

const passwordSchema = z.object({
  currentPassword: z.string().min(1).max(200),
  newPassword: z.string().min(8).max(200),
});

interface UserRow {
  id: string;
  email: string;
  name: string;
  firstName?: string | null;
  lastName?: string | null;
  company?: string | null;
  createdAt?: Date;
}

const publicUser = (u: UserRow) => ({
  id: u.id,
  email: u.email,
  name: u.name,
  firstName: u.firstName ?? null,
  lastName: u.lastName ?? null,
  company: u.company ?? null,
  createdAt: u.createdAt ?? null,
});

const displayName = (first?: string | null, last?: string | null, fallback?: string) =>
  [first, last].filter(Boolean).join(" ").trim() || (fallback ?? "");

authRouter.post("/register", async (req, res) => {
  const body = registerSchema.parse(req.body);
  const { email, password } = body;
  const firstName = body.firstName ? sanitizeText(body.firstName) : null;
  const lastName = body.lastName ? sanitizeText(body.lastName) : null;
  const company = body.company ? sanitizeText(body.company) : null;
  const name = sanitizeText(displayName(firstName, lastName, body.name)) || email;
  try {
    const user = await prisma.user.create({
      data: {
        email: email.toLowerCase(),
        name,
        firstName,
        lastName,
        company,
        passwordHash: await hashPassword(password),
      },
    });
    const token = await createSession(user.id);
    res.status(201).json({ token, user: publicUser(user) });
  } catch (err) {
    if (err instanceof Prisma.PrismaClientKnownRequestError && err.code === "P2002") {
      return void res.status(409).json({ error: "email already registered" });
    }
    throw err;
  }
});

authRouter.post("/login", async (req, res) => {
  const { email, password } = credentialsSchema.parse(req.body);
  const user = await prisma.user.findUnique({ where: { email: email.toLowerCase() } });
  // Same response for unknown email and wrong password.
  if (!user || !(await verifyPassword(password, user.passwordHash))) {
    return void res.status(401).json({ error: "invalid credentials" });
  }
  const token = await createSession(user.id);
  res.json({ token, user: publicUser(user) });
});

authRouter.post("/logout", requireAuth, async (req, res) => {
  const header = req.headers.authorization;
  if (header?.startsWith("Bearer ")) await destroySession(header.slice("Bearer ".length).trim());
  res.status(204).end();
});

authRouter.get("/me", requireAuth, async (req, res) => {
  // currentUser is the session-cached row; re-read so a profile update made in
  // another tab is reflected immediately.
  const user = await prisma.user.findUniqueOrThrow({ where: { id: currentUser(req).id } });
  res.json(publicUser(user));
});

/** Update the signed-in user's own profile (account page). */
authRouter.patch("/me", requireAuth, async (req, res) => {
  const body = profileSchema.parse(req.body);
  const existing = await prisma.user.findUniqueOrThrow({ where: { id: currentUser(req).id } });

  const firstName = body.firstName ? sanitizeText(body.firstName) : existing.firstName;
  const lastName =
    body.lastName === undefined ? existing.lastName : sanitizeText(body.lastName) || null;
  const company =
    body.company === undefined || body.company === null
      ? body.company === null
        ? null
        : existing.company
      : sanitizeText(body.company) || null;

  try {
    const user = await prisma.user.update({
      where: { id: existing.id },
      data: {
        firstName,
        lastName,
        company,
        name: displayName(firstName, lastName, existing.name),
        ...(body.email ? { email: body.email.toLowerCase() } : {}),
      },
    });
    res.json(publicUser(user));
  } catch (err) {
    if (err instanceof Prisma.PrismaClientKnownRequestError && err.code === "P2002") {
      return void res.status(409).json({ error: "email already registered" });
    }
    throw err;
  }
});

/** Change password. Sessions stay valid — the caller keeps their token. */
authRouter.post("/password", requireAuth, async (req, res) => {
  const { currentPassword, newPassword } = passwordSchema.parse(req.body);
  const user = await prisma.user.findUniqueOrThrow({ where: { id: currentUser(req).id } });
  if (!(await verifyPassword(currentPassword, user.passwordHash))) {
    return void res.status(403).json({ error: "current password is incorrect" });
  }
  await prisma.user.update({
    where: { id: user.id },
    data: { passwordHash: await hashPassword(newPassword) },
  });
  res.status(204).end();
});
