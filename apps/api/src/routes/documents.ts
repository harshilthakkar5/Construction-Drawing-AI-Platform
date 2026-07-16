import { Router } from "express";
import { objectKeys } from "@cdip/shared";
import { z } from "zod";
import { prisma } from "../db.js";
import { processDocumentQueue } from "../queues.js";
import {
  PART_SIZE,
  abortMultipartUpload,
  completeMultipartUpload,
  createMultipartUpload,
  listUploadedParts,
  presignGetObject,
  presignUploadPart,
} from "../s3.js";

/**
 * Direct-to-storage upload (large-file hard rules): the API only issues
 * presigned URLs and records rows — file bytes never pass through it.
 * Multipart is resumable: the client can list already-uploaded parts and
 * continue. Completion is server-side (ListParts → Complete), so the browser
 * never needs to read ETag headers cross-origin.
 */
export const documentsRouter = Router({ mergeParams: true });

const projectParam = z.object({ projectId: z.string().uuid() });
const docParams = projectParam.extend({ documentId: z.string().uuid() });
const uploadParams = docParams.extend({ uploadId: z.string().min(1) });

const initiateSchema = z.object({
  filename: z.string().min(1).max(500),
  size: z.number().int().positive(),
});

documentsRouter.get("/", async (req, res) => {
  const { projectId } = projectParam.parse(req.params);
  const documents = await prisma.document.findMany({
    where: { projectId },
    orderBy: [{ createdAt: "asc" }, { id: "asc" }],
  });
  res.json(documents);
});

documentsRouter.post("/", async (req, res) => {
  const { projectId } = projectParam.parse(req.params);
  const { filename, size } = initiateSchema.parse(req.body);
  await prisma.project.findUniqueOrThrow({ where: { id: projectId } });

  const document = await prisma.document.create({
    data: { projectId, filename, spacesKey: "", pages: 0 },
  });
  const key = objectKeys.originalPdf(projectId, document.id);
  await prisma.document.update({ where: { id: document.id }, data: { spacesKey: key } });

  const uploadId = await createMultipartUpload(key, "application/pdf");
  res.status(201).json({
    documentId: document.id,
    uploadId,
    key,
    partSize: PART_SIZE,
    partCount: Math.max(1, Math.ceil(size / PART_SIZE)),
  });
});

/** Presigned PUT URLs for a batch of part numbers. */
documentsRouter.post("/:documentId/upload/:uploadId/part-urls", async (req, res) => {
  const { projectId, documentId, uploadId } = uploadParams.parse(req.params);
  const { partNumbers } = z
    .object({ partNumbers: z.array(z.number().int().min(1).max(10000)).min(1).max(100) })
    .parse(req.body);
  const key = objectKeys.originalPdf(projectId, documentId);
  const urls = Object.fromEntries(
    await Promise.all(
      partNumbers.map(async (n) => [n, await presignUploadPart(key, uploadId, n)] as const),
    ),
  );
  res.json({ urls });
});

/** Already-uploaded parts — lets an interrupted upload resume. */
documentsRouter.get("/:documentId/upload/:uploadId/parts", async (req, res) => {
  const { projectId, documentId, uploadId } = uploadParams.parse(req.params);
  const key = objectKeys.originalPdf(projectId, documentId);
  const parts = await listUploadedParts(key, uploadId);
  res.json({ parts: parts.map((p) => ({ partNumber: p.PartNumber, size: p.Size })) });
});

documentsRouter.post("/:documentId/upload/:uploadId/complete", async (req, res) => {
  const { projectId, documentId, uploadId } = uploadParams.parse(req.params);
  const document = await prisma.document.findUniqueOrThrow({ where: { id: documentId } });
  const key = objectKeys.originalPdf(projectId, documentId);

  await completeMultipartUpload(key, uploadId);
  await processDocumentQueue.add("process", {
    projectId,
    documentId,
    spacesKey: key,
  });
  res.json({ documentId, status: document.status });
});

documentsRouter.post("/:documentId/upload/:uploadId/abort", async (req, res) => {
  const { projectId, documentId, uploadId } = uploadParams.parse(req.params);
  const key = objectKeys.originalPdf(projectId, documentId);
  await abortMultipartUpload(key, uploadId).catch(() => {});
  await prisma.document.delete({ where: { id: documentId } }).catch(() => {});
  res.status(204).end();
});

/** Redirect to a presigned GET of the original PDF (viewer + verification). */
documentsRouter.get("/:documentId/file", async (req, res) => {
  const { documentId } = docParams.parse(req.params);
  const document = await prisma.document.findUniqueOrThrow({ where: { id: documentId } });
  res.redirect(302, await presignGetObject(document.spacesKey));
});
