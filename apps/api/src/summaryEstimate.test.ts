import { describe, expect, it } from "vitest";
import {
  MAX_OUTPUT_TOKENS,
  SECTION_SIZE,
  TYPICAL_OUTPUT_TOKENS,
  estimateProjectRollup,
  estimateSummaryRun,
} from "./summaryEstimate.js";

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

  it("prices at the Sonnet rate ($3/M in, $15/M out)", () => {
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
