import type { Prisma } from "@prisma/client";
import {
  type RfiReviewCheckId,
  type RfiReviewDepth,
  type RfiReviewPageDto,
  type RfiReviewRunDto,
  type RfiReviewStage,
  type RfiReviewStatus,
  type RfiReviewTarget,
  type RfiReviewThinking,
  type RfiScanUsageDto,
} from "@cdip/shared";
import { prisma } from "./db.js";
import { rfiReviewModel, rfiReviewProvider } from "./llm.js";
import { retrieveChunkIds, type RetrievalResult } from "./retrieval.js";
import { scanUsage, type CostOf } from "./rfiScanRules.js";
import {
  SCOPE_KINDS,
  checkQuery,
  estimateReview,
  normalizeSheet,
  rankScope,
  reviewIsActive,
  scopeHash,
  selectRfiChecks,
  type Box,
  type CandidateChunk,
  type CandidatePage,
  type ReviewScope,
} from "./rfiReviewRules.js";
import { estimateCostUsd } from "./usage.js";

/**
 * Plans a targeted RFI review BEFORE any review model call is spent, and
 * persists the plan so the scope a person approves is exactly the scope the
 * worker later reads (docs/rfi-targeted-review.md).
 *
 * Retrieval is the chat's own `retrieveChunkIds` — dense + full-text + exact
 * identifiers, fused — called once per selected check with that check's
 * query. It is not re-implemented here and never in Python: the worker only
 * ever receives a run id and reads the stored scope.
 */

/** A plan that cannot be made, with the status and detail the route returns. */
export class PlanError extends Error {
  constructor(
    message: string,
    readonly status = 400,
    readonly details: Record<string, unknown> = {},
  ) {
    super(message);
  }
}

export interface PlanRequest {
  target: RfiReviewTarget;
  checkMode: "auto" | "custom";
  checkIds: string[];
  depth: RfiReviewDepth;
  thinking: RfiReviewThinking;
  /** Pages the person removed from an earlier plan — related sheets that are
   * obviously unrelated, or the wrong one of two pages claiming a sheet. */
  excludePageIds: string[];
}

export interface PlanDeps {
  retrieve: (projectId: string, question: string, options: { limit: number }) => Promise<RetrievalResult>;
}

/** Per check query; the ranking and the depth cap decide what survives. */
const HITS_PER_QUERY = 24;

interface PageRow {
  id: string;
  documentId: string;
  pageNumber: number;
  combinedPageNumber: number | null;
  sheetNumber: string | null;
  discipline: string | null;
  pdfWidth: number | null;
  pdfHeight: number | null;
  sheetRegionText: string | null;
}

/** Pages a review may use: live (not superseded), processed, in this
 * project, and not excluded from RFI analysis. The ONE filter every scope
 * query goes through. */
async function analysablePages(projectId: string): Promise<PageRow[]> {
  return prisma.$queryRaw<PageRow[]>`
    SELECT p.id, p."documentId", p."pageNumber", p."combinedPageNumber", p."sheetNumber",
           p.discipline, p."pdfWidth", p."pdfHeight", p."sheetRegionText"
      FROM pages p JOIN documents d ON d.id = p."documentId"
     WHERE d."projectId" = ${projectId}
       AND d."supersededAt" IS NULL
       AND d."includeInRfiAnalysis"
       AND d.status = 'completed'
     ORDER BY p."combinedPageNumber" NULLS LAST, p."pageNumber"`;
}

const asPage = (row: PageRow): CandidatePage => ({
  pageId: row.id,
  documentId: row.documentId,
  pageNumber: row.pageNumber,
  combinedPageNumber: row.combinedPageNumber,
  sheetNumber: row.sheetNumber,
  discipline: row.discipline,
  pdfWidth: row.pdfWidth,
  pdfHeight: row.pdfHeight,
});

/**
 * The pages one named sheet resolves to. The sheet number read off the title
 * block first; then a whole token of the title-block text, for a page whose
 * number the classifier did not settle. Two pages claiming one sheet are BOTH
 * returned and reported: silently picking one would review a drawing the
 * person may not have meant.
 */
export function resolveSheet(value: string, pages: PageRow[]): PageRow[] {
  const wanted = normalizeSheet(value);
  if (!wanted) return [];
  const exact = pages.filter((p) => p.sheetNumber && normalizeSheet(p.sheetNumber) === wanted);
  if (exact.length) return exact;
  return pages.filter((p) =>
    (p.sheetRegionText ?? "").split(/\s+/).some((token) => token && normalizeSheet(token) === wanted),
  );
}

