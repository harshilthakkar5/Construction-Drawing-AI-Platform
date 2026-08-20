import { afterEach, describe, expect, it } from "vitest";
import {
  BATCH_MIN_PAGES,
  MAX_OUTPUT_TOKENS,
  SECTION_SIZE,
  TYPICAL_OUTPUT_TOKENS,
  estimateProjectRollup,
  estimateSummaryRun,
} from "./summaryEstimate.js";

const savedEnv = { ...process.env };
afterEach(() => {
  process.env = { ...savedEnv };
});

/**
 * These numbers are shown to a user in a confirmation dialog before they spend
 * money, so the call *structure* has to match summarize.run_portion exactly.
 * The output-length constant is an estimate; the call counts are not.
 */
const page = (tokens: number) => tokens;

describe("estimateSummaryRun — call structure", () => {
  it("a one-page discipline is one page call plus one rollup", () => {
    const result = estimateSummaryRun({ pageTokens: [page(3900)], reusedPages: 0 });
    expect(result.pageCalls).toBe(1);
    expect(result.sectionCalls).toBe(0); // section tier skipped below SECTION_SIZE
    expect(result.portionCalls).toBe(1);
    expect(result.totalCalls).toBe(2);
  });

  it("keeps the section tier once a discipline outgrows one section", () => {
    const result = estimateSummaryRun({
      pageTokens: Array.from({ length: SECTION_SIZE + 1 }, () => page(2000)),
      reusedPages: 0,
    });
    expect(result.sectionCalls).toBe(2); // 11 pages → two groups
    expect(result.totalCalls).toBe(SECTION_SIZE + 1 + 2 + 1);
  });

  it("skips sections exactly at the boundary, matching the worker", () => {
    const atBoundary = estimateSummaryRun({
      pageTokens: Array.from({ length: SECTION_SIZE }, () => page(1000)),
      reusedPages: 0,
    });
    expect(atBoundary.sectionCalls).toBe(0);
  });

  it("counts reused page summaries toward the section decision but not the cost", () => {
    const result = estimateSummaryRun({ pageTokens: [], reusedPages: SECTION_SIZE + 1 });
    expect(result.pageCalls).toBe(0); // nothing to re-summarize
    expect(result.sectionCalls).toBe(2); // but they still have to be rolled up
    expect(result.portionCalls).toBe(1);
  });

  it("an already-summarized small discipline costs one rollup", () => {
    const result = estimateSummaryRun({ pageTokens: [], reusedPages: 3 });
    expect(result.totalCalls).toBe(1);
    expect(result.inputTokens).toBeLessThan(6000);
  });

  it("has nothing to do for an empty discipline", () => {
    const result = estimateSummaryRun({ pageTokens: [], reusedPages: 0 });
    expect(result.totalCalls).toBe(0);
    expect(result.costUsd).toBe(0);
  });
});

describe("estimateSummaryRun — tokens and cost", () => {
  it("charges the page's own chunk tokens as input", () => {
    const small = estimateSummaryRun({ pageTokens: [page(1000)], reusedPages: 0 });
    const large = estimateSummaryRun({ pageTokens: [page(9000)], reusedPages: 0 });
    expect(large.inputTokens - small.inputTokens).toBe(8000);
    expect(large.costUsd).toBeGreaterThan(small.costUsd);
  });

  it("never claims an output longer than the cap allows", () => {
    const result = estimateSummaryRun({ pageTokens: [page(5000)], reusedPages: 0 });
    expect(result.outputTokens).toBeLessThanOrEqual(result.totalCalls * MAX_OUTPUT_TOKENS);
    expect(TYPICAL_OUTPUT_TOKENS).toBeLessThan(MAX_OUTPUT_TOKENS);
  });

  it("prices at the Sonnet rate ($3/M in, $15/M out) by default", () => {
    // One page call (1000 chunk tokens + scaffolding) + one portion rollup.
    const result = estimateSummaryRun({ pageTokens: [page(1000)], reusedPages: 0 });
    const expected = (result.inputTokens * 3 + result.outputTokens * 15) / 1_000_000;
    expect(result.costUsd).toBeCloseTo(expected, 10);
  });

  it("scales roughly linearly with page count", () => {
    const one = estimateSummaryRun({ pageTokens: [page(2000)], reusedPages: 0 });
    const five = estimateSummaryRun({
      pageTokens: Array.from({ length: 5 }, () => page(2000)),
      reusedPages: 0,
    });
    expect(five.costUsd).toBeGreaterThan(one.costUsd * 3);
    expect(five.costUsd).toBeLessThan(one.costUsd * 6);
  });
});

