import { createHash } from "node:crypto";
import { Router } from "express";
import { z } from "zod";
import { buildSources, type ChunkSourceRecord } from "../citations.js";
import { answerFromChunks, chatModelAvailable, type HistoryTurn } from "../answer.js";
import { chatProvider } from "../llm.js";
import { chatLimiter } from "../rateLimit.js";
import { prisma } from "../db.js";
import { hybridEnabled, retrieveChunkIds } from "../retrieval.js";
import { redis } from "../redis.js";
import { chatDuration, retrievalCacheCounter } from "../telemetry.js";
import { embeddingKeyEnv, embeddingsAvailable } from "../embedding.js";

/**
 * RAG chat (FR-14, FR-21..23): question → hybrid retrieval (dense embedding +
 * Postgres keyword search, fused by rank; project filter, optional portion) →
 * Claude with inline chunk metadata →
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
  // The mode is part of the key: a cached dense-only result must not be served
  // to a request that asked for hybrid, or the switch would look like it did
  // nothing until the TTL expired.
  const mode = hybridEnabled() ? "hybrid" : "dense";
  return `retrieval:${projectId}:${mode}:${portionId ?? "all"}:${hash}`;
}

async function cachedChunkIds(projectId: string, portionId: string | undefined, question: string) {
  const key = retrievalCacheKey(projectId, portionId, question);
  const cached = await redis.get(key).catch(() => null);
  retrievalCacheCounter.add(1, { result: cached ? "hit" : "miss" });
  if (cached) return JSON.parse(cached) as string[];

  const { chunkIds, dense, keyword } = await retrieveChunkIds(projectId, question, {
    portionId,
    limit: RETRIEVAL_LIMIT,
  });
  if (hybridEnabled()) {
    console.log(
      `[retrieval] ${chunkIds.length} chunks for project ${projectId} (dense ${dense}, keyword ${keyword})`,
    );
  }
  await redis.set(key, JSON.stringify(chunkIds), "EX", RETRIEVAL_CACHE_TTL).catch(() => {});
  return chunkIds;
}

chatRouter.post("/", chatLimiter, async (req, res) => {
  const requestStart = performance.now();
  const { projectId } = projectParam.parse(req.params);
  const { question, sessionId, portionId } = askSchema.parse(req.body);

  if (!chatModelAvailable() || !embeddingsAvailable()) {
    // Names the key the ACTIVE provider needs — telling someone running
    // CHAT_PROVIDER=gemini to set ANTHROPIC_API_KEY sends them the wrong way.
    const chatKey = chatProvider() === "gemini" ? "GEMINI_API_KEY" : "ANTHROPIC_API_KEY";
    return void res.status(503).json({
      error: `chat requires ${chatKey} and ${embeddingKeyEnv()} to be configured`,
    });
  }
  await prisma.project.findUniqueOrThrow({ where: { id: projectId } });

  const session = sessionId
    ? await prisma.chatSession.findUniqueOrThrow({ where: { id: sessionId, projectId } })
    : await prisma.chatSession.create({ data: { projectId } });

  const chunkIds = await cachedChunkIds(projectId, portionId, question);
  const chunkRows = await prisma.chunk.findMany({
    where: { id: { in: chunkIds } },
    include: { page: { include: { document: true } } },
  });
  // preserve Qdrant's best-first order
  const byId = new Map(chunkRows.map((c) => [c.id, c]));
  const ordered = chunkIds.flatMap((id) => byId.get(id) ?? []);

  // Zero retrieved chunks is NOT a dead end. A construction-discipline
  // question ("what is a column?") matches nothing in the drawings by
  // definition, and answer.ts answers it under an explicit "not from this
  // project's drawings" line — while anything outside construction gets one
  // refusal sentence. A question about THIS project still gets "the drawings
  // do not cover it", now from the model, which can say what it could not find.

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
    projectId,
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

/**
 * Chat history for a project (the "old chats" picker).
 *
 * A session is only interesting to a reader as its questions, so each row
 * carries the first question as a preview, the last activity time and the turn
 * count — enough to pick one without loading it. `window` filters by last
 * activity: 1h | 24h | 7d | 30d | 3m | all, or `from`/`to` for a custom range.
 */
const HISTORY_WINDOWS: Record<string, number> = {
  "1h": 60 * 60 * 1000,
  "24h": 24 * 60 * 60 * 1000,
  "7d": 7 * 24 * 60 * 60 * 1000,
  "30d": 30 * 24 * 60 * 60 * 1000,
  "3m": 90 * 24 * 60 * 60 * 1000,
};

const historyQuery = z.object({
  window: z.enum(["1h", "24h", "7d", "30d", "3m", "all", "custom"]).default("30d"),
  from: z.coerce.date().optional(),
  to: z.coerce.date().optional(),
  limit: z.coerce.number().int().min(1).max(100).default(50),
});

/** First question in a session — what the picker shows as its title. */
function sessionPreview(messages: { role: string; content: unknown }[]): string {
  const first = messages.find((m) => m.role === "user");
  const content = first?.content;
  const question =
    typeof content === "object" && content !== null
      ? String((content as Record<string, unknown>).question ?? "")
      : String(content ?? "");
  return question.trim().slice(0, 140);
}

chatRouter.get("/sessions", async (req, res) => {
  const { projectId } = projectParam.parse(req.params);
  const { window, from, to, limit } = historyQuery.parse(req.query);

  // A custom range with no bounds is "all" rather than an error — the UI can
  // open the custom picker before either date is chosen.
  let since: Date | undefined;
  let until: Date | undefined;
  if (window === "custom") {
    since = from;
    until = to;
  } else if (window !== "all") {
    since = new Date(Date.now() - HISTORY_WINDOWS[window]!);
  }

  const sessions = await prisma.chatSession.findMany({
    where: { projectId },
    include: {
      messages: { orderBy: { createdAt: "asc" }, select: { role: true, content: true, createdAt: true } },
    },
    orderBy: { createdAt: "desc" },
    // Filtering on the LAST message means an old session used again today
    // still shows under "last hour", so the window has to be applied after the
    // messages are known rather than in the query.
    take: 300,
  });

  const rows = sessions
    .filter((s) => s.messages.length > 0)
    .map((s) => ({
      id: s.id,
      createdAt: s.createdAt,
      lastMessageAt: s.messages[s.messages.length - 1]!.createdAt,
      messageCount: s.messages.length,
      preview: sessionPreview(s.messages),
    }))
    .filter((s) => (!since || s.lastMessageAt >= since) && (!until || s.lastMessageAt <= until))
    .sort((a, b) => b.lastMessageAt.getTime() - a.lastMessageAt.getTime())
    .slice(0, limit);

  res.json(rows);
});
