import { describe, expect, it } from "vitest";
import { chosenAfter, processingSpend } from "./disciplineProcessingRules.js";

describe("chosenAfter", () => {
  it("adds what was asked and keeps what was chosen before", () => {
    expect(chosenAfter(["structural"], ["mechanical"], [], false)).toEqual(["mechanical", "structural"]);
  });
  it("never removes a discipline", () => {
    expect(chosenAfter(["general", "structural"], [], ["electrical"], false)).toEqual(["general", "structural"]);
  });
  it("all = every discipline with pages, plus unsorted pages", () => {
    expect(chosenAfter(["structural"], [], ["electrical", "structural"], true)).toEqual([
      "electrical",
      "structural",
      "unsorted",
    ]);
  });
});

describe("processingSpend", () => {
  const row = (stage: string, model: string, inputTokens: number) => ({
    stage, model, inputTokens, outputTokens: 0, cacheReadTokens: 0, cacheWriteTokens: 0,
  });
  it("adds up each discipline's processing calls and nothing else", () => {
    const spend = processingSpend(
      [
        row("process:structural", "gemini-embedding-001", 1000),
        row("process:structural", "gemini-embedding-001-batch", 3000),
        row("process:mechanical", "gemini-embedding-001", 500),
        row("discovery", "claude-sonnet-5", 99_999), // a review's call, not processing
      ],
      (r) => r.inputTokens / 1000,
    );
    expect(spend.get("structural")).toEqual({ costUsd: 4, tokens: 4000 });
    expect(spend.get("mechanical")).toEqual({ costUsd: 0.5, tokens: 500 });
    expect(spend.has("discovery")).toBe(false);
  });
});
