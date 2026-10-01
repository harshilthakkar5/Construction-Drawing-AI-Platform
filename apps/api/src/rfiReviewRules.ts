import { createHash } from "node:crypto";
import {
  RFI_REVIEW_CHECKS,
  RFI_REVIEW_CHECK_IDS,
  RFI_REVIEW_DEPTHS,
  type RfiCheckPlanDto,
  type RfiReviewCheckId,
  type RfiReviewCheckMode,
  type RfiReviewDepth,
  type RfiReviewEstimateDto,
  type RfiReviewProvider,
  type RfiReviewStatus,
  type RfiReviewThinking,
} from "@cdip/shared";

/**
 * The decisions behind a targeted RFI review's PLAN, kept pure so they are
 * tested without a database, a queue or a model (docs/rfi-targeted-review.md).
 * rfiReviewPlanner.ts gathers the inputs; this decides what they mean.
 */

/** "A3.27", "a-3.27" and "A327" name one sheet — the same normalization
 * rfi_checks.normalize and cdip_identifiers() apply. */
export function normalizeSheet(value: string): string {
  return value.toUpperCase().replace(/[^A-Z0-9]/g, "");
}

// --- Routing ----------------------------------------------------------------------

/** The element families a mark's shape names. A routing SEED, never a limit:
 * the family's checks are added, nothing else is removed. */
const ELEMENT_FAMILIES: { pattern: RegExp; family: string; checks: RfiReviewCheckId[] }[] = [
  { pattern: /^(SW|CW)[-\s]?\d/, family: "shear or core wall", checks: ["C02"] },
  { pattern: /^(PC|PILE|P)[-\s]?\d/, family: "pile or pile cap", checks: ["F04"] },
  { pattern: /^(WF|CF)[-\s]?\d/, family: "wall footing", checks: ["F02"] },
  { pattern: /^(MAT|MF)[-\s]?\d/, family: "mat foundation", checks: ["F03"] },
  { pattern: /^(F|FTG|SF)[-\s]?\d/, family: "footing", checks: ["F01"] },
  { pattern: /^(C|COL)[-\s]?\d/, family: "column", checks: ["C01", "C03"] },
  { pattern: /^(B|BM|G|GB|J)[-\s]?\d/, family: "beam", checks: ["B01", "B02", "B03"] },
  { pattern: /^(S|SL|SLAB)[-\s]?\d/, family: "slab", checks: ["FL01", "FL02", "FL03", "FL04"] },
];

/** The family an element mark belongs to, when its shape says so. */
export function elementFamily(mark: string): { family: string; checks: RfiReviewCheckId[] } | null {
  const upper = mark.trim().toUpperCase();
  const hit = ELEMENT_FAMILIES.find((f) => f.pattern.test(upper));
  return hit ? { family: hit.family, checks: hit.checks } : null;
}

export interface CheckSelection {
  checkIds: RfiReviewCheckId[];
  /** Why each SELECTED check was chosen, in words the plan screen shows. */
  reasons: Record<string, string>;
  /** All 16: selected or not, applicability, reason. Never a silent skip. */
  plan: Record<string, RfiCheckPlanDto>;
}

/**
 * Which of the 16 objectives a review runs, and what the plan says about each
 * of the others. Deterministic on purpose: which families are in play is
 * decided here, from the target's own words and the element's mark, never by
 * a model — a model deciding a family is absent is a model deciding what not
 * to look at.
 *
 *   all_original  every objective runs; applicability is still reported.
 *   custom        exactly the ids given; the rest are `not_selected` and say so.
 *   auto          G01 and G02 always; a family when the target names it (a
 *                 keyword, or an element mark's shape). When the target names
 *                 NO family at all — a bare sheet number — ambiguity keeps
 *                 every objective eligible and all 16 run. A family auto left
 *                 out is marked `unknown` with the reason, never dropped
 *                 silently.
 */
