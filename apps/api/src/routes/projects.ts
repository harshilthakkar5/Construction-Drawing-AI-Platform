import { Router } from "express";
import { z } from "zod";
import { prisma } from "../db.js";

export const projectsRouter = Router();

const createProjectSchema = z.object({
  name: z.string().min(1).max(200),
  description: z.string().max(2000).optional(),
});

const updateProjectSchema = z
  .object({
    name: z.string().min(1).max(200).optional(),
    description: z.string().max(2000).nullable().optional(),
  })
  .refine((b) => b.name !== undefined || b.description !== undefined, {
    message: "nothing to update",
  });

const idParam = z.object({ projectId: z.string().uuid() });

projectsRouter.get("/", async (_req, res) => {
  const projects = await prisma.project.findMany({ orderBy: { createdAt: "desc" } });
  res.json(projects);
});

projectsRouter.post("/", async (req, res) => {
  const body = createProjectSchema.parse(req.body);
  const project = await prisma.project.create({ data: body });
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
  const project = await prisma.project.update({ where: { id: projectId }, data: body });
  res.json(project);
});

projectsRouter.delete("/:projectId", async (req, res) => {
  const { projectId } = idParam.parse(req.params);
  // Cascades to documents/pages rows. Object-storage cleanup is a later phase.
  await prisma.project.delete({ where: { id: projectId } });
  res.status(204).end();
});
