import { createHash } from "node:crypto";
import { Router } from "express";
import { z } from "zod";
import { buildSources, type ChunkSourceRecord } from "../citations.js";
import { answerFromChunks, anthropicAvailable, type HistoryTurn } from "../claude.js";
import { prisma } from "../db.js";
import { searchChunks } from "../qdrant.js";
import { redis } from "../redis.js";
import { chatDuration, retrievalCacheCounter } from "../telemetry.js";
import { embedQuery, voyageAvailable } from "../voyage.js";

/**
 * RAG chat (FR-14, FR-21..23): question → Voyage embedding → Qdrant search
 * (project filter, optional portion) → Claude with inline chunk metadata →
 * chunk-ID citations mapped back to {document, page, bbox}. Claude only ever
 * sees retrieved chunks. Retrievals are cached in Redis; every exchange is
 * persisted to chat_sessions/messages.
 */
export const chatRouter = Router({ mergeParams: true });

const projectParam = z.object({ projectId: z.string().uuid() });
const askSchema = z.object({
  question: z.string().min(1).max(4000),
  sessionId: z.string().uuid().optional(),
  portionId: z.string().uuid().optional(),
});

const RETRIEVAL_LIMIT = 18;
const RETRIEVAL_CACHE_TTL = 3600;
const HISTORY_TURNS = 8;

function retrievalCacheKey(projectId: string, portionId: string | undefined, question: string) {
  const normalized = question.trim().toLowerCase().replace(/\s+/g, " ");
  const hash = createHash("sha256").update(normalized).digest("hex");
  return `retrieval:${projectId}:${portionId ?? "all"}:${hash}`;
}

async function retrieveChunkIds(projectId: string, portionId: string | undefined, question: string) {
  const key = retrievalCacheKey(projectId, portionId, question);
  const cached = await redis.get(key).catch(() => null);
  retrievalCacheCounter.add(1, { result: cached ? "hit" : "miss" });
  if (cached) return JSON.parse(cached) as string[];

  const vector = await embedQuery(question);
  const ids = await searchChunks(projectId, vector, { portionId, limit: RETRIEVAL_LIMIT });
  await redis.set(key, JSON.stringify(ids), "EX", RETRIEVAL_CACHE_TTL).catch(() => {});
  return ids;
}

chatRouter.post("/", async (req, res) => {
  const requestStart = performance.now();
  const { projectId } = projectParam.parse(req.params);
  const { question, sessionId, portionId } = askSchema.parse(req.body);

  if (!anthropicAvailable() || !voyageAvailable()) {
    return void res.status(503).json({
      error: "chat requires ANTHROPIC_API_KEY and VOYAGE_API_KEY to be configured",
    });
  }
  await prisma.project.findUniqueOrThrow({ where: { id: projectId } });

  const session = sessionId
    ? await prisma.chatSession.findUniqueOrThrow({ where: { id: sessionId, projectId } })
    : await prisma.chatSession.create({ data: { projectId } });

  const chunkIds = await retrieveChunkIds(projectId, portionId, question);
  const chunkRows = await prisma.chunk.findMany({
    where: { id: { in: chunkIds } },
    include: { page: { include: { document: true } } },
  });
  // preserve Qdrant's best-first order
  const byId = new Map(chunkRows.map((c) => [c.id, c]));
  const ordered = chunkIds.flatMap((id) => byId.get(id) ?? []);

  if (ordered.length === 0) {
    return void res.json({
      sessionId: session.id,
      answer:
        "No indexed content matched this question yet. Upload documents and wait for processing (embedding requires VOYAGE_API_KEY on the worker), or try different wording.",
      sources: [],
    });
  }

  const history: HistoryTurn[] = (
    await prisma.message.findMany({
      where: { sessionId: session.id },
      orderBy: { createdAt: "desc" },
      take: HISTORY_TURNS,
    })
  )
    .reverse()
    .map((m) => ({
      role: m.role,
      text:
        typeof m.content === "object" && m.content !== null
          ? String((m.content as Record<string, unknown>).question ?? (m.content as Record<string, unknown>).answer ?? "")
          : String(m.content),
    }));

  const rawAnswer = await answerFromChunks(
    question,
    ordered.map((c) => ({
      chunkId: c.id,
      filename: c.page.document.filename,
      combinedPageNumber: c.page.combinedPageNumber,
      text: c.text,
    })),
    history,
  );

  const records = new Map<string, ChunkSourceRecord>(
    ordered.map((c) => [
      c.id,
      {
        chunkId: c.id,
        documentId: c.page.documentId,
        filename: c.page.document.filename,
        pageNumber: c.page.pageNumber,
        combinedPageNumber: c.page.combinedPageNumber,
        bbox: c.bbox as unknown as ChunkSourceRecord["bbox"],
        // FR-19: the viewer scales the bbox with the PDF page size.
        pageWidth: c.page.pdfWidth,
        pageHeight: c.page.pdfHeight,
      },
    ]),
  );
  const { text, sources } = buildSources(rawAnswer, records);

  // FR-23: persist prompt, retrieved chunks, response, sources, timestamp.
  await prisma.$transaction([
    prisma.message.create({
      data: {
        sessionId: session.id,
        role: "user",
        content: { question, portionId: portionId ?? null, retrievedChunkIds: chunkIds },
      },
    }),
    prisma.message.create({
      data: {
        sessionId: session.id,
        role: "assistant",
        content: { answer: rawAnswer, displayedAnswer: text },
        sources: sources as unknown as object[],
      },
    }),
  ]);

  chatDuration.record(performance.now() - requestStart, { cached: false });
  res.json({ sessionId: session.id, answer: text, sources });
});

/** Replay/audit (FR-23) and session restore for the UI. */
chatRouter.get("/:sessionId/messages", async (req, res) => {
  const { projectId } = projectParam.parse(req.params);
  const { sessionId } = z.object({ sessionId: z.string().uuid() }).parse(req.params);
  const session = await prisma.chatSession.findUniqueOrThrow({
    where: { id: sessionId, projectId },
    include: { messages: { orderBy: { createdAt: "asc" } } },
  });
  res.json(session.messages);
});