export function selectRfiChecks(
  targetText: string,
  mode: RfiReviewCheckMode,
  custom: readonly string[] = [],
  element: string | null = null,
): CheckSelection {
  const text = targetText.toUpperCase();
  const family = element ? elementFamily(element) : null;
  const signal = (id: RfiReviewCheckId): string | null => {
    const check = RFI_REVIEW_CHECKS.find((c) => c.id === id)!;
    if (check.applicability.always) return "runs on every plan review";
    if (family?.checks.includes(id)) return `"${element}" is a ${family.family} mark`;
    const hit = (check.applicability.keywords as readonly string[]).find((k) => mentions(text, k));
    return hit ? `the target mentions "${hit}"` : null;
  };

  if (mode === "custom") {
    const unknown = custom.filter((id) => !(RFI_REVIEW_CHECK_IDS as readonly string[]).includes(id));
    if (unknown.length) throw new Error(`unknown check id(s): ${unknown.join(", ")}`);
    if (!custom.length) throw new Error("choose at least one check");
  }

  const anyFamily = RFI_REVIEW_CHECKS.some((c) => !c.applicability.always && signal(c.id));
  const checkIds: RfiReviewCheckId[] = [];
  const reasons: Record<string, string> = {};
  const plan: Record<string, RfiCheckPlanDto> = {};
  for (const check of RFI_REVIEW_CHECKS) {
    const why = signal(check.id);
    const applicability = why ? "applicable" : "unknown";
    let selected: boolean;
    let reason: string;
    if (mode === "all_original") {
      selected = true;
      reason = why ?? "every original question runs; discovery reports if this one does not apply";
    } else if (mode === "custom") {
      selected = custom.includes(check.id);
      reason = selected ? "chosen by you" : "not chosen — pick it in Custom or run All original questions";
    } else if (why) {
      selected = true;
      reason = why;
    } else if (!anyFamily) {
      selected = true;
      reason = "the target does not say which elements it shows, so every objective runs";
    } else {
      selected = false;
      reason = "nothing in the target points at it — choose it in Custom or run All original questions";
    }
    plan[check.id] = { selected, applicability, reason };
    if (selected) {
      checkIds.push(check.id);
      reasons[check.id] = reason;
    }
  }
  return { checkIds, reasons, plan };
}

/** Whether the target's words use a keyword as a WORD: "COL" is not in
 * "COLOR", "MAT" not in "MATERIAL", "PC" not in "SPC". A keyword ending in a
 * separator ("C-", "SW-") is a mark prefix and needs only its front edge. */
export function mentions(upperText: string, keyword: string): boolean {
  const escaped = keyword.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const tail = /[A-Z0-9]$/.test(keyword) ? "(?![A-Z0-9])" : "";
  const head = /^[A-Z0-9]/.test(keyword) ? "(?<![A-Z0-9])" : "";
  return new RegExp(`${head}${escaped}${tail}`).test(upperText);
}

/** The retrieval question for one check: the target's sheet numbers (and the
 * element mark) first, so the exact-identifier arm finds them, then the
 * check's own concepts. */
export function checkQuery(checkId: RfiReviewCheckId, anchors: readonly string[]): string {
  const check = RFI_REVIEW_CHECKS.find((c) => c.id === checkId)!;
  return [...anchors, check.query].join(" ").trim();
}

/** "LEVEL 14", "LEVEL 5" out of a title — the one piece of a title a
 * same-level correspondence is matched on. Null when the title names none. */
export function levelOf(text: string | null | undefined): string | null {
  const found = /\bLEVEL\s*([0-9]{1,3}|[A-Z]{1,2}\d?)\b/.exec((text ?? "").toUpperCase());
  // "LEVEL 01" and "LEVEL 1" are one floor.
  return found ? `LEVEL ${(found[1] ?? "").replace(/^0+(?=\d)/, "")}` : null;
}

/** A sheet number's SHAPE: its letters and how many digits follow. "A3.35"
 * and "A3.27" share one; "C6" (a column mark) does not share S2.105's. */
export function sheetShape(normalized: string): string | null {
  const m = /^([A-Z]{1,3})(\d+)([A-Z]?)$/.exec(normalized);
  return m ? `${m[1]}${m[2]!.length}${m[3] ? "x" : ""}` : null;
}

