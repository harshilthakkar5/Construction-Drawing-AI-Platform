import { randomUUID } from "node:crypto";
import type { Prisma } from "@prisma/client";
import {
  RFI_REVIEW_CATALOGUE_VERSION,
  RFI_REVIEW_CHECK_IDS,
  RFI_REVIEW_DEPTHS,
  RFI_REVIEW_INPUT_LIMITS,
  reviewThinkingCapability,
  type RfiCheckPlanDto,
  type RfiCheckResultDto,
  type RfiInventoryItemDto,
  type RfiReviewCheckId,
  type RfiReviewCheckMode,
  type RfiReviewComparisonDto,
  type RfiReviewCoverageDto,
  type RfiReviewDepth,
  type RfiReviewEstimateDto,
  type RfiReviewLimitsDto,
  type RfiReviewModelOptionDto,
  type RfiReviewPageDto,
  type RfiReviewProvider,
  type RfiReviewRunDto,
  type RfiReviewStage,
  type RfiReviewStatus,
  type RfiReviewTarget,
  type RfiReviewThinking,
  type RfiScanUsageDto,
} from "@cdip/shared";
import { prisma } from "./db.js";
import { DEFAULT_RFI_REVIEW_GEMINI_MODEL, DEFAULT_RFI_REVIEW_MODEL, keyFor, rfiReviewModel, rfiReviewProvider } from "./llm.js";
import { retrieveChunkIds, type RetrievalResult } from "./retrieval.js";
import { scanUsage, type CostOf } from "./rfiScanRules.js";
import {
  SCOPE_KINDS,
  checkQuery,
  comparability,
  costDifference,
  elementFamily,
  estimateReview,
  levelOf,
  normalizeSheet,
  rankScope,
  referencedSheets,
  reviewIsActive,
  scopeHash,
  selectRfiChecks,
  type Box,
  type CandidateChunk,
  type CandidatePage,
  type ReviewScope,
} from "./rfiReviewRules.js";
import { PRICING_VERSION, estimateCostUsd, pricingConfidence, withUsageContext } from "./usage.js";

/**
 * Plans a targeted RFI review BEFORE any review model call is spent, and
 * persists the plan so the scope a person approves is exactly the scope the
 * worker later reads (docs/rfi-targeted-review.md).
 *
 * Retrieval is the chat's own `retrieveChunkIds` — dense + full-text + exact
 * identifiers, fused — called once per selected check with that check's
 * query. It is not re-implemented here and never in Python: the worker only
 * ever receives a run id and reads the stored scope. Identifiers are the
 * `cdip_identifiers()` SQL function's, the one definition the worker writes
 * `chunk_identifiers` with — never a regex of this file's own.
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
  checkMode: RfiReviewCheckMode;
  checkIds: string[];
  depth: RfiReviewDepth;
  thinking: RfiReviewThinking;
  provider?: RfiReviewProvider;
  model?: string;
  maxInputTokens?: number;
  maxThinkingTokens?: number | null;
  /** Pages the person removed from an earlier plan — related sheets that are
   * obviously unrelated, or the wrong one of two pages claiming a sheet. */
  excludePageIds: string[];
}

export interface PlanDeps {
  retrieve: (projectId: string, question: string, options: { limit: number }) => Promise<RetrievalResult>;
}

// --- Model choice ------------------------------------------------------------------

/** The vision-capable models a review may be run on. The configured default
 * of each provider is always offered; the rest are the tiers this repository
 * has run and priced. A model name the person types is not accepted — a
 * typo would plan fine and fail at the first call. */
const CANDIDATE_MODELS: Record<RfiReviewProvider, string[]> = {
  claude: ["claude-sonnet-5", "claude-opus-5", "claude-haiku-4-5"],
  gemini: ["gemini-3.6-flash", "models/gemini-3.1-pro-preview", "gemini-2.5-pro", "gemini-2.5-flash"],
};

function configuredModel(provider: RfiReviewProvider): string {
  return provider === "gemini"
    ? process.env.RFI_REVIEW_GEMINI_MODEL || DEFAULT_RFI_REVIEW_GEMINI_MODEL
    : process.env.RFI_REVIEW_MODEL || DEFAULT_RFI_REVIEW_MODEL;
}

