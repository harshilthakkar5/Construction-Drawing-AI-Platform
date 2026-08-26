import { Router } from "express";
import { objectKeys } from "@cdip/shared";
import { z } from "zod";
import { prisma } from "../db.js";
import { processDocumentQueue } from "../queues.js";
import { isValidPdfFilename, sanitizeFilename } from "../sanitize.js";
import { objectLooksLikePdf, scanUploadedObject } from "../scan.js";
import { deleteDocumentPoints } from "../qdrant.js";
import {
  PART_SIZE,
  abortMultipartUpload,
  completeMultipartUpload,
  createMultipartUpload,
  deleteObject,
  deletePrefix,
  listUploadedParts,
  objectExists,
  presignGetObject,
  presignUploadPart,
} from "../s3.js";

/**
 * Direct-to-storage upload (large-file hard rules): the API only issues
 * presigned URLs and records rows — file bytes never pass through it.
 * Multipart is resumable: the client can list already-uploaded parts and
 * continue. Completion is server-side (ListParts → Complete), so the browser
 * never needs to read ETag headers cross-origin.
 *
 * Phase 5 adds: filename sanitization + size cap, %PDF magic validation and a
 * pluggable malware-scan hook after completion (both read at most a presigned
 * range — never the whole file), and FR-4 revisions via replacesDocumentId.
 */
export const documentsRouter = Router({ mergeParams: true });

const projectParam = z.object({ projectId: z.string().uuid() });
const docParams = projectParam.extend({ documentId: z.string().uuid() });
const uploadParams = docParams.extend({ uploadId: z.string().min(1) });

/** 2 GiB per file — CLAUDE.md sizes whole sets at 100 MB – 1 GB+. */
const MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024;

