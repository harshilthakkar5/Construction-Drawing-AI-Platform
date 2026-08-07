import { estimateCostUsd } from "./usage.js";

/**
 * What will pressing "Generate summary" actually cost?
 *
 * Summaries are the most expensive thing a user can trigger by hand, so the
 * button asks for confirmation first — and a confirmation dialog is only worth
 * showing if its numbers are real. These come from stored data, not guesswork:
 * `chunks.tokenCount` is the exact input the page tier will send, and the call
 * structure below mirrors summarize.run_portion exactly.
 *
 * What is estimated rather than known:
 *  - output length per call. The model decides; the cap is SUMMARY_MAX_TOKENS.
 *    TYPICAL_OUTPUT_TOKENS is calibrated from observed runs (~1350 on real
 *    sheets) and is the one number here that can be materially wrong.
 *  - prompt scaffolding, which is small and near-constant.
 *
 * Deliberately optimistic in one place: a page that already has a summary is
 * counted as free, exactly as the worker treats it. The worker additionally
 * re-summarizes a page whose cited chunks were replaced (a reprocessed page),
 * which this cannot see cheaply — so a re-run after reprocessing can cost more
 * than quoted. That is called out in the DTO as `reusedPageSummaries`.
 */

/** Mirrors summarize.SECTION_SIZE — sections group this many pages. */
export const SECTION_SIZE = 10;

/** Mirrors summarize.MAX_TOKENS (SUMMARY_MAX_TOKENS). */
export const MAX_OUTPUT_TOKENS = 2000;

/**
 * Typical completion length for one summary call, from observed runs. Every
 * tier produces the same {overview, items[]} shape with the same item cap, so
 * one constant covers all three.
 */
export const TYPICAL_OUTPUT_TOKENS = 1350;

/** Cached system prompt (~250) plus the per-call instruction wrapper. */
const SYSTEM_PROMPT_TOKENS = 250;
const PROMPT_OVERHEAD_TOKENS = 60;

/** Summaries run on Sonnet (workers/src/summarize.py SUMMARY_MODEL). */
export const SUMMARY_MODEL = "claude-sonnet-5";

export interface SummaryEstimateInput {
  /** Chunk tokens per page that still needs a page summary (one entry each). */
  pageTokens: number[];
  /** Pages whose summary already exists and will be reused — free. */
  reusedPages: number;
}

export interface SummaryEstimateResult {
  pageCalls: number;
  sectionCalls: number;
  portionCalls: number;
  totalCalls: number;
  inputTokens: number;
  outputTokens: number;
  costUsd: number;
}

/** Ceiling division without floating point surprises. */
const groups = (count: number, size: number) => Math.ceil(count / size);

export function estimateSummaryRun(input: SummaryEstimateInput): SummaryEstimateResult {
  const pageCalls = input.pageTokens.length;
  const totalPages = pageCalls + input.reusedPages;

  // Page tier: each call sends that page's chunks.
  let inputTokens = input.pageTokens.reduce(
    (sum, tokens) => sum + tokens + SYSTEM_PROMPT_TOKENS + PROMPT_OVERHEAD_TOKENS,
    0,
  );
  let outputTokens = pageCalls * TYPICAL_OUTPUT_TOKENS;

  // Section tier: skipped at or below SECTION_SIZE pages, where it would only
  // restate the page summaries (summarize.needs_section_tier).
  const sectionCalls = totalPages > SECTION_SIZE ? groups(totalPages, SECTION_SIZE) : 0;
  if (sectionCalls > 0) {
    // Each section reads the page summaries in its group.
    inputTokens +=
      totalPages * TYPICAL_OUTPUT_TOKENS +
      sectionCalls * (SYSTEM_PROMPT_TOKENS + PROMPT_OVERHEAD_TOKENS);
    outputTokens += sectionCalls * TYPICAL_OUTPUT_TOKENS;
  }

  // Portion tier: reads whichever level sits below it.
  const portionCalls = totalPages > 0 ? 1 : 0;
  if (portionCalls > 0) {
    const lowerCount = sectionCalls > 0 ? sectionCalls : totalPages;
    inputTokens +=
      lowerCount * TYPICAL_OUTPUT_TOKENS + SYSTEM_PROMPT_TOKENS + PROMPT_OVERHEAD_TOKENS;
    outputTokens += TYPICAL_OUTPUT_TOKENS;
  }

  return {
    pageCalls,
    sectionCalls,
    portionCalls,
    totalCalls: pageCalls + sectionCalls + portionCalls,
    inputTokens,
    outputTokens,
    costUsd: estimateCostUsd({
      model: SUMMARY_MODEL,
      inputTokens,
      outputTokens,
      cacheReadTokens: 0,
      cacheWriteTokens: 0,
    }),
  };
}

/**
 * The project rollup reads the portion summaries that already exist — one
 * call, no page work.
 */
export function estimateProjectRollup(portionSummaries: number): SummaryEstimateResult {
  const inputTokens =
    portionSummaries * TYPICAL_OUTPUT_TOKENS + SYSTEM_PROMPT_TOKENS + PROMPT_OVERHEAD_TOKENS;
  const outputTokens = TYPICAL_OUTPUT_TOKENS;
  return {
    pageCalls: 0,
    sectionCalls: 0,
    portionCalls: 1,
    totalCalls: 1,
    inputTokens,
    outputTokens,
    costUsd: estimateCostUsd({
      model: SUMMARY_MODEL,
      inputTokens,
      outputTokens,
      cacheReadTokens: 0,
      cacheWriteTokens: 0,
    }),
  };
}
