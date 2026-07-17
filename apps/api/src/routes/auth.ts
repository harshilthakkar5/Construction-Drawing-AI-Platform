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

export const authRouter = Router();

const credentialsSchema = z.object({
  email: z.string().email().max(320),
  password: z.string().min(8).max(200),
});
const registerSchema = credentialsSchema.extend({ name: z.string().min(1).max(200) });

const publicUser = ({ id, email, name }: { id: string; email: string; name: string }) => ({
  id,
  email,
  name,
});

authRouter.post("/register", async (req, res) => {
  const { email, name, password } = registerSchema.parse(req.body);
  try {
    const user = await prisma.user.create({
      data: { email: email.toLowerCase(), name, passwordHash: await hashPassword(password) },
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

authRouter.get("/me", requireAuth, (req, res) => {
  res.json(publicUser(currentUser(req)));
});
