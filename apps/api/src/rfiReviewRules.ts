import { createHash } from "node:crypto";
import {
  RFI_REVIEW_CHECKS,
  RFI_REVIEW_CHECK_IDS,
  RFI_REVIEW_DEPTHS,
  type RfiReviewCheckId,
  type RfiReviewDepth,
  type RfiReviewEstimateDto,
  type RfiReviewStatus,
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

export interface CheckSelection {
  checkIds: RfiReviewCheckId[];
  /** Why each was chosen, in words the plan screen shows. */
  reasons: Record<string, string>;
}

/**
 * Which checks a review runs. Deterministic on purpose: which families are
 * in play is decided here, from the target's own words, and never left to a
 * model — a model deciding that a family is absent is a model deciding what
 * not to look at.
 *
 * `auto`: a check with no keywords always runs (grid and columns are on every
 * plan); a keyworded one runs when the target's text says it applies. When in
 * doubt a check is included rather than dropped — a check that finds nothing
 * costs a little, one that never ran costs a missed RFI and says nothing.
 *
 * `custom`: exactly the ids given, validated against the catalogue.
 */
export function selectRfiChecks(
  targetText: string,
  mode: "auto" | "custom",
  custom: readonly string[] = [],
): CheckSelection {
  if (mode === "custom") {
    const unknown = custom.filter((id) => !(RFI_REVIEW_CHECK_IDS as readonly string[]).includes(id));
    if (unknown.length) throw new Error(`unknown check id(s): ${unknown.join(", ")}`);
    const checkIds = RFI_REVIEW_CHECK_IDS.filter((id) => custom.includes(id));
    if (!checkIds.length) throw new Error("choose at least one check");
    return { checkIds, reasons: Object.fromEntries(checkIds.map((id) => [id, "chosen by you"])) };
  }
  const text = targetText.toUpperCase();
  const checkIds: RfiReviewCheckId[] = [];
  const reasons: Record<string, string> = {};
  for (const check of RFI_REVIEW_CHECKS) {
    const keywords: readonly string[] = check.autoKeywords;
    if (keywords.length === 0) {
      checkIds.push(check.id);
      reasons[check.id] = "runs on every plan review";
      continue;
    }
    const hit = keywords.find((k) => text.includes(k));
    if (hit) {
      checkIds.push(check.id);
      reasons[check.id] = `the target mentions "${hit}"`;
    }
  }
  return { checkIds, reasons };
}

/** The retrieval question for one check: the target's sheet numbers first
 * (so the exact-identifier arm finds them), then the check's own concepts. */
export function checkQuery(checkId: RfiReviewCheckId, sheetNumbers: readonly string[]): string {
  const check = RFI_REVIEW_CHECKS.find((c) => c.id === checkId)!;
  return [...sheetNumbers, check.query].join(" ").trim();
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
}

export type CandidateChunk = Omit<ScopeChunk, "score">;
export type CandidatePage = Omit<ScopePage, "role" | "visual" | "crops">;

const RRF_K = 60;

export interface RankInput {
  depth: RfiReviewDepth;
  /** Each named side's pages, in the order the user named them. */
  sides: { label: string; pages: CandidatePage[] }[];
  /** Every chunk on the target pages. */
  targetChunks: CandidateChunk[];
  /** One ranked chunk-id list per check query, as retrieval returned it. */
  hitLists: string[][];
  /** Metadata for every chunk id that can appear in hitLists or targetChunks,
   * already filtered to live, analysable documents of this project. */
  chunks: Map<string, CandidateChunk>;
  pages: Map<string, CandidatePage>;
}

/**
 * The bounded scope: which chunks and pages a review may use.
 *
 * Caps are CAPS: a chunk earns its place by rank, and an over-full plan drops
 * its weakest evidence rather than whatever the database returned last.
 * Two things are never dropped for rank:
 *   - the target pages themselves (the user named them), and
 *   - the geometry measured on them (gridmarks), which is the most reliable
 *     positional evidence the project has.
 */
export function rankScope(input: RankInput): ReviewScope {
  const caps = RFI_REVIEW_DEPTHS[input.depth];
  const allowed = new Set<string>(SCOPE_KINDS);
  const targetPageIds = new Set(input.sides.flatMap((s) => s.pages.map((p) => p.pageId)));

  const score = new Map<string, number>();
  for (const list of input.hitLists) {
    list.forEach((id, rank) => score.set(id, (score.get(id) ?? 0) + 1 / (RRF_K + rank + 1)));
  }

  const mustKeep = input.targetChunks.filter((c) => c.kind === "gridmarks");
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
    ...mustKeep.map((c) => ({ ...c, score: score.get(c.chunkId) ?? 0 })),
    ...ranked.slice(0, room).map(({ c, s }) => ({ ...c, score: s })),
  ];

  // Pages: every target page, then the pages the chosen chunks sit on.
  const pageOrder: { page: CandidatePage; role: string; best: number }[] = [];
  const seen = new Set<string>();
  input.sides.forEach((side, i) => {
    for (const page of side.pages) {
      if (seen.has(page.pageId)) continue;
      seen.add(page.pageId);
      pageOrder.push({ page, role: input.sides.length > 1 ? `side:${i}` : "target", best: Infinity });
    }
  });
  const bestByPage = new Map<string, number>();
  for (const c of chosen) bestByPage.set(c.pageId, Math.max(bestByPage.get(c.pageId) ?? 0, c.score));
  const related = [...bestByPage.entries()]
    .filter(([id]) => !seen.has(id) && input.pages.has(id))
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  for (const [id, best] of related) pageOrder.push({ page: input.pages.get(id)!, role: "related", best });

  let visualLeft = caps.visualPages;
  const pages: ScopePage[] = pageOrder.map(({ page, role }) => {
    // Target pages are always rendered; related pages while room remains.
    const visual = role !== "related" ? true : visualLeft > 0;
    if (visual) visualLeft--;
    const crops = visual ? cropAnchors(chosen.filter((c) => c.pageId === page.pageId), caps.cropsPerPage) : [];
    return { ...page, role, visual, crops };
  });

  return {
    sides: input.sides.map((s) => ({ label: s.label, pageIds: s.pages.map((p) => p.pageId) })),
    pages,
    chunks: chosen,
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
 * which chunks — so a re-plan that changes any of it is a different hash, and
 * `start` can prove the scope it queues is the one on the screen.
 */
export function scopeHash(scope: ReviewScope, checkIds: readonly string[], depth: string): string {
  const body = JSON.stringify({
    checks: [...checkIds].sort(),
    depth,
    pages: scope.pages.map((p) => `${p.documentId}:${p.pageNumber}:${p.role}:${p.visual ? 1 : 0}`).sort(),
    crops: scope.pages.flatMap((p) => p.crops.map((c) => c.chunkId)).sort(),
    chunks: scope.chunks.map((c) => c.chunkId).sort(),
  });
  return createHash("sha256").update(body).digest("hex");
}

// --- Estimate ---------------------------------------------------------------------

/** Rough per-image input tokens: ~1.15 megapixels on Claude (~1,600), and the
 * fixed per-part budget Gemini's ultra_high media resolution spends is the
 * same order. An estimate, not a quote. */
export const IMAGE_TOKENS = 1600;
const SYSTEM_TOKENS = 1500;
const PER_CHUNK_OVERHEAD = 40;
const OUTPUT = { discovery: 3000, reasoning: 1500, verification: 1500 } as const;

/**
 * What a plan will roughly cost, from the stored chunk token counts. Three
 * calls: discovery sees everything, reasoning sees the observations (text
 * only), verification re-sees about half — only the evidence the proposed
 * candidates cite. Thinking is NOT priced, the same choice summaryEstimate
 * makes: how much a model reasons is its own decision, and a number invented
 * here would be the one line with nothing behind it. The screen says so.
 */
export function estimateReview(
  scope: ReviewScope,
  model: string,
  costOf: (row: { model: string; inputTokens: number; outputTokens: number; cacheReadTokens: number; cacheWriteTokens: number }) => number,
  depth: RfiReviewDepth = "standard",
): RfiReviewEstimateDto {
  const chunkTokens = scope.chunks.reduce((sum, c) => sum + c.tokenCount + PER_CHUNK_OVERHEAD, 0);
  const visualPages = scope.pages.filter((p) => p.visual);
  // Side-by-side pairs exist only where two rendered sheets line up, which the
  // worker learns from the PDFs; priced at the most it may send, so the quote
  // is an upper bound rather than a surprise.
  const pairImages = visualPages.length >= 2 ? 2 * RFI_REVIEW_DEPTHS[depth].pairWindows : 0;
  const imageParts = visualPages.reduce((sum, p) => sum + 1 + p.crops.length, 0) + pairImages;
  const imageTokens = imageParts * IMAGE_TOKENS;
  const discovery = SYSTEM_TOKENS + chunkTokens + imageTokens;
  const reasoning = SYSTEM_TOKENS + OUTPUT.discovery + scope.chunks.length * 15;
  const verification = SYSTEM_TOKENS + OUTPUT.reasoning + Math.round((chunkTokens + imageTokens) / 2);
  const inputTokens = discovery + reasoning + verification;
  const outputTokens = OUTPUT.discovery + OUTPUT.reasoning + OUTPUT.verification;
  return {
    modelCalls: 3,
    inputTokens,
    outputTokens,
    imageParts,
    costUsd: costOf({ model, inputTokens, outputTokens, cacheReadTokens: 0, cacheWriteTokens: 0 }),
    model,
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
 * A document revised since planning (superseded), taken out of RFI analysis,
 * or re-ingested (its chunk ids re-minted) means the approved evidence is not
 * what the project now says — analysing it would be analysing old drawings.
 */
export function staleReason(
  scope: ReviewScope,
  live: { documentIds: Set<string>; chunkIds: Set<string> },
): string | null {
  const docs = new Set(scope.pages.map((p) => p.documentId));
  for (const id of docs) {
    if (!live.documentIds.has(id)) {
      return "a document in this plan was revised, removed or excluded from RFI analysis since it was planned";
    }
  }
  const missing = scope.chunks.filter((c) => !live.chunkIds.has(c.chunkId)).length;
  if (missing) return `${missing} piece(s) of evidence in this plan were re-processed since it was planned`;
  return null;
}