/**
 * Which identifiers a target's text uses that name another sheet of the set,
 * and which look like sheet numbers but name nothing in the project. Only an
 * identifier with the SHAPE of the set's own sheet numbers can be an
 * unresolved reference — otherwise every column mark would be one.
 */
export function referencedSheets(
  identifiers: ReadonlyMap<string, number>,
  sheets: ReadonlyMap<string, string[]>,
  exclude: ReadonlySet<string>,
): { resolved: { sheet: string; pageIds: string[]; mentions: number }[]; unresolved: string[] } {
  const shapes = new Set([...sheets.keys()].map(sheetShape).filter((s): s is string => !!s));
  const resolved = [];
  const unresolved = [];
  for (const [ident, mentions] of identifiers) {
    if (exclude.has(ident)) continue;
    const pageIds = sheets.get(ident);
    if (pageIds) resolved.push({ sheet: ident, pageIds, mentions });
    else if (shapes.has(sheetShape(ident) ?? "")) unresolved.push(ident);
  }
  resolved.sort((a, b) => b.mentions - a.mentions || a.sheet.localeCompare(b.sheet));
  return { resolved, unresolved: unresolved.sort() };
}

// --- Scope ------------------------------------------------------------------------

/** What may enter a review, in trust order (the plan's evidence hierarchy):
 * the sheet's own words, geometry measured from it, and — as context only —
 * a vision model's account of it. Summaries are never evidence. */
export const SCOPE_KINDS = ["text", "gridmarks", "description"] as const;

export interface ScopePage {
  pageId: string;
  documentId: string;
  pageNumber: number;
  combinedPageNumber: number | null;
  sheetNumber: string | null;
  discipline: string | null;
  pdfWidth: number | null;
  pdfHeight: number | null;
  role: string;
  /** Why the page is in the scope, in words. */
  reason: string;
  visual: boolean;
  /** Where to crop on this page: the boxes of its highest-ranked chunks,
   * decided here so the worker renders exactly what the plan priced. */
  crops: { chunkId: string; bbox: Box }[];
}