async function chunksOf(pageIds: string[]): Promise<CandidateChunk[]> {
  if (!pageIds.length) return [];
  const rows = await prisma.chunk.findMany({
    where: { pageId: { in: pageIds }, kind: { in: [...SCOPE_KINDS] } },
    select: { id: true, pageId: true, kind: true, text: true, bbox: true, tokenCount: true },
  });
  return rows.map((r) => ({
    chunkId: r.id,
    pageId: r.pageId,
    kind: r.kind,
    text: r.text,
    bbox: (r.bbox && typeof r.bbox === "object" ? r.bbox : null) as Box | null,
    tokenCount: r.tokenCount,
  }));
}

export async function planReview(
  projectId: string,
  userId: string,
  request: PlanRequest,
  deps: PlanDeps = { retrieve: retrieveChunkIds },
) {
  const excluded = new Set(request.excludePageIds);
  const all = await analysablePages(projectId);
  if (!all.length) {
    throw new PlanError("no processed drawings are available for RFI review in this project yet", 409);
  }
  const values = request.target.type === "sheet" ? [request.target.value] : request.target.values;

  const notes: string[] = [];
  const ambiguous: string[] = [];
  const sides: { label: string; pages: CandidatePage[] }[] = [];
  for (const value of values) {
    const found = resolveSheet(value, all);
    if (!found.length) {
      const known = [...new Set(all.map((p) => p.sheetNumber).filter((s): s is string => !!s))];
      throw new PlanError(`no sheet "${value}" was found in this project's drawings`, 404, {
        value,
        knownSheets: known.slice(0, 40),
        hint: known.length
          ? "Use a sheet number exactly as it is printed in the title block."
          : "No sheet numbers have been read yet — mark the title-block region first.",
      });
    }
    const kept = found.filter((p) => !excluded.has(p.id));
    if (!kept.length) throw new PlanError(`every page of "${value}" was removed — keep at least one`, 400);
    if (found.length > 1) ambiguous.push(value);
    sides.push({ label: value, pages: kept.map(asPage) });
  }
  if (ambiguous.length) {
    notes.push(
      `More than one page claims ${ambiguous.join(", ")}. All of them are included; remove the wrong one if it is not what you meant.`,
    );
  }

  const targetPageIds = sides.flatMap((s) => s.pages.map((p) => p.pageId));
  const targetChunks = await chunksOf(targetPageIds);
  const byId = new Map(all.map((p) => [p.id, p]));
  const targetText = [
    ...targetPageIds.map((id) => byId.get(id)?.sheetRegionText ?? ""),
    ...targetChunks.map((c) => c.text),
  ].join("\n");

  let selection;
  try {
    selection = selectRfiChecks(targetText, request.checkMode, request.checkIds);
  } catch (err) {
    throw new PlanError((err as Error).message, 400);
  }

  const sheetNumbers = [...new Set(sides.flatMap((s) => s.pages.map((p) => p.sheetNumber ?? s.label)))];
  const hitLists: string[][] = [];
  let denseMissing = false;
  for (const checkId of selection.checkIds) {
    const result = await deps.retrieve(projectId, checkQuery(checkId, sheetNumbers), { limit: HITS_PER_QUERY });
    if (result.dense === 0) denseMissing = true;
    hitLists.push(result.chunkIds);
  }
  if (denseMissing) {
    notes.push("Semantic search returned nothing for at least one check; keyword and exact-identifier search still ran.");
  }

  // Everything retrieval returned, restricted to analysable pages of THIS
  // project — retrieval's own filters already exclude superseded documents,
  // but not a document someone took out of RFI analysis.
  const hitIds = [...new Set(hitLists.flat())];
  const hitRows = hitIds.length
    ? await prisma.chunk.findMany({
        where: { id: { in: hitIds }, kind: { in: [...SCOPE_KINDS] } },
        select: { id: true, pageId: true, kind: true, text: true, bbox: true, tokenCount: true },
      })
    : [];
  const chunks = new Map<string, CandidateChunk>();
  for (const r of hitRows) {
    if (!byId.has(r.pageId) || excluded.has(r.pageId)) continue;
    chunks.set(r.id, {
      chunkId: r.id,
      pageId: r.pageId,
      kind: r.kind,
      text: r.text,
      bbox: (r.bbox && typeof r.bbox === "object" ? r.bbox : null) as Box | null,
      tokenCount: r.tokenCount,
    });
  }
  const pages = new Map<string, CandidatePage>();
  for (const c of chunks.values()) pages.set(c.pageId, asPage(byId.get(c.pageId)!));

  const scope = rankScope({ depth: request.depth, sides, targetChunks, hitLists, chunks, pages });
  const hash = scopeHash(scope, selection.checkIds, request.depth);
  const model = rfiReviewModel();
  const estimate = estimateReview(scope, model, estimateCostUsd, request.depth);

  // A new plan replaces this person's earlier unstarted plans for the project:
  // re-planning after removing a sheet must not leave a trail of dead ones.
  await prisma.rfiReviewRun.updateMany({
    where: { projectId, createdById: userId, status: "planned" },
    data: { status: "cancelled" },
  });
  const run = await prisma.rfiReviewRun.create({
    data: {
      projectId,
      createdById: userId,
      target: request.target as unknown as Prisma.InputJsonValue,
      checkMode: request.checkMode,
      checkIds: selection.checkIds,
      checkReasons: selection.reasons,
      depth: request.depth,
      thinkingRequested: request.thinking,
      provider: rfiReviewProvider(),
      model,
      scope: { ...scope, ambiguous } as unknown as Prisma.InputJsonValue,
      scopeHash: hash,
      estimate: estimate as unknown as Prisma.InputJsonValue,
      notes,
    },
  });
  return run;
}

