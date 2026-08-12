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
 *
 * Phase 5: superseded document revisions (FR-4) are excluded — the manifest
 * always shows the latest revision of each drawing set. Manifest entries also
 * carry the PDF page size so the viewer can scale bbox highlights (FR-19).
 */
export const pagesRouter = Router({ mergeParams: true });

const projectParam = z.object({ projectId: z.string().uuid() });
const pageParams = projectParam.extend({ combined: z.coerce.number().int().min(1) });

async function manifestDocs(projectId: string): Promise<ManifestDocument[]> {
  return prisma.document.findMany({
    where: { projectId, supersededAt: null },
    select: { id: true, filename: true, pages: true, createdAt: true },
  });
}

pagesRouter.get("/manifest", async (req, res) => {
  const { projectId } = projectParam.parse(req.params);
  await prisma.project.findUniqueOrThrow({ where: { id: projectId } });

  const docs = await manifestDocs(projectId);
  const entries = buildPageManifest(docs);

  const processed = await prisma.page.findMany({
    where: { document: { projectId, supersededAt: null }, imageUrl: { not: null } },
    select: { documentId: true, pageNumber: true, pdfWidth: true, pdfHeight: true },
  });
  const processedByKey = new Map(processed.map((p) => [`${p.documentId}:${p.pageNumber}`, p]));

  res.json(
    entries.map((e) => {
      const page = processedByKey.get(`${e.documentId}:${e.pageNumber}`);
      return {
        ...e,
        hasImage: page !== undefined,
        pageWidth: page?.pdfWidth ?? null,
        pageHeight: page?.pdfHeight ?? null,
      };
    }),
  );
});

async function resolvePage(projectId: string, combined: number) {
  const entry = findManifestEntry(await manifestDocs(projectId), combined);
  if (!entry) return undefined;
  return { projectId, ...entry };
}

/**
 * How long the browser may reuse a media redirect.
 *
 * Every request re-signs the storage URL, so without this the redirect target
 * changed on each load and the browser treated an already-downloaded page image
 * as a brand-new resource — re-opening a project re-fetched every page it
 * showed. Caching the 302 means the same presigned URL comes back, so the image
 * itself is served from the browser cache.
 *
 * `private` because the URL is a capability: it must not be held by a shared
 * proxy. Well inside the 1-hour presign expiry, so a cached redirect can never
 * point at an expired signature. Rendered pages are immutable — reprocessing a
 * document writes the same key — so a stale window costs nothing.
 */
const MEDIA_REDIRECT_CACHE = "private, max-age=600";

pagesRouter.get("/pages/:combined/image", async (req, res) => {
  const { projectId, combined } = pageParams.parse(req.params);
  const entry = await resolvePage(projectId, combined);
  if (!entry) return void res.status(404).json({ error: "page not found" });
  res.set("Cache-Control", MEDIA_REDIRECT_CACHE);
  res.redirect(
    302,
    await presignGetObject(objectKeys.pageImage(projectId, entry.documentId, entry.pageNumber)),
  );
});

/**
 * "Why did this sheet get that discipline?" — the text the region scrape read
 * on this page, how it was read, and what the classifier made of it. The
 * answer to a mis-categorized sheet is almost always a box that is too tight.
 */
pagesRouter.get("/pages/:combined/region-text", async (req, res) => {
  const { projectId, combined } = pageParams.parse(req.params);
  const entry = await resolvePage(projectId, combined);
  if (!entry) return void res.status(404).json({ error: "page not found" });
  const page = await prisma.page.findUnique({
    where: {
      documentId_pageNumber: { documentId: entry.documentId, pageNumber: entry.pageNumber },
    },
    select: {
      sheetRegionText: true,
      regionMethod: true,
      regionVersion: true,
      sheetNumber: true,
      discipline: true,
      disciplineSource: true,
    },
  });
  if (!page) return void res.status(404).json({ error: "page not processed yet" });
  res.json({ combinedPageNumber: combined, ...page });
});

pagesRouter.get("/pages/:combined/thumb", async (req, res) => {
  const { projectId, combined } = pageParams.parse(req.params);
  const entry = await resolvePage(projectId, combined);
  if (!entry) return void res.status(404).json({ error: "page not found" });
  res.set("Cache-Control", MEDIA_REDIRECT_CACHE);
  res.redirect(
    302,
    await presignGetObject(objectKeys.pageThumb(projectId, entry.documentId, entry.pageNumber)),
  );
});