export interface Box {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface ScopeChunk {
  chunkId: string;
  pageId: string;
  kind: string;
  text: string;
  bbox: Box | null;
  tokenCount: number;
  /** Retrieval rank fused across the check queries; 0 for a target chunk
   * that no query surfaced. */
  score: number;
}

export interface ReviewScope {
  sides: { label: string; pageIds: string[] }[];
  pages: ScopePage[];
  chunks: ScopeChunk[];
  /** Pages that qualified but were left out by a cap. */
  omitted: { sheetNumber: string | null; reason: string }[];
}

export type CandidateChunk = Omit<ScopeChunk, "score">;
export type CandidatePage = Omit<ScopePage, "role" | "visual" | "crops" | "reason">;

const RRF_K = 60;

export interface RankInput {
  depth: RfiReviewDepth;
  /** Each named side's pages, in the order the user named them. */
  sides: { label: string; pages: CandidatePage[] }[];
  /** Pages the target's element mark sits on (element targets). */
  elementPages?: CandidatePage[];
  /** Every chunk on the target pages. */
  targetChunks: CandidateChunk[];
  /** Chunks that carry the element's mark: kept, and cropped first. */
  elementChunkIds?: ReadonlySet<string>;
  /** Pages pulled in BEFORE ranking: sheets the target refers to, and
   * same-level plans of another discipline. Each with its reason. */
  forced?: { page: CandidatePage; role: "reference" | "correspondence"; reason: string }[];
  /** One ranked chunk-id list per check query, as retrieval returned it. */
  hitLists: string[][];
  /** Which check each hit list belongs to, parallel to hitLists. */
  hitChecks?: string[];
  /** Metadata for every chunk id that can appear in hitLists or targetChunks,
   * already filtered to live, analysable documents of this project. */
  chunks: Map<string, CandidateChunk>;
  pages: Map<string, CandidatePage>;
}

/**
 * The bounded scope: which chunks and pages a review may use.
 *
 * Caps are CAPS: a chunk earns its place by rank, and an over-full plan drops
 * its weakest evidence rather than whatever the database returned last — and
 * says which pages it dropped. Never dropped for rank:
 *   - the pages the user named (sides, or the element's pages),
 *   - the chunks carrying the element's mark,
 *   - the geometry measured on the named pages (gridmarks), and
 *   - forced pages (references, same-level correspondences), which a reranker
 *     must not be allowed to discard, since the comparison IS them.
 */
export function rankScope(input: RankInput): ReviewScope {
  const caps = RFI_REVIEW_DEPTHS[input.depth];
  const allowed = new Set<string>(SCOPE_KINDS);
  const named = [...input.sides.flatMap((s) => s.pages), ...(input.elementPages ?? [])];
  const targetPageIds = new Set(named.map((p) => p.pageId));
  const elementIds = input.elementChunkIds ?? new Set<string>();

  const score = new Map<string, number>();
  const foundBy = new Map<string, Set<string>>();
  input.hitLists.forEach((list, i) => {
    const check = input.hitChecks?.[i];
    list.forEach((id, rank) => {
      score.set(id, (score.get(id) ?? 0) + 1 / (RRF_K + rank + 1));
      if (check) {
        const page = input.chunks.get(id)?.pageId;
        if (page) {
          if (!foundBy.has(page)) foundBy.set(page, new Set());
          foundBy.get(page)!.add(check);
        }
      }
    });
  });

  const mustKeep = input.targetChunks.filter((c) => c.kind === "gridmarks" || elementIds.has(c.chunkId));
  const mustIds = new Set(mustKeep.map((c) => c.chunkId));
  const pool = new Map<string, CandidateChunk>();
  for (const c of input.targetChunks) pool.set(c.chunkId, c);
  for (const id of score.keys()) {
    const c = input.chunks.get(id);
    if (c) pool.set(id, c);
  }
  const ranked = [...pool.values()]
    .filter((c) => allowed.has(c.kind) && !mustIds.has(c.chunkId))
    // A target chunk no query surfaced still outranks nothing-at-all, but
    // not a related chunk that retrieval actually chose.
    .map((c) => ({ c, s: (score.get(c.chunkId) ?? 0) + (targetPageIds.has(c.pageId) ? 0.25 / RRF_K : 0) }))
    .sort((a, b) => b.s - a.s || a.c.chunkId.localeCompare(b.c.chunkId));

  const room = Math.max(0, caps.chunks - mustKeep.length);
  const chosen: ScopeChunk[] = [
    // Element chunks rank first so they are the first crops on their page.
    ...mustKeep.map((c) => ({ ...c, score: elementIds.has(c.chunkId) ? 1 : (score.get(c.chunkId) ?? 0) })),
    ...ranked.slice(0, room).map(({ c, s }) => ({ ...c, score: s })),
  ];

  const pageOrder: { page: CandidatePage; role: string; reason: string }[] = [];
  const seen = new Set<string>();
  const add = (page: CandidatePage, role: string, reason: string) => {
    if (seen.has(page.pageId)) return;
    seen.add(page.pageId);
    pageOrder.push({ page, role, reason });
  };
  input.sides.forEach((side, i) => {
    for (const page of side.pages) {
      add(
        page,
        input.sides.length > 1 ? `side:${i}` : "target",
        input.sides.length > 1 ? `named in the comparison as "${side.label}"` : `the sheet you named ("${side.label}")`,
      );
    }
  });
  for (const page of input.elementPages ?? []) add(page, "element", "the element you named is drawn here");
  for (const f of input.forced ?? []) add(f.page, f.role, f.reason);

  const bestByPage = new Map<string, number>();
  for (const c of chosen) bestByPage.set(c.pageId, Math.max(bestByPage.get(c.pageId) ?? 0, c.score));
  const related = [...bestByPage.entries()]
    .filter(([id]) => !seen.has(id) && input.pages.has(id))
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  for (const [id] of related) {
    const checks = [...(foundBy.get(id) ?? [])].sort();
    add(input.pages.get(id)!, "related", checks.length ? `found by the search for ${checks.join(", ")}` : "found by retrieval");
  }

  // Pages retrieval matched whose every chunk lost the rank cut: said, not hidden.
  const omitted: { sheetNumber: string | null; reason: string }[] = [];
  for (const [pageId, checks] of foundBy) {
    if (seen.has(pageId) || !input.pages.has(pageId)) continue;
    omitted.push({
      sheetNumber: input.pages.get(pageId)!.sheetNumber,
      reason: `matched ${[...checks].sort().join(", ")} but ranked below the ${caps.chunks}-piece cap of this depth`,
    });
  }

  let visualLeft = caps.visualPages;
  const pages: ScopePage[] = pageOrder.map(({ page, role, reason }) => {
    // Named and forced pages are always rendered; related pages while room remains.
    const visual = role !== "related" ? true : visualLeft > 0;
    if (visual) visualLeft--;
    const crops = visual ? cropAnchors(chosen.filter((c) => c.pageId === page.pageId), caps.cropsPerPage) : [];
    if (!visual) omitted.push({ sheetNumber: page.sheetNumber, reason: `text only — beyond the ${caps.visualPages} rendered pages of this depth` });
    return { ...page, role, reason, visual, crops };
  });

  return {
    sides: input.sides.map((s) => ({ label: s.label, pageIds: s.pages.map((p) => p.pageId) })),
    pages,
    chunks: chosen,
    omitted,
  };
}

/**
 * Where to crop a page: its best-ranked chunks with a usable box. A gridmarks
 * chunk's box is the whole grid's extent, which is the whole-page overview
 * again, so it is never a crop; neither is a chunk with no box.
 */
export function cropAnchors(chunks: ScopeChunk[], limit: number): { chunkId: string; bbox: Box }[] {
  return chunks
    .filter((c) => c.kind !== "gridmarks" && c.bbox && c.bbox.width > 0 && c.bbox.height > 0)
    .sort((a, b) => b.score - a.score || a.chunkId.localeCompare(b.chunkId))
    .slice(0, limit)
    .map((c) => ({ chunkId: c.chunkId, bbox: c.bbox! }));
}

/**
 * The identity of what a person approved. It covers everything the worker
 * will read — which checks, which pages (and which of them are rendered),
 * which chunks, which model and limits — so a re-plan that changes any of it
 * is a different hash, and `start` can prove the scope it queues is the one on
 * the screen.
 */
export function scopeHash(scope: ReviewScope, checkIds: readonly string[], depth: string, settings: object = {}): string {
  const body = JSON.stringify({
    checks: [...checkIds].sort(),
    depth,
    // Canonical: the settings come back from a JSONB column, which reorders
    // object keys, and a hash over the stored copy must equal the planned one.
    settings: canonical(settings),
    pages: scope.pages.map((p) => `${p.documentId}:${p.pageNumber}:${p.role}:${p.visual ? 1 : 0}`).sort(),
    crops: scope.pages.flatMap((p) => p.crops.map((c) => c.chunkId)).sort(),
    chunks: scope.chunks.map((c) => c.chunkId).sort(),
  });
  return createHash("sha256").update(body).digest("hex");
}

/** A value with every object's keys sorted, so equal data stringifies equally. */
export function canonical(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.keys(value).sort().map((k) => [k, canonical((value as Record<string, unknown>)[k])]));
  }
  return value;
}