const initiateSchema = z.object({
  filename: z.string().min(1).max(500),
  size: z
    .number()
    .int()
    .positive()
    .max(MAX_UPLOAD_BYTES, { message: `file exceeds ${MAX_UPLOAD_BYTES} bytes` }),
  /** FR-4: upload a new revision replacing an existing document. */
  replacesDocumentId: z.string().uuid().optional(),
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
  const { filename, size, replacesDocumentId } = initiateSchema.parse(req.body);
  await prisma.project.findUniqueOrThrow({ where: { id: projectId } });

  const safeName = sanitizeFilename(filename);
  if (!isValidPdfFilename(safeName)) {
    return void res.status(400).json({ error: "filename must be a .pdf name" });
  }

  let revision = 1;
  if (replacesDocumentId) {
    const previous = await prisma.document.findUniqueOrThrow({
      where: { id: replacesDocumentId, projectId },
    });
    if (previous.supersededAt) {
      return void res.status(409).json({ error: "document is already superseded — replace the latest revision" });
    }
    revision = previous.revision + 1;
  }

  const document = await prisma.document.create({
    data: {
      projectId,
      filename: safeName,
      spacesKey: "",
      pages: 0,
      revision,
      previousVersionId: replacesDocumentId ?? null,
    },
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

  // Content validation: reject anything that isn't actually a PDF.
  if (!(await objectLooksLikePdf(key))) {
    await deleteObject(key).catch(() => {});
    await prisma.document.update({ where: { id: documentId }, data: { status: "failed" } });
    return void res.status(422).json({ error: "uploaded file is not a PDF" });
  }

  // Malware-scan hook (no-op unless MALWARE_SCAN_URL is configured; scanner
  // failures block the file rather than waving it through).
  let verdict: Awaited<ReturnType<typeof scanUploadedObject>>;
  try {
    verdict = await scanUploadedObject(key);
  } catch (err) {
    console.error("malware scan failed", err);
    await prisma.document.update({ where: { id: documentId }, data: { status: "failed" } });
    return void res.status(502).json({ error: "malware scan unavailable — upload rejected" });
  }
  if (verdict === "infected") {
    await deleteObject(key).catch(() => {});
    await prisma.document.update({ where: { id: documentId }, data: { status: "failed" } });
    return void res.status(422).json({ error: "upload failed malware scan" });
  }

  await processDocumentQueue.add("process", {
    projectId,
    documentId,
    spacesKey: key,
  });
  res.json({ documentId, status: document.status });
});

/** Re-enqueue processing for a stuck or failed document. Safe to repeat: the
 * pipeline resumes — pages that already finished are skipped, so a document
 * that died at page N continues from page N. Use when a job exhausted its
 * retries or the worker died mid-run (status frozen at "processing"). */
documentsRouter.post("/:documentId/reprocess", async (req, res) => {
  const { projectId, documentId } = docParams.parse(req.params);
  const document = await prisma.document.findUniqueOrThrow({
    where: { id: documentId, projectId },
  });
  if (document.supersededAt) {
    return void res.status(409).json({ error: "document is superseded — reprocess the latest revision" });
  }
  if (document.status === "completed") {
    return void res.status(409).json({ error: "document already completed" });
  }
  // A missing original means the upload never completed or validation deleted
  // it — retrying would 404 forever. The only fix is deleting + re-uploading.
  if (!(await objectExists(document.spacesKey))) {
    return void res.status(409).json({
      error:
        "original PDF is missing from storage (upload never completed or was rejected) — delete this document and upload the file again",
    });
  }
  await prisma.document.update({ where: { id: documentId }, data: { status: "uploaded" } });
  await processDocumentQueue.add("process", {
    projectId,
    documentId,
    spacesKey: document.spacesKey,
  });
  console.log(`[reprocess] document ${documentId} re-queued for project ${projectId}`);
  res.json({ documentId, status: "uploaded" });
});

/**
 * Re-queue processing for every completed document in the project. Use after
 * flipping a worker switch — e.g. documents processed with
 * EMBEDDINGS_ENABLED=false have chunks but no vectors; re-running indexes them
 * (already-processed pages are skipped, so this is cheap).
 *
 * `{"reembed": true}` additionally clears every chunk's embeddingId first.
 * That is what a change of EMBEDDING_PROVIDER, EMBEDDING_MODEL or
 * EMBEDDING_DIM needs: the worker embeds chunks whose embeddingId is NULL, so
 * without this the run queues every document and embeds nothing, leaving the
 * new collection empty and chat answering from nowhere. It re-embeds the whole
 * project from scratch — the one call in this app that knowingly spends the
 * full index cost, so it is opt-in rather than the default.
 */
const reindexSchema = z.object({ reembed: z.boolean().optional().default(false) });

documentsRouter.post("/reindex", async (req, res) => {
  const { projectId } = projectParam.parse(req.params);
  const { reembed } = reindexSchema.parse(req.body ?? {});

  let cleared = 0;
  if (reembed) {
    // Point ID == chunk ID, so the old vectors are simply overwritten when the
    // collection is unchanged, and left untouched in the previous collection
    // when QDRANT_COLLECTION moved — which is what makes a provider switch
    // reversible.
    ({ count: cleared } = await prisma.chunk.updateMany({
      where: { page: { document: { projectId } }, embeddingId: { not: null } },
      data: { embeddingId: null },
    }));
    console.log(`[reindex] cleared ${cleared} embedding ids for project ${projectId}`);
  }
  const documents = await prisma.document.findMany({
    where: { projectId, supersededAt: null, status: { in: ["completed", "failed"] } },
    select: { id: true, spacesKey: true },
  });
  let queued = 0;
  for (const document of documents) {
    if (!(await objectExists(document.spacesKey))) continue; // original is gone
    await prisma.document.update({ where: { id: document.id }, data: { status: "uploaded" } });
    await processDocumentQueue.add("process", {
      projectId,
      documentId: document.id,
      spacesKey: document.spacesKey,
    });
    queued++;
  }
  console.log(`[reindex] re-queued ${queued}/${documents.length} documents for project ${projectId}`);
  res.json({ queued, total: documents.length, cleared });
});

/** Delete one document everywhere: DB rows (cascade to pages/chunks), its
 * storage prefix (original + page images/thumbs/text), and Qdrant points.
 * The recovery path for broken uploads, and general per-document cleanup. */
documentsRouter.delete("/:documentId", async (req, res) => {
  const { projectId, documentId } = docParams.parse(req.params);
  await prisma.document.findUniqueOrThrow({ where: { id: documentId, projectId } });
  await prisma.document.delete({ where: { id: documentId } });
  const tag = `[cleanup doc ${documentId.slice(0, 8)}]`;
  // Remove pending jobs first — they would retry against the deleted row.
  try {
    const jobs = [
      ...(await processDocumentQueue.getWaiting(0, 999)),
      ...(await processDocumentQueue.getDelayed(0, 999)),
      ...(await processDocumentQueue.getFailed(0, 999)),
    ];
    let removed = 0;
    for (const job of jobs) {
      if ((job.data as { documentId?: string }).documentId !== documentId) continue;
      await job.remove().then(
        () => removed++,
        () => {},
      );
    }
    console.log(`${tag} queues: removed ${removed} pending jobs`);
  } catch (err) {
    console.error(`${tag} queue cleanup FAILED`, err);
  }
  try {
    const removed = await deletePrefix(`projects/${projectId}/pdfs/${documentId}/`);
    console.log(`${tag} storage: deleted ${removed} objects`);
  } catch (err) {
    console.error(`${tag} storage cleanup FAILED`, err);
  }
  try {
    await deleteDocumentPoints(documentId);
    console.log(`${tag} qdrant: deleted points`);
  } catch (err) {
    console.error(`${tag} qdrant cleanup FAILED`, err);
  }
  res.status(204).end();
});

documentsRouter.post("/:documentId/upload/:uploadId/abort", async (req, res) => {
  const { projectId, documentId, uploadId } = uploadParams.parse(req.params);
  const key = objectKeys.originalPdf(projectId, documentId);
  await abortMultipartUpload(key, uploadId).catch(() => {});
  await prisma.document.delete({ where: { id: documentId } }).catch(() => {});
  res.status(204).end();
});

/** Redirect to a presigned GET of the original PDF (viewer + verification).
 * This is the ONLY media path: everything is presigned-URL access. */
documentsRouter.get("/:documentId/file", async (req, res) => {
  const { documentId } = docParams.parse(req.params);
  const document = await prisma.document.findUniqueOrThrow({ where: { id: documentId } });
  res.redirect(302, await presignGetObject(document.spacesKey));
});