// --- DTO --------------------------------------------------------------------------

type RunRow = Prisma.RfiReviewRunGetPayload<Record<string, never>>;

const strings = (value: unknown): string[] =>
  Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : [];

/** The worker's per-stage usage JSON, priced like a scan's. */
function reviewUsage(raw: unknown, costOf: CostOf): RfiReviewRunDto["usage"] {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const u = raw as { stages?: Record<string, unknown>; total?: unknown };
  const stages: Record<string, RfiScanUsageDto> = {};
  for (const [name, value] of Object.entries(u.stages ?? {})) {
    const priced = scanUsage(value, costOf);
    if (priced) stages[name] = priced;
  }
  return { stages, total: scanUsage(u.total, costOf) };
}

export function toReviewDto(row: RunRow, candidates: number, now = new Date()): RfiReviewRunDto {
  const scope = (row.scope ?? {}) as unknown as ReviewScope & { ambiguous?: string[] };
  const chunkCount = new Map<string, number>();
  for (const c of scope.chunks ?? []) chunkCount.set(c.pageId, (chunkCount.get(c.pageId) ?? 0) + 1);
  const pages: RfiReviewPageDto[] = (scope.pages ?? []).map((p) => ({
    pageId: p.pageId,
    documentId: p.documentId,
    pageNumber: p.pageNumber,
    combinedPageNumber: p.combinedPageNumber,
    sheetNumber: p.sheetNumber,
    discipline: p.discipline,
    role: p.role,
    visual: p.visual,
    chunks: chunkCount.get(p.pageId) ?? 0,
  }));

  let status = row.status as RfiReviewStatus;
  let error = row.error;
  // A worker that died mid-run would otherwise leave "running" forever.
  if ((status === "queued" || status === "running") && !reviewIsActive(row, now)) {
    status = "failed";
    error = error ?? "the worker stopped responding during this review; start a new plan to run it again";
  }

  return {
    id: row.id,
    status,
    stage: (row.stage as RfiReviewStage | null) ?? null,
    progress: row.progress,
    target: row.target as unknown as RfiReviewTarget,
    checkMode: row.checkMode === "custom" ? "custom" : "auto",
    checkIds: strings(row.checkIds) as RfiReviewCheckId[],
    checkReasons:
      row.checkReasons && typeof row.checkReasons === "object" && !Array.isArray(row.checkReasons)
        ? (row.checkReasons as Record<string, string>)
        : {},
    depth: row.depth as RfiReviewDepth,
    thinkingRequested: row.thinkingRequested as RfiReviewThinking,
    thinkingSent: strings(row.thinkingSent),
    pages,
    ambiguous: strings(scope.ambiguous),
    chunkCount: (scope.chunks ?? []).length,
    scopeHash: row.scopeHash,
    estimate: (row.estimate as unknown as RfiReviewRunDto["estimate"]) ?? null,
    usage: reviewUsage(row.usage, estimateCostUsd),
    candidates,
    notes: strings(row.notes),
    error,
    createdAt: row.createdAt.toISOString(),
    startedAt: row.startedAt?.toISOString() ?? null,
    completedAt: row.completedAt?.toISOString() ?? null,
  };
}