// --- Estimate ---------------------------------------------------------------------

/** Rough per-image input tokens: ~1.15 megapixels on Claude (~1,600), and the
 * fixed per-part budget Gemini's ultra_high media resolution spends is the
 * same order. An estimate, not a quote. */
export const IMAGE_TOKENS = 1600;
const SYSTEM_TOKENS = 2500;
const PER_CHUNK_OVERHEAD = 40;
const OUTPUT = { discovery: 4000, reasoning: 2000, verification: 2000 } as const;
/** The output headroom the worker adds per call for an effort setting (the
 * same numbers workers/src/llm.py spends) — the top of the thinking range. */
const EFFORT_HEADROOM: Record<RfiReviewThinking, number> = { low: 1024, medium: 4096, high: 8192 };

export type CostOf = (row: { model: string; inputTokens: number; outputTokens: number; cacheReadTokens: number; cacheWriteTokens: number }) => number;

export interface EstimateSettings {
  provider: RfiReviewProvider;
  model: string;
  depth: RfiReviewDepth;
  maxInputTokens: number;
  thinkingEffort: RfiReviewThinking;
  maxThinkingTokens: number | null;
  checkCount: number;
  pricingVersion: string;
  /** False when the model's price is a guess: the range is then null-priced. */
  priced: boolean;
  planning: { tokens: number; costUsd: number | null };
}