export function reviewModelOptions(): { default: { provider: RfiReviewProvider; model: string }; options: RfiReviewModelOptionDto[] } {
  const options: RfiReviewModelOptionDto[] = [];
  for (const provider of ["claude", "gemini"] as RfiReviewProvider[]) {
    const models = [configuredModel(provider), ...CANDIDATE_MODELS[provider]];
    for (const model of [...new Set(models)]) {
      options.push({
        provider,
        model,
        available: Boolean(process.env[keyFor(provider)]),
        capability: reviewThinkingCapability(provider, model),
        priced: pricingConfidence(model) !== "unknown",
      });
    }
  }
  return { default: { provider: rfiReviewProvider(), model: rfiReviewModel() }, options };
}

export interface ResolvedSettings {
  provider: RfiReviewProvider;
  model: string;
  limits: RfiReviewLimitsDto;
}

/** Provider, model and limits for one run, validated against what the model
 * accepts: a thinking ceiling on a model that takes only an effort is
 * refused, not silently ignored. */
export function resolveSettings(request: Pick<PlanRequest, "provider" | "model" | "maxInputTokens" | "maxThinkingTokens" | "thinking" | "depth">): ResolvedSettings {
  const provider = request.provider ?? rfiReviewProvider();
  const model = request.model ?? (request.provider ? configuredModel(provider) : rfiReviewModel());
  const offered = reviewModelOptions().options.find((o) => o.provider === provider && o.model === model);
  if (!offered) {
    throw new PlanError(`"${model}" is not a ${provider} model this review can run on`, 400, {
      models: reviewModelOptions().options.filter((o) => o.provider === provider).map((o) => o.model),
    });
  }
  const maxThinkingTokens = request.maxThinkingTokens ?? null;
  if (maxThinkingTokens !== null && !offered.capability.budget) {
    throw new PlanError(`${model} does not take a thinking token limit. ${offered.capability.note} Use the effort setting instead.`, 400);
  }
  const caps = RFI_REVIEW_DEPTHS[request.depth];
  return {
    provider,
    model,
    limits: {
      maxInputTokens: request.maxInputTokens ?? RFI_REVIEW_INPUT_LIMITS.default,
      maxThinkingTokens,
      thinkingEffort: request.thinking,
      maxTotalTokens: caps.maxTotalTokens,
      maxBatches: caps.maxBatches,
    },
  };
}

// --- Pages -------------------------------------------------------------------------

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
  revision: number;
}

/** Pages a review may use: live (not superseded), processed, in this
 * project, and not excluded from RFI analysis — which is how a historical RFI
 * stays out of every retrieval and image path. The ONE filter every scope
 * query goes through. */