describe("estimateProjectRollup", () => {
  it("is a single call over the existing portion summaries", () => {
    const result = estimateProjectRollup(4);
    expect(result.totalCalls).toBe(1);
    expect(result.pageCalls).toBe(0);
    expect(result.inputTokens).toBeGreaterThan(4 * TYPICAL_OUTPUT_TOKENS);
  });

  it("costs more with more disciplines to combine", () => {
    expect(estimateProjectRollup(8).costUsd).toBeGreaterThan(
      estimateProjectRollup(2).costUsd,
    );
  });
});


describe("the quote follows SUMMARY_PROVIDER", () => {
  /**
   * The bug this pins: the estimate hardcoded claude-sonnet-5, so a project
   * running SUMMARY_PROVIDER=gemini was shown Claude's model name and Claude's
   * prices for work Gemini was about to do.
   */
  const oneDiscipline = () => estimateSummaryRun({ pageTokens: [3900, 2100], reusedPages: 0 });

  it("names the model that will actually run", () => {
    process.env.SUMMARY_PROVIDER = "gemini";
    expect(oneDiscipline().model).toBe("gemini-2.5-pro");
    process.env.SUMMARY_PROVIDER = "claude";
    expect(oneDiscipline().model).toBe("claude-sonnet-5");
  });

  it("prices Gemini at Gemini's rate, not Sonnet's", () => {
    process.env.SUMMARY_PROVIDER = "claude";
    const claude = oneDiscipline().costUsd;
    process.env.SUMMARY_PROVIDER = "gemini";
    const gemini = oneDiscipline().costUsd;
    expect(gemini).toBeGreaterThan(0);
    expect(gemini).not.toBeCloseTo(claude, 10);
  });

  it("honours an explicit model override", () => {
    process.env.SUMMARY_PROVIDER = "claude";
    process.env.SUMMARY_MODEL = "claude-opus-5";
    expect(oneDiscipline().model).toBe("claude-opus-5");
  });

  it("the project rollup follows the provider too", () => {
    process.env.SUMMARY_PROVIDER = "gemini";
    expect(estimateProjectRollup(3).model).toBe("gemini-2.5-pro");
  });

  it("a typo falls back to claude rather than quoting nothing", () => {
    process.env.SUMMARY_PROVIDER = "gpt4";
    expect(oneDiscipline().model).toBe("claude-sonnet-5");
  });
});

describe("batch discount", () => {
  const pages = (n: number) => Array.from({ length: n }, () => 2000);

  it("halves the page tier when batching is on", () => {
    process.env.SUMMARY_USE_BATCH = "false";
    const sequential = estimateSummaryRun({ pageTokens: pages(8), reusedPages: 0 });
    process.env.SUMMARY_USE_BATCH = "true";
    const batched = estimateSummaryRun({ pageTokens: pages(8), reusedPages: 0 });

    expect(batched.batched).toBe(true);
    expect(sequential.batched).toBe(false);
    expect(batched.costUsd).toBeLessThan(sequential.costUsd);
    // Page tier halves; the portion rollup is untouched, so the total falls by
    // less than half.
    expect(batched.costUsd).toBeGreaterThan(sequential.costUsd / 2);
  });

  it("does not discount a run too small for the worker to batch", () => {
    process.env.SUMMARY_USE_BATCH = "true";
    const tooFew = estimateSummaryRun({
      pageTokens: pages(BATCH_MIN_PAGES - 1),
      reusedPages: 0,
    });
    expect(tooFew.batched).toBe(false);
  });

  it("never discounts the project rollup — one call is never a batch", () => {
    process.env.SUMMARY_USE_BATCH = "true";
    expect(estimateProjectRollup(5).batched).toBe(false);
  });

  it("token counts are unchanged by batching — only the price moves", () => {
    process.env.SUMMARY_USE_BATCH = "false";
    const sequential = estimateSummaryRun({ pageTokens: pages(8), reusedPages: 0 });
    process.env.SUMMARY_USE_BATCH = "true";
    const batched = estimateSummaryRun({ pageTokens: pages(8), reusedPages: 0 });
    expect(batched.inputTokens).toBe(sequential.inputTokens);
    expect(batched.outputTokens).toBe(sequential.outputTokens);
    expect(batched.totalCalls).toBe(sequential.totalCalls);
  });
});
