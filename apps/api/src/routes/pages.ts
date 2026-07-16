import { Router } from "express";
import { objectKeys } from "@cdip/shared";
import { z } from "zod";
import { prisma } from "../db.js";
import { buildPageManifest, findManifestEntry, type ManifestDocument } from "../manifest.js";
import { presignGetObject } from "../s3.js";

/**
 * Combined-view endpoints. buildPageManifest/findManifestEntry are the
 * canonical numbering; stored pages.combinedPageNumber (worker-written with
 * the same ordering rule) is the denormalized copy used for citations later.
 */
export const pagesRouter = Router({ mergeParams: true });

const projectParam = z.object({ projectId: z.string().uuid() });
const pageParams = projectParam.extend({ combined: z.coerce.number().int().min(1) });

async function manifestDocs(projectId: string): Promise<ManifestDocument[]> {
  return prisma.document.findMany({
    where: { projectId },
    select: { id: true, filename: true, pages: true, createdAt: true },
  });
}

pagesRouter.get("/manifest", async (req, res) => {
  const { projectId } = projectParam.parse(req.params);
  await prisma.project.findUniqueOrThrow({ where: { id: projectId } });

  const docs = await manifestDocs(projectId);
  const entries = buildPageManifest(docs);

  const processed = await prisma.page.findMany({
    where: { document: { projectId }, imageUrl: { not: null } },
    select: { documentId: true, pageNumber: true },
  });
  const processedSet = new Set(processed.map((p) => `${p.documentId}:${p.pageNumber}`));

  res.json(
    entries.map((e) => ({
      ...e,
      hasImage: processedSet.has(`${e.documentId}:${e.pageNumber}`),
    })),
  );
});

async function resolvePage(projectId: string, combined: number) {
  const entry = findManifestEntry(await manifestDocs(projectId), combined);
  if (!entry) return undefined;
  return { projectId, ...entry };
}

pagesRouter.get("/pages/:combined/image", async (req, res) => {
  const { projectId, combined } = pageParams.parse(req.params);
  const entry = await resolvePage(projectId, combined);
  if (!entry) return void res.status(404).json({ error: "page not found" });
  res.redirect(
    302,
    await presignGetObject(objectKeys.pageImage(projectId, entry.documentId, entry.pageNumber)),
  );
});

pagesRouter.get("/pages/:combined/thumb", async (req, res) => {
  const { projectId, combined } = pageParams.parse(req.params);
  const entry = await resolvePage(projectId, combined);
  if (!entry) return void res.status(404).json({ error: "page not found" });
  res.redirect(
    302,
    await presignGetObject(objectKeys.pageThumb(projectId, entry.documentId, entry.pageNumber)),
  );
});
