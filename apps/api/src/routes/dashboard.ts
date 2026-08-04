import { Router } from "express";
import { z } from "zod";
import { currentUser } from "../auth.js";
import { prisma } from "../db.js";
import { estimateCostUsd } from "../usage.js";

/**
 * Account-level dashboard: one call returns everything the landing page shows
 * — totals, token spend (overall, per stage, per project), the distributions
 * the charts render, and recent activity.
 *
 * Scope: the projects this user can see (owned, member of, or legacy
 * ownerless), matching GET /projects. Usage rows whose project was deleted
 * keep counting toward the account total but not toward any project row.
 */
export const dashboardRouter = Router();

/** Window for the activity series, chosen by the range control on the page. */
const rangeSchema = z.object({
  days: z.coerce.number().int().refine((n) => [7, 14, 30, 90].includes(n)).default(14),
});

const emptyTokens = () => ({
  inputTokens: 0,
  outputTokens: 0,
  cacheReadTokens: 0,
  cacheWriteTokens: 0,
  totalTokens: 0,
  costUsd: 0,
});

type Tokens = ReturnType<typeof emptyTokens>;

interface UsageRow {
  projectId: string | null;
  kind: string;
  model: string;
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  cacheWriteTokens: number;
  createdAt: Date;
}

function addUsage(target: Tokens, row: UsageRow) {
  target.inputTokens += row.inputTokens;
  target.outputTokens += row.outputTokens;
  target.cacheReadTokens += row.cacheReadTokens;
  target.cacheWriteTokens += row.cacheWriteTokens;
  target.totalTokens +=
    row.inputTokens + row.outputTokens + row.cacheReadTokens + row.cacheWriteTokens;
  target.costUsd += estimateCostUsd(row);
}

const dayKey = (d: Date) => d.toISOString().slice(0, 10);

dashboardRouter.get("/", async (req, res) => {
  const user = currentUser(req);
  const { days } = rangeSchema.parse(req.query);
  const visibleProjects = {
    OR: [
      { ownerId: null },
      { ownerId: user.id },
      { members: { some: { userId: user.id } } },
    ],
  };

  const projects = await prisma.project.findMany({
    where: visibleProjects,
    orderBy: { createdAt: "desc" },
    select: { id: true, name: true, description: true, createdAt: true, ownerId: true },
  });
  const projectIds = projects.map((p) => p.id);
  const scope = { projectId: { in: projectIds } };

  const since = new Date(Date.now() - days * 24 * 60 * 60 * 1000);

  const [documents, pageCount, chunkCount, portionRows, messageCount, usageRows, summaryCount] =
    await Promise.all([
      prisma.document.findMany({
        where: { ...scope, supersededAt: null },
        select: { id: true, projectId: true, status: true, pages: true, createdAt: true },
      }),
      prisma.page.count({ where: { document: { ...scope, supersededAt: null } } }),
      prisma.chunk.count({ where: { page: { document: { ...scope, supersededAt: null } } } }),
      prisma.page.groupBy({
        by: ["discipline"],
        where: { document: { ...scope, supersededAt: null } },
        _count: true,
      }),
      prisma.message.count({ where: { session: scope } }),
      prisma.usageEvent.findMany({
        where: { OR: [{ projectId: { in: projectIds } }, { projectId: null }] },
        select: {
          projectId: true,
          kind: true,
          model: true,
          inputTokens: true,
          outputTokens: true,
          cacheReadTokens: true,
          cacheWriteTokens: true,
          createdAt: true,
        },
      }),
      prisma.summary.count({ where: scope }),
    ]);

  // --- token aggregation: overall, per stage, per model, per project, per day
  const totals = emptyTokens();
  const byKind: Record<string, Tokens> = {};
  const byModel: Record<string, Tokens> = {};
  const byProject = new Map<string, Tokens>();
  const byDay = new Map<string, Tokens>();

  for (const row of usageRows) {
    addUsage(totals, row);
    addUsage((byKind[row.kind] ??= emptyTokens()), row);
    addUsage((byModel[row.model] ??= emptyTokens()), row);
    if (row.projectId) {
      if (!byProject.has(row.projectId)) byProject.set(row.projectId, emptyTokens());
      addUsage(byProject.get(row.projectId)!, row);
    }
    if (row.createdAt >= since) {
      const key = dayKey(row.createdAt);
      if (!byDay.has(key)) byDay.set(key, emptyTokens());
      addUsage(byDay.get(key)!, row);
    }
  }

  // --- per-project rollup for the table
  const docsByProject = new Map<string, { count: number; pages: number; statuses: Set<string> }>();
  for (const doc of documents) {
    const entry = docsByProject.get(doc.projectId) ?? {
      count: 0,
      pages: 0,
      statuses: new Set<string>(),
    };
    entry.count += 1;
    entry.pages += doc.pages;
    entry.statuses.add(doc.status);
    docsByProject.set(doc.projectId, entry);
  }

  const documentStatus: Record<string, number> = {};
  for (const doc of documents) documentStatus[doc.status] = (documentStatus[doc.status] ?? 0) + 1;

  const disciplines = portionRows
    .map((row) => ({ discipline: row.discipline ?? "unclassified", pages: row._count }))
    .sort((a, b) => b.pages - a.pages);

  const projectRows = projects.map((p) => {
    const docs = docsByProject.get(p.id);
    return {
      id: p.id,
      name: p.name,
      description: p.description,
      createdAt: p.createdAt,
      documents: docs?.count ?? 0,
      pages: docs?.pages ?? 0,
      /** Worst-case status across the project's documents, for the badge. */
      status: docs?.statuses.has("failed")
        ? "failed"
        : docs?.statuses.has("processing") || docs?.statuses.has("uploaded")
          ? "processing"
          : docs
            ? "completed"
            : "empty",
      tokens: byProject.get(p.id) ?? emptyTokens(),
    };
  });

  // Dense day series so the chart has no gaps.
  const activity: { date: string; totalTokens: number; costUsd: number }[] = [];
  for (let i = days - 1; i >= 0; i--) {
    const key = dayKey(new Date(Date.now() - i * 24 * 60 * 60 * 1000));
    const day = byDay.get(key);
    activity.push({ date: key, totalTokens: day?.totalTokens ?? 0, costUsd: day?.costUsd ?? 0 });
  }

  res.json({
    activityDays: days,
    totals: {
      projects: projects.length,
      documents: documents.length,
      pages: pageCount,
      chunks: chunkCount,
      summaries: summaryCount,
      chatMessages: messageCount,
    },
    tokens: totals,
    tokensByKind: byKind,
    tokensByModel: byModel,
    documentStatus,
    disciplines,
    projects: projectRows,
    activity,
    recentProjects: projectRows.slice(0, 5),
  });
});