async function analysablePages(projectId: string): Promise<PageRow[]> {
  return prisma.$queryRaw<PageRow[]>`
    SELECT p.id, p."documentId", p."pageNumber", p."combinedPageNumber", p."sheetNumber",
           p.discipline, p."pdfWidth", p."pdfHeight", p."sheetRegionText", d.revision
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

function toCandidateChunk(r: { id: string; pageId: string; kind: string; text: string; bbox: unknown; tokenCount: number }): CandidateChunk {
  return {
    chunkId: r.id,
    pageId: r.pageId,
    kind: r.kind,
    text: r.text,
    bbox: (r.bbox && typeof r.bbox === "object" ? r.bbox : null) as Box | null,
    tokenCount: r.tokenCount,
  };
}

async function chunksOf(pageIds: string[]): Promise<CandidateChunk[]> {
  if (!pageIds.length) return [];
  const rows = await prisma.chunk.findMany({
    where: { pageId: { in: pageIds }, kind: { in: [...SCOPE_KINDS] } },
    select: { id: true, pageId: true, kind: true, text: true, bbox: true, tokenCount: true },
  });
  return rows.map(toCandidateChunk);
}

/** Every chunk carrying an element's mark, by the SAME identifier definition
 * the chunks were indexed with. */
async function elementChunks(value: string, pageIds: string[]): Promise<{ chunkId: string; pageId: string }[]> {
  if (!pageIds.length) return [];
  return prisma.$queryRaw<{ chunkId: string; pageId: string }[]>`
    SELECT DISTINCT ci."chunkId", c."pageId"
      FROM chunk_identifiers ci JOIN chunks c ON c.id = ci."chunkId"
     WHERE ci.identifier = ANY(cdip_identifiers(upper(${value})))
       AND c."pageId" = ANY(${pageIds}::text[])
       AND c.kind = 'text'`;
}

/** The identifiers the target's own words use, with how often. */
async function identifiersIn(chunkIds: string[]): Promise<Map<string, number>> {
  if (!chunkIds.length) return new Map();
  const rows = await prisma.$queryRaw<{ identifier: string; n: bigint }[]>`
    SELECT identifier, count(*) AS n FROM chunk_identifiers
     WHERE "chunkId" = ANY(${chunkIds}::text[]) GROUP BY identifier`;
  const out = new Map<string, number>();
  for (const r of rows) {
    const key = normalizeSheet(r.identifier);
    out.set(key, (out.get(key) ?? 0) + Number(r.n));
  }
  return out;
}

/** Most pages an element target may pull in before the plan asks to narrow. */
export const MAX_ELEMENT_PAGES = 6;

interface Target {
  sides: { label: string; pages: CandidatePage[] }[];
  elementPages: CandidatePage[];
  elementChunkIds: Set<string>;
  ambiguous: string[];
  notes: string[];
  element: string | null;
}

async function resolveTarget(target: RfiReviewTarget, all: PageRow[], excluded: Set<string>): Promise<Target> {
  const notes: string[] = [];
  const ambiguous: string[] = [];
  const known = () => [...new Set(all.map((p) => p.sheetNumber).filter((s): s is string => !!s))];

  if (target.type === "element") {
    const live = all.filter((p) => !excluded.has(p.id));
    const hits = await elementChunks(target.value, live.map((p) => p.id));
    const byId = new Map(live.map((p) => [p.id, p]));
    let pageIds = [...new Set(hits.map((h) => h.pageId))];
    if (!pageIds.length) {
      throw new PlanError(`"${target.value}" was not found as a mark in this project's drawings`, 404, {
        value: target.value,
        hint: "Type the mark exactly as it is printed on the plan, e.g. C-6 or PC1.",
      });
    }
    if (target.level) {
      const want = levelOf(`LEVEL ${target.level.replace(/^LEVEL\s*/i, "")}`);
      const narrowed = pageIds.filter((id) => levelOf(byId.get(id)!.sheetRegionText) === want);
      if (!narrowed.length) throw new PlanError(`"${target.value}" is not on any sheet titled ${want}`, 404, { choices: choicesFor(pageIds, byId) });
      pageIds = narrowed;
    }
    if (target.area) {
      const area = target.area.toUpperCase();
      const narrowed = pageIds.filter((id) => (byId.get(id)!.sheetRegionText ?? "").toUpperCase().includes(area));
      if (!narrowed.length) throw new PlanError(`"${target.value}" is not on any sheet whose title mentions "${target.area}"`, 404, { choices: choicesFor(pageIds, byId) });
      pageIds = narrowed;
    }
    if (pageIds.length > MAX_ELEMENT_PAGES) {
      throw new PlanError(
        `"${target.value}" is marked on ${pageIds.length} sheets — say which level or area you mean`,
        409,
        { choices: choicesFor(pageIds, byId) },
      );
    }
    const levels = new Set(pageIds.map((id) => levelOf(byId.get(id)!.sheetRegionText) ?? "?"));
    if (pageIds.length > 1) {
      ambiguous.push(target.value);
      notes.push(
        `"${target.value}" is marked on ${pageIds.length} sheets${levels.size > 1 ? ` across ${levels.size} levels` : ""}. All are included; add a level or remove the pages you did not mean.`,
      );
    }
    const family = elementFamily(target.value);
    notes.push(family ? `"${target.value}" reads as a ${family.family} mark.` : `"${target.value}" does not have the shape of any element family; every objective stays eligible.`);
    return {
      sides: [],
      elementPages: pageIds.map((id) => asPage(byId.get(id)!)),
      elementChunkIds: new Set(hits.filter((h) => pageIds.includes(h.pageId)).map((h) => h.chunkId)),
      ambiguous,
      notes,
      element: target.value,
    };
  }

  const values = target.type === "sheet" ? [target.value] : target.values;
  const sides: { label: string; pages: CandidatePage[] }[] = [];
  for (const value of values) {
    const found = resolveSheet(value, all);
    if (!found.length) {
      const sheets = known();
      throw new PlanError(`no sheet "${value}" was found in this project's drawings`, 404, {
        value,
        knownSheets: sheets.slice(0, 40),
        hint: sheets.length
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
  return { sides, elementPages: [], elementChunkIds: new Set(), ambiguous, notes, element: null };
}

function choicesFor(pageIds: string[], byId: Map<string, PageRow>) {
  return pageIds.map((id) => {
    const p = byId.get(id)!;
    return { pageId: id, sheetNumber: p.sheetNumber, level: levelOf(p.sheetRegionText) };
  });
}

// --- Plan --------------------------------------------------------------------------

export async function planReview(
  projectId: string,
  userId: string,
  request: PlanRequest,
  deps: PlanDeps = { retrieve: retrieveChunkIds },
) {
  const settings = resolveSettings(request);
  const caps = RFI_REVIEW_DEPTHS[request.depth];
  const excluded = new Set(request.excludePageIds);
  const all = await analysablePages(projectId);
  if (!all.length) {
    throw new PlanError("no processed drawings are available for RFI review in this project yet", 409);
  }
  const byId = new Map(all.map((p) => [p.id, p]));
  const target = await resolveTarget(request.target, all, excluded);
  const notes = [...target.notes];

  const namedPages = [...target.sides.flatMap((s) => s.pages), ...target.elementPages];
  const namedIds = new Set(namedPages.map((p) => p.pageId));
  const targetChunks = await chunksOf([...namedIds]);
  const elementText = targetChunks.filter((c) => target.elementChunkIds.has(c.chunkId)).map((c) => c.text);
  // An element routes on its mark and the words printed with it — not the
  // whole sheet's, which name every family the sheet happens to draw.
  const routingText = (
    target.element
      ? [target.element, ...elementText]
      : [...targetChunks.map((c) => c.text), ...[...namedIds].map((id) => byId.get(id)?.sheetRegionText ?? "")]
  ).join("\n");

  let selection;
  try {
    selection = selectRfiChecks(routingText, request.checkMode, request.checkIds, target.element);
  } catch (err) {
    throw new PlanError((err as Error).message, 400);
  }

  // Forced pages: what the target's own words point at, and the same level
  // drawn by another discipline. Added BEFORE ranking, so no cap can drop them
  // in favour of something retrieval merely found similar.
  const sheets = new Map<string, string[]>();
  for (const p of all) {
    if (!p.sheetNumber || excluded.has(p.id)) continue;
    const key = normalizeSheet(p.sheetNumber);
    sheets.set(key, [...(sheets.get(key) ?? []), p.id]);
  }
  const ownSheets = new Set(namedPages.map((p) => normalizeSheet(p.sheetNumber ?? "")).filter(Boolean));
  const refs = referencedSheets(
    await identifiersIn(targetChunks.filter((c) => c.kind === "text").map((c) => c.chunkId)),
    sheets,
    ownSheets,
  );
  const forced: { page: CandidatePage; role: "reference" | "correspondence"; reason: string }[] = [];
  const omittedForced: { sheetNumber: string | null; reason: string }[] = [];
  refs.resolved.forEach((ref, i) => {
    const page = byId.get(ref.pageIds[0]!)!;
    if (i < caps.referencePages) {
      forced.push({ page: asPage(page), role: "reference", reason: `the target refers to ${page.sheetNumber ?? ref.sheet} (${ref.mentions} mention${ref.mentions === 1 ? "" : "s"})` });
    } else {
      omittedForced.push({ sheetNumber: page.sheetNumber, reason: `referred to by the target, beyond the ${caps.referencePages} reference sheets of this depth` });
    }
  });
  if (refs.unresolved.length) {
    notes.push(`The target refers to ${refs.unresolved.join(", ")}, which ${refs.unresolved.length === 1 ? "is" : "are"} not in this project.`);
  }
  if (request.target.type !== "compare") {
    const targetDisciplines = new Set(namedPages.map((p) => p.discipline));
    const levels = new Set(namedPages.map((p) => levelOf(byId.get(p.pageId)?.sheetRegionText)).filter((l): l is string => !!l));
    const taken = new Set([...namedIds, ...forced.map((f) => f.page.pageId)]);
    const matches = all.filter(
      (p) =>
        !taken.has(p.id) &&
        !excluded.has(p.id) &&
        p.discipline &&
        !targetDisciplines.has(p.discipline) &&
        levels.has(levelOf(p.sheetRegionText) ?? ""),
    );
    matches.forEach((p, i) => {
      const level = levelOf(p.sheetRegionText);
      if (i < caps.referencePages) {
        forced.push({ page: asPage(p), role: "correspondence", reason: `the same level (${level}) drawn by ${p.discipline}` });
      } else {
        omittedForced.push({ sheetNumber: p.sheetNumber, reason: `the same level (${level}), beyond the ${caps.referencePages} corresponding sheets of this depth` });
      }
    });
  }

  // Retrieval, with every embedding/rerank call it makes tagged to this run —
  // planning's own spend, read back from the ledger rather than guessed.
  const runId = randomUUID();
  const anchors = [
    ...new Set([
      ...(target.element ? [target.element] : []),
      ...namedPages.map((p) => p.sheetNumber).filter((s): s is string => !!s),
      ...target.sides.map((s) => s.label),
    ]),
  ];
  const hitLists: string[][] = [];
  const searchLog: RfiReviewCoverageDto["searchLog"] = [];
  let denseMissing = false;
  await withUsageContext({ reviewRunId: runId, stage: "planning" }, async () => {
    for (const checkId of selection.checkIds) {
      const query = checkQuery(checkId, anchors);
      const result = await deps.retrieve(projectId, query, { limit: caps.hitsPerQuery });
      if (result.dense === 0) denseMissing = true;
      hitLists.push(result.chunkIds);
      searchLog.push({ query: `${checkId}: ${query}`, found: result.chunkIds.length, stage: "planning" });
    }
  });
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
    chunks.set(r.id, toCandidateChunk(r));
  }
  // Forced pages' own words come too, so the model can see WHAT on the
  // reference answers the target.
  for (const c of await chunksOf(forced.map((f) => f.page.pageId))) chunks.set(c.chunkId, c);
  const pages = new Map<string, CandidatePage>();
  for (const c of chunks.values()) pages.set(c.pageId, asPage(byId.get(c.pageId)!));

  const scope = rankScope({
    depth: request.depth,
    sides: target.sides,
    elementPages: target.elementPages,
    targetChunks,
    elementChunkIds: target.elementChunkIds,
    forced,
    hitLists: [...hitLists, forced.map((f) => f.page.pageId).flatMap((id) => [...chunks.values()].filter((c) => c.pageId === id).map((c) => c.chunkId))],
    hitChecks: [...selection.checkIds, "reference"],
    chunks,
    pages,
  });
  scope.omitted.push(...omittedForced);

  const planningRows = await prisma.usageEvent.findMany({ where: { reviewRunId: runId } });
  const planningTokens = planningRows.reduce((sum, r) => sum + r.inputTokens + r.outputTokens, 0);
  const planningCostUsd = planningRows.some((r) => pricingConfidence(r.model) === "unknown")
    ? null
    : planningRows.reduce((sum, r) => sum + estimateCostUsd(r), 0);

  const hash = scopeHash(scope, selection.checkIds, request.depth, { provider: settings.provider, model: settings.model, limits: settings.limits });
  const estimate = estimateFor(scope, settings, request.depth, selection.checkIds.length, { tokens: planningTokens, costUsd: planningCostUsd });

  const docIds = [...new Set(scope.pages.map((p) => p.documentId))];
  const sourceRevisions = Object.fromEntries(docIds.map((id) => [id, all.find((p) => p.documentId === id)!.revision]));
  const coverage: RfiReviewCoverageDto = {
    omittedPages: scope.omitted,
    unresolvedReferences: refs.unresolved,
    searchLog,
    omissions: [],
  };

  // A new plan replaces this person's earlier unstarted plans for the project:
  // re-planning after removing a sheet must not leave a trail of dead ones.
  await prisma.rfiReviewRun.updateMany({
    where: { projectId, createdById: userId, status: "planned" },
    data: { status: "cancelled" },
  });
  return prisma.rfiReviewRun.create({
    data: {
      id: runId,
      projectId,
      createdById: userId,
      target: request.target as unknown as Prisma.InputJsonValue,
      checkMode: request.checkMode,
      checkIds: selection.checkIds,
      checkReasons: selection.reasons,
      checkPlan: selection.plan as unknown as Prisma.InputJsonValue,
      catalogueVersion: RFI_REVIEW_CATALOGUE_VERSION,
      depth: request.depth,
      thinkingRequested: request.thinking,
      provider: settings.provider,
      model: settings.model,
      limits: settings.limits as unknown as Prisma.InputJsonValue,
      scope: { ...scope, ambiguous: target.ambiguous } as unknown as Prisma.InputJsonValue,
      scopeHash: hash,
      estimate: estimate as unknown as Prisma.InputJsonValue,
      planningUsage: { tokens: planningTokens, costUsd: planningCostUsd, calls: planningRows.length } as Prisma.InputJsonValue,
      sourceRevisions: sourceRevisions as Prisma.InputJsonValue,
      coverage: coverage as unknown as Prisma.InputJsonValue,
      notes,
    },
  });
}

function estimateFor(
  scope: ReviewScope,
  settings: ResolvedSettings,
  depth: RfiReviewDepth,
  checkCount: number,
  planning: { tokens: number; costUsd: number | null },
): RfiReviewEstimateDto {
  return estimateReview(
    scope,
    {
      provider: settings.provider,
      model: settings.model,
      depth,
      maxInputTokens: settings.limits.maxInputTokens,
      thinkingEffort: settings.limits.thinkingEffort,
      maxThinkingTokens: settings.limits.maxThinkingTokens,
      checkCount,
      pricingVersion: PRICING_VERSION,
      priced: pricingConfidence(settings.model) !== "unknown",
      planning,
    },
    estimateCostUsd,
  );
}

/**
 * What an already-planned scope would cost on another model, without
 * re-planning: the pages and evidence are the same, only the price and the
 * thinking bounds change. Nothing is stored — choosing that model means
 * planning again with it, so the approved scope names the model it runs on.
 */
export function reestimate(
  run: RunRow,
  request: Pick<PlanRequest, "provider" | "model" | "maxInputTokens" | "maxThinkingTokens" | "thinking">,
): RfiReviewEstimateDto {
  const depth = run.depth as RfiReviewDepth;
  const settings = resolveSettings({ ...request, depth });
  const planning = (run.planningUsage ?? {}) as { tokens?: number; costUsd?: number | null };
  return estimateFor(run.scope as unknown as ReviewScope, settings, depth, strings(run.checkIds).length, {
    tokens: planning.tokens ?? 0,
    costUsd: planning.costUsd ?? null,
  });
}

// --- DTO --------------------------------------------------------------------------

export type RunRow = Prisma.RfiReviewRunGetPayload<Record<string, never>>;

const strings = (value: unknown): string[] =>
  Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : [];

const record = <T>(value: unknown): Record<string, T> | null =>
  value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, T>) : null;

/** The worker's per-stage usage JSON, priced like a scan's — with an unknown
 * model's cost left unknown rather than quoted at a guessed rate. */
function reviewUsage(raw: unknown, costOf: CostOf): RfiReviewRunDto["usage"] {
  const u = record<unknown>(raw) as { stages?: Record<string, unknown>; total?: unknown } | null;
  if (!u) return null;
  const priced = (value: unknown): RfiScanUsageDto | null => {
    const usage = scanUsage(value, costOf);
    if (usage?.model && pricingConfidence(usage.model) === "unknown") return { ...usage, costUsd: null };
    return usage;
  };
  const stages: Record<string, RfiScanUsageDto> = {};
  for (const [name, value] of Object.entries(u.stages ?? {})) {
    const p = priced(value);
    if (p) stages[name] = p;
  }
  return { stages, total: priced(u.total) };
}

/** All 16 as the plan left them; a row planned before the catalogue grew
 * reports the checks it did not know as not selected. */
function checkPlanOf(row: RunRow): Record<string, RfiCheckPlanDto> {
  const stored = record<RfiCheckPlanDto>(row.checkPlan) ?? {};
  const reasons = record<string>(row.checkReasons) ?? {};
  const selected = new Set(strings(row.checkIds));
  return Object.fromEntries(
    RFI_REVIEW_CHECK_IDS.map((id) => [
      id,
      stored[id] ?? {
        selected: selected.has(id),
        applicability: "unknown",
        reason: reasons[id] ?? (selected.has(id) ? "selected" : "not selected in this run"),
      },
    ]),
  );
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
    reason: p.reason ?? (p.role === "related" ? "found by retrieval" : "named in the target"),
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

  const depth = row.depth as RfiReviewDepth;
  const caps = RFI_REVIEW_DEPTHS[depth] ?? RFI_REVIEW_DEPTHS.standard;
  const storedLimits = record<unknown>(row.limits) as Partial<RfiReviewLimitsDto> | null;
  const limits: RfiReviewLimitsDto = {
    maxInputTokens: storedLimits?.maxInputTokens ?? RFI_REVIEW_INPUT_LIMITS.default,
    maxThinkingTokens: storedLimits?.maxThinkingTokens ?? null,
    thinkingEffort: (storedLimits?.thinkingEffort ?? row.thinkingRequested) as RfiReviewThinking,
    maxTotalTokens: storedLimits?.maxTotalTokens ?? caps.maxTotalTokens,
    maxBatches: storedLimits?.maxBatches ?? caps.maxBatches,
  };
  const storedCoverage = record<unknown>(row.coverage) as Partial<RfiReviewCoverageDto> | null;
  const coverage: RfiReviewCoverageDto = {
    omittedPages: storedCoverage?.omittedPages ?? scope.omitted ?? [],
    unresolvedReferences: storedCoverage?.unresolvedReferences ?? [],
    searchLog: storedCoverage?.searchLog ?? [],
    omissions: storedCoverage?.omissions ?? [],
  };
  const checkMode: RfiReviewCheckMode =
    row.checkMode === "custom" || row.checkMode === "all_original" ? row.checkMode : "auto";

  return {
    id: row.id,
    status,
    stage: (row.stage as RfiReviewStage | null) ?? null,
    progress: row.progress,
    target: row.target as unknown as RfiReviewTarget,
    checkMode,
    checkIds: strings(row.checkIds) as RfiReviewCheckId[],
    checkReasons: record<string>(row.checkReasons) ?? {},
    checkPlan: checkPlanOf(row),
    checkResults: record<RfiCheckResultDto>(row.checkResults),
    inventory: Array.isArray(row.inventory) ? (row.inventory as unknown as RfiInventoryItemDto[]) : [],
    coverage,
    catalogueVersion: row.catalogueVersion || "before the 16-question catalogue",
    depth,
    provider: (row.provider === "gemini" ? "gemini" : "claude") as RfiReviewProvider,
    model: row.model ?? "",
    limits,
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

/** A finished run's actual cost and tokens, or null where it is unknown. */
function actual(row: RunRow): { costUsd: number | null; tokens: number | null } {
  const total = reviewUsage(row.usage, estimateCostUsd)?.total;
  if (!total) return { costUsd: null, tokens: null };
  return { costUsd: total.costUsd ?? null, tokens: total.inputTokens + total.outputTokens };
}

/** Two runs side by side — cost AND whether the comparison means anything. */
export function compareRuns(base: RunRow, other: RunRow): RfiReviewComparisonDto {
  const a = actual(base);
  const b = actual(other);
  const complete = (r: RunRow) => r.status === "ready";
  const facts = (r: RunRow) => ({
    target: r.target,
    checkIds: strings(r.checkIds),
    sourceRevisions: (record<number>(r.sourceRevisions) ?? {}) as Record<string, number>,
    complete: complete(r),
  });
  const verdict = comparability(facts(base), facts(other));
  const diff = costDifference(a.costUsd, b.costUsd);
  if (a.costUsd === null || b.costUsd === null) verdict.caveats.push("at least one run's cost is unknown, so no difference is shown");
  return {
    base: { id: base.id, model: base.model ?? "", ...a },
    other: { id: other.id, model: other.model ?? "", ...b },
    differenceUsd: diff.usd,
    differencePercent: diff.percent,
    ...verdict,
  };
}
