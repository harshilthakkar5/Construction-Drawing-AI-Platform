import { afterEach, describe, expect, it } from "vitest";
import {
  BATCH_MIN_PAGES,
  MAX_OUTPUT_TOKENS,
  SECTION_SIZE,
  TYPICAL_OUTPUT_TOKENS,
  defaultDetail,
  estimateProjectRollup,
  estimateSummaryRun,
  rollupOutputTokens,
  summaryThinking,
} from "./summaryEstimate.js";
import { rateFor } from "./usage.js";

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

  it("prices at the rate of the model it says will run", () => {
    // The rate is READ from RATES rather than restated here. It used to be
    // written out as $3/$15, which was Sonnet 4.6's price on a row labelled
    // claude-sonnet-5 — so the test agreed with the bug and kept agreeing with
    // it. A duplicated constant cannot catch its own original drifting.
    const result = estimateSummaryRun({ pageTokens: [page(1000)], reusedPages: 0 });
    const rate = rateFor(result.model);
    const expected =
      (result.inputTokens * rate.input + result.outputTokens * rate.output) / 1_000_000;
    expect(result.costUsd).toBeCloseTo(expected, 10);
    expect(result.costUsd).toBeGreaterThan(0);
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
    expect(oneDiscipline().model).toBe("models/gemini-3.1-pro-preview");
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
    expect(estimateProjectRollup(3).model).toBe("models/gemini-3.1-pro-preview");
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

describe("summary size", () => {
  const pages = (n: number) => Array.from({ length: n }, () => 900);

  it("prices standard exactly as before sizes existed", () => {
    const implicit = estimateSummaryRun({ pageTokens: pages(25), reusedPages: 0 });
    const standard = estimateSummaryRun({ pageTokens: pages(25), reusedPages: 0, detail: "standard" });
    expect(standard).toEqual(implicit);
    expect(rollupOutputTokens("standard")).toBe(TYPICAL_OUTPUT_TOKENS);
  });

  it("charges a bigger size in the rollups and never in the page tier", () => {
    const standard = estimateSummaryRun({ pageTokens: pages(25), reusedPages: 0, detail: "standard" });
    const full = estimateSummaryRun({ pageTokens: pages(25), reusedPages: 0, detail: "full" });
    expect(full.totalCalls).toBe(standard.totalCalls);
    expect(full.outputTokens).toBeGreaterThan(standard.outputTokens);
    expect(full.costUsd).toBeGreaterThan(standard.costUsd);
    // 25 pages = 3 sections + 1 portion rollup; only those 4 answers grow.
    const growth = rollupOutputTokens("full") - rollupOutputTokens("standard");
    expect(full.outputTokens - standard.outputTokens).toBe(4 * growth);
    expect(full.detail).toBe("full");
  });

  it("a brief size costs less", () => {
    const standard = estimateSummaryRun({ pageTokens: [], reusedPages: 30, detail: "standard" });
    const brief = estimateSummaryRun({ pageTokens: [], reusedPages: 30, detail: "brief" });
    expect(brief.costUsd).toBeLessThan(standard.costUsd);
  });

  it("scales the project rollup too", () => {
    expect(estimateProjectRollup(4, "detailed").outputTokens).toBeGreaterThan(
      estimateProjectRollup(4, "standard").outputTokens,
    );
  });

  it("uses SUMMARY_DETAIL when nothing is asked for, and ignores a typo", () => {
    process.env.SUMMARY_DETAIL = "detailed";
    expect(defaultDetail()).toBe("detailed");
    expect(estimateSummaryRun({ pageTokens: pages(3), reusedPages: 0 }).detail).toBe("detailed");
    process.env.SUMMARY_DETAIL = "enormous";
    expect(defaultDetail()).toBe("standard");
  });

  it("names SUMMARY_THINKING for the dialog and refuses a typo", () => {
    delete process.env.SUMMARY_THINKING;
    expect(summaryThinking()).toBeNull();
    process.env.SUMMARY_THINKING = "HIGH";
    expect(summaryThinking()).toBe("high");
    process.env.SUMMARY_THINKING = "disabled";
    expect(summaryThinking()).toBe("off");
    process.env.SUMMARY_THINKING = "maximum";
    expect(summaryThinking()).toBeNull();
  });
});