/**
 * What a plan will roughly cost, as a RANGE. Discovery sees everything,
 * split into as many calls as the input limit needs (capped at the depth's
 * batches); reasoning sees the observations (text only); verification re-sees
 * about half. The low end assumes no thinking and the images at their size;
 * the high end adds the full thinking headroom on every call — a model decides
 * how much it reasons, and a single number invented for it would be the one
 * line with nothing behind it.
 */
export function estimateReview(scope: ReviewScope, settings: EstimateSettings, costOf: CostOf): RfiReviewEstimateDto {
  const caps = RFI_REVIEW_DEPTHS[settings.depth];
  const chunkTokens = scope.chunks.reduce((sum, c) => sum + c.tokenCount + PER_CHUNK_OVERHEAD, 0);
  const visualPages = scope.pages.filter((p) => p.visual);
  // Side-by-side pairs exist only where two rendered sheets line up, which the
  // worker learns from the PDFs; priced at the most it may send.
  const pairImages = visualPages.length >= 2 ? 2 * caps.pairWindows : 0;
  const imageParts = visualPages.reduce((sum, p) => sum + 1 + p.crops.length, 0) + pairImages;
  const imageTokens = imageParts * IMAGE_TOKENS;
  const evidence = chunkTokens + imageTokens;
  const batches = Math.min(caps.maxBatches, Math.max(1, Math.ceil(evidence / Math.max(1, settings.maxInputTokens - SYSTEM_TOKENS))));
  const discoveryOut = OUTPUT.discovery + 250 * settings.checkCount;
  const discovery = batches * SYSTEM_TOKENS + Math.min(evidence, batches * settings.maxInputTokens);
  const reasoning = SYSTEM_TOKENS + discoveryOut * batches + scope.chunks.length * 15;
  const verification = SYSTEM_TOKENS + OUTPUT.reasoning + Math.round(evidence / 2);
  const inputTokens = discovery + reasoning + verification;
  const calls = batches + 2;
  const outputTokens = discoveryOut * batches + OUTPUT.reasoning + OUTPUT.verification;
  const thinkingPerCall = settings.maxThinkingTokens ?? EFFORT_HEADROOM[settings.thinkingEffort];
  const cost = (input: number, output: number) =>
    costOf({ model: settings.model, inputTokens: input, outputTokens: output, cacheReadTokens: 0, cacheWriteTokens: 0 });
  const assumptions = [
    `~${IMAGE_TOKENS} input tokens per image (${imageParts} images, including up to ${pairImages} side-by-side)`,
    `${batches} discovery call(s) at up to ${settings.maxInputTokens.toLocaleString()} input tokens each`,
    `thinking up to ${thinkingPerCall.toLocaleString()} tokens per call on the high end, none on the low end`,
    "the model decides how much it reasons and writes; the actual cost is shown after the run",
  ];
  if (evidence > caps.maxBatches * settings.maxInputTokens) {
    assumptions.push(`the evidence exceeds ${caps.maxBatches} call(s) at this input limit; the rest is omitted and the run is marked partial`);
  }
  return {
    modelCalls: calls,
    inputTokens,
    outputTokens,
    imageParts,
    costUsd: settings.priced ? cost(inputTokens, outputTokens) : null,
    costLowUsd: settings.priced ? cost(Math.round(inputTokens * 0.8), Math.round(outputTokens * 0.5)) : null,
    costHighUsd: settings.priced ? cost(Math.round(inputTokens * 1.2), outputTokens + thinkingPerCall * calls) : null,
    model: settings.model,
    provider: settings.provider,
    pricingVersion: settings.priced ? settings.pricingVersion : "no published price for this model — cost unknown",
    planningCostUsd: settings.planning.costUsd,
    planningTokens: settings.planning.tokens,
    assumptions,
  };
}

