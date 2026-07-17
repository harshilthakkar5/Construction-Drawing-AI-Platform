import { Router } from "express";
import { z } from "zod";
import { prisma } from "../db.js";

/**
 * FR-19 support: resolve a chunk ID to its viewer location — combined page,
 * bounding box, and PDF page size. Summary items only carry chunk IDs; the UI
 * calls this to jump-and-highlight (chat sources already embed the same data).
 * This is the tail of the source-verification chain:
 * answer → chunk_id → page → bbox → original PDF.
 */
export const chunksRouter = Router({ mergeParams: true });

const params = z.object({ projectId: z.string().uuid(), chunkId: z.string().uuid() });

chunksRouter.get("/:chunkId/location", async (req, res) => {
  const { projectId, chunkId } = params.parse(req.params);
  const chunk = await prisma.chunk.findFirst({
    where: { id: chunkId, page: { document: { projectId } } },
    include: { page: { include: { document: true } } },
  });
  if (!chunk) return void res.status(404).json({ error: "chunk not found in this project" });
  res.json({
    chunkId: chunk.id,
    documentId: chunk.page.documentId,
    filename: chunk.page.document.filename,
    pageNumber: chunk.page.pageNumber,
    combinedPageNumber: chunk.page.combinedPageNumber,
    bbox: chunk.bbox,
    pageWidth: chunk.page.pdfWidth,
    pageHeight: chunk.page.pdfHeight,
  });
});
