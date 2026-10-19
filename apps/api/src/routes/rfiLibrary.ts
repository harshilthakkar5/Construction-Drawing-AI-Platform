import { Router } from "express";
import { z } from "zod";
import { RFI_CUSTOM_QUESTION_CHARS, RFI_LIBRARY_CUSTOM_MAX } from "@cdip/shared";
import { currentUser } from "../auth.js";
import { prisma } from "../db.js";
import { buildLibrary, library, questionIdsOf } from "../rfiLibrary.js";

/**
 * The question library a project picks from: the shipped library (the
 * client's workbook, imported once by a developer — users never upload it)
 * plus the questions this project saved, each under an element or as a
 * follow-up under any question of that element.
 */
export const rfiLibraryRouter = Router({ mergeParams: true });

const projectParam = z.object({ projectId: z.string().uuid() });
const questionParams = projectParam.extend({ questionId: z.string().uuid() });
const questionText = z.string().trim().min(1).max(RFI_CUSTOM_QUESTION_CHARS);
const category = z.string().trim().max(60).nullable().optional();

const select = { id: true, elementId: true, parentId: true, category: true, text: true } as const;
const customsOf = (projectId: string) =>
  prisma.rfiCustomQuestion.findMany({ where: { projectId }, select, orderBy: { createdAt: "asc" } });

rfiLibraryRouter.get("/", async (req, res) => {
  const { projectId } = projectParam.parse(req.params);
  const { file, version } = library();
  res.json(buildLibrary(file, version, await customsOf(projectId)));
});

rfiLibraryRouter.post("/questions", async (req, res) => {
  const { projectId } = projectParam.parse(req.params);
  const body = z
    .object({ elementId: z.string().min(1).max(40), parentId: z.string().min(1).max(60).nullable().optional(), category, text: questionText })
    .parse(req.body ?? {});
  const customs = await customsOf(projectId);
  if (customs.length >= RFI_LIBRARY_CUSTOM_MAX) {
    return void res.status(409).json({ error: `a project can save at most ${RFI_LIBRARY_CUSTOM_MAX} questions` });
  }
  const ids = questionIdsOf(library().file, body.elementId, customs);
  if (!ids) return void res.status(400).json({ error: `element ${body.elementId} is not in the library` });
  if (body.parentId && !ids.has(body.parentId)) {
    return void res.status(400).json({ error: `question ${body.parentId} is not a question of element ${body.elementId}` });
  }
  const row = await prisma.rfiCustomQuestion.create({
    data: {
      projectId,
      elementId: body.elementId,
      parentId: body.parentId ?? null,
      category: body.category || null,
      text: body.text,
      createdById: currentUser(req).id,
    },
    select,
  });
  res.status(201).json(row);
});

rfiLibraryRouter.patch("/questions/:questionId", async (req, res) => {
  const { projectId, questionId } = questionParams.parse(req.params);
  const body = z.object({ text: questionText.optional(), category }).parse(req.body ?? {});
  // The parent never moves, so a follow-up can never become its own ancestor.
  const { count } = await prisma.rfiCustomQuestion.updateMany({
    where: { id: questionId, projectId },
    data: { ...(body.text ? { text: body.text } : {}), ...(body.category !== undefined ? { category: body.category || null } : {}) },
  });
  if (!count) return void res.status(404).json({ error: "not found" });
  res.json(await prisma.rfiCustomQuestion.findUniqueOrThrow({ where: { id: questionId }, select }));
});

/** Deletes the question and every follow-up under it. */
rfiLibraryRouter.delete("/questions/:questionId", async (req, res) => {
  const { projectId, questionId } = questionParams.parse(req.params);
  const customs = await customsOf(projectId);
  if (!customs.some((c) => c.id === questionId)) return void res.status(404).json({ error: "not found" });
  const doomed = new Set([questionId]);
  for (let grew = true; grew; ) {
    grew = false;
    for (const c of customs) {
      if (c.parentId && doomed.has(c.parentId) && !doomed.has(c.id)) {
        doomed.add(c.id);
        grew = true;
      }
    }
  }
  await prisma.rfiCustomQuestion.deleteMany({ where: { projectId, id: { in: [...doomed] } } });
  res.json({ deleted: [...doomed] });
});