// --- Lifecycle --------------------------------------------------------------------

/**
 * A review presumed dead: queued or running, with no heartbeat for this long.
 * Keyed on the heartbeat (the worker touches it every few seconds while it
 * works) rather than the start time, because a deep review can legitimately
 * run long — the same rule summaries use.
 */
export const STALE_REVIEW_MS = 15 * 60 * 1000;

export function reviewIsActive(
  run: { status: RfiReviewStatus; createdAt: Date; startedAt: Date | null; heartbeatAt: Date | null },
  now: Date,
): boolean {
  if (run.status !== "queued" && run.status !== "running") return false;
  const since = (run.heartbeatAt ?? run.startedAt ?? run.createdAt).getTime();
  return now.getTime() - since < STALE_REVIEW_MS;
}

/**
 * Why a planned scope can no longer be started, or null when it still can.
 * A document revised since planning (superseded or a new revision number),
 * taken out of RFI analysis, or re-ingested (its chunk ids re-minted) means
 * the approved evidence is not what the project now says — analysing it would
 * be analysing old drawings.
 */
export function staleReason(
  scope: ReviewScope,
  live: { documentIds: Set<string>; chunkIds: Set<string>; revisions?: Map<string, number> },
  planned: Record<string, number> = {},
): string | null {
  const docs = new Set(scope.pages.map((p) => p.documentId));
  for (const id of docs) {
    if (!live.documentIds.has(id)) {
      return "a document in this plan was revised, removed or excluded from RFI analysis since it was planned";
    }
    const now = live.revisions?.get(id);
    if (planned[id] !== undefined && now !== undefined && now !== planned[id]) {
      return "a document in this plan has a new revision since it was planned";
    }
  }
  const missing = scope.chunks.filter((c) => !live.chunkIds.has(c.chunkId)).length;
  if (missing) return `${missing} piece(s) of evidence in this plan were re-processed since it was planned`;
  return null;
}

// --- Comparison -------------------------------------------------------------------

/**
 * Whether two runs' costs may be compared as like for like. A different
 * target, check set, drawing revision or coverage means a cheaper run may
 * simply have looked at less — the caveats say which.
 */
export function comparability(
  a: { target: unknown; checkIds: string[]; sourceRevisions: Record<string, number>; complete: boolean },
  b: { target: unknown; checkIds: string[]; sourceRevisions: Record<string, number>; complete: boolean },
): { sameTarget: boolean; sameChecks: boolean; sameRevisions: boolean; sameCoverage: boolean; caveats: string[] } {
  const sameTarget = JSON.stringify(a.target) === JSON.stringify(b.target);
  const sameChecks = [...a.checkIds].sort().join() === [...b.checkIds].sort().join();
  const sameRevisions = JSON.stringify(sortKeys(a.sourceRevisions)) === JSON.stringify(sortKeys(b.sourceRevisions));
  const sameCoverage = a.complete === b.complete;
  const caveats: string[] = [];
  if (!sameTarget) caveats.push("the two runs reviewed different targets");
  if (!sameChecks) caveats.push("the two runs ran different checks");
  if (!sameRevisions) caveats.push("the drawings changed between the two runs");
  if (!sameCoverage) caveats.push("one run was partial — it looked at less");
  if (!caveats.length) caveats.push("same target, checks and drawings — the difference is the model and settings, not the inputs. It says nothing about which found more");
  return { sameTarget, sameChecks, sameRevisions, sameCoverage, caveats };
}

function sortKeys(o: Record<string, number>): [string, number][] {
  return Object.entries(o).sort(([x], [y]) => x.localeCompare(y));
}

/** Absolute and percentage difference, only when both are known and the
 * base is non-zero — never a percentage of nothing. */
export function costDifference(base: number | null, other: number | null): { usd: number | null; percent: number | null } {
  if (base === null || other === null) return { usd: null, percent: null };
  return { usd: other - base, percent: base > 0 ? ((other - base) / base) * 100 : null };
}
