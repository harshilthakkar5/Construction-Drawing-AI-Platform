import { Prisma } from "@prisma/client";
import { Router } from "express";
import {
  PROJECT_ROLE_VALUES,
  RFI_REVIEW_DEPTH_KEYS,
  RFI_REVIEW_INPUT_LIMITS,
  RFI_REVIEW_THINKING,
  RFI_REVIEW_THINKING_LIMITS,
} from "@cdip/shared";
import { z } from "zod";
import { assertProjectOwner, currentUser } from "../auth.js";
import { purgeProjectData } from "../cleanup.js";
import { prisma } from "../db.js";

export const projectsRouter = Router();

/** Roles are a closed vocabulary — the same slugs the classifier assigns. */
const rolesSchema = z
  .array(z.enum(PROJECT_ROLE_VALUES as [string, ...string[]]))
  .max(PROJECT_ROLE_VALUES.length)
  // A role picked twice would be repeated in the summarizer's prompt.
  .transform((roles) => [...new Set(roles)]);

const createProjectSchema = z.object({
  name: z.string().min(1).max(200),
  description: z.string().max(2000).optional(),
  roles: rolesSchema.optional(),
});

const provider = z.enum(["claude", "gemini"]).nullable();
const model = z.string().trim().min(1).max(100).nullable();
/** RfiProjectSettings: the same bounds a run enforces, so a saved setting never makes Plan fail. */
const rfiSettingsSchema = z
  .object({
    review: z
      .object({
        provider,
        model,
        depth: z.enum(RFI_REVIEW_DEPTH_KEYS as [string, ...string[]]),
        thinking: z.enum(RFI_REVIEW_THINKING),
        maxInputTokens: z.number().int().min(RFI_REVIEW_INPUT_LIMITS.min).max(RFI_REVIEW_INPUT_LIMITS.max).nullable(),
        maxThinkingTokens: z.number().int().min(RFI_REVIEW_THINKING_LIMITS.min).max(RFI_REVIEW_THINKING_LIMITS.max).nullable(),
      })
      .strict(),
    fullScan: z
      .object({ provider, model, useBatch: z.boolean(), budgetUsd: z.number().positive().max(10_000).nullable() })
      .strict(),
  })
  .strict()
  .nullable();

export const updateProjectSchema = z
  .object({
    name: z.string().min(1).max(200).optional(),
    description: z.string().max(2000).nullable().optional(),
    roles: rolesSchema.optional(),
    // Spending money is the owner's call, like every other change here; null
    // turns one-click off. Capped well under the full-scan budget ceiling.
    rfiAutoScanUsd: z.number().positive().max(1000).nullable().optional(),
    rfiSettings: rfiSettingsSchema.optional(),
  })
  .refine((b) => Object.values(b).some((v) => v !== undefined), {
    message: "nothing to update",
  });

const idParam = z.object({ projectId: z.string().uuid() });

/** Projects the user owns or belongs to; legacy ownerless projects stay visible. */
projectsRouter.get("/", async (req, res) => {
  const user = currentUser(req);
  const projects = await prisma.project.findMany({
    where: {
      OR: [
        { ownerId: null },
        { ownerId: user.id },
        { members: { some: { userId: user.id } } },
      ],
    },
    orderBy: { createdAt: "desc" },
  });
  res.json(projects);
});

projectsRouter.post("/", async (req, res) => {
  const body = createProjectSchema.parse(req.body);
  const user = currentUser(req);
  // FR-1 owner: creator becomes the owner (project row + membership row).
  const project = await prisma.project.create({
    data: {
      ...body,
      ownerId: user.id,
      members: { create: { userId: user.id, role: "owner" } },
    },
  });
  res.status(201).json(project);
});

projectsRouter.get("/:projectId", async (req, res) => {
  const { projectId } = idParam.parse(req.params);
  const project = await prisma.project.findUniqueOrThrow({ where: { id: projectId } });
  res.json(project);
});

projectsRouter.patch("/:projectId", async (req, res) => {
  const { projectId } = idParam.parse(req.params);
  const body = updateProjectSchema.parse(req.body);
  if (!(await assertProjectOwner(req, projectId))) {
    return void res.status(403).json({ error: "owner role required" });
  }
  const { rfiSettings, ...rest } = body;
  const project = await prisma.project.update({
    where: { id: projectId },
    data: { ...rest, ...(rfiSettings !== undefined ? { rfiSettings: rfiSettings ?? Prisma.DbNull } : {}) },
  });
  res.json(project);
});

projectsRouter.delete("/:projectId", async (req, res) => {
  const { projectId } = idParam.parse(req.params);
  if (!(await assertProjectOwner(req, projectId))) {
    return void res.status(403).json({ error: "owner role required" });
  }
  console.log(`[cleanup ${projectId.slice(0, 8)}] project deletion requested by ${currentUser(req).email}`);
  // Postgres first (cascades to documents/pages/chunks/portions/summaries/
  // chat rows), then purge object storage, Qdrant points, and Redis caches.
  await prisma.project.delete({ where: { id: projectId } });
  await purgeProjectData(projectId);
  res.status(204).end();
});

// --- Member management (owner only, except read) ---

projectsRouter.get("/:projectId/members", async (req, res) => {
  const { projectId } = idParam.parse(req.params);
  const members = await prisma.projectMember.findMany({
    where: { projectId },
    include: { user: { select: { id: true, email: true, name: true } } },
    orderBy: { createdAt: "asc" },
  });
  res.json(members.map((m) => ({ ...m.user, role: m.role, addedAt: m.createdAt })));
});

const addMemberSchema = z.object({
  email: z.string().email(),
  role: z.enum(["owner", "member"]).default("member"),
});

projectsRouter.post("/:projectId/members", async (req, res) => {
  const { projectId } = idParam.parse(req.params);
  const { email, role } = addMemberSchema.parse(req.body);
  if (!(await assertProjectOwner(req, projectId))) {
    return void res.status(403).json({ error: "owner role required" });
  }
  const user = await prisma.user.findUnique({ where: { email: email.toLowerCase() } });
  if (!user) return void res.status(404).json({ error: "no user with that email" });
  const member = await prisma.projectMember.upsert({
    where: { projectId_userId: { projectId, userId: user.id } },
    create: { projectId, userId: user.id, role },
    update: { role },
  });
  res.status(201).json({ id: user.id, email: user.email, name: user.name, role: member.role });
});

projectsRouter.delete("/:projectId/members/:userId", async (req, res) => {
  const { projectId } = idParam.parse(req.params);
  const { userId } = z.object({ userId: z.string().uuid() }).parse(req.params);
  if (!(await assertProjectOwner(req, projectId))) {
    return void res.status(403).json({ error: "owner role required" });
  }
  const project = await prisma.project.findUniqueOrThrow({ where: { id: projectId } });
  if (project.ownerId === userId) {
    return void res.status(400).json({ error: "cannot remove the project owner" });
  }
  await prisma.projectMember.delete({ where: { projectId_userId: { projectId, userId } } });
  res.status(204).end();
});
