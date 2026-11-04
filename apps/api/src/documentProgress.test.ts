import { describe, expect, it } from "vitest";
import { classifyProblem, documentPercent, documentStageText, problemText, summaryProgressView } from "@cdip/shared";

const at = "2026-11-04T00:00:00Z";

describe("document progress words", () => {
  it("fills the bar within each stage's slice", () => {
    expect(documentPercent(null)).toBe(0);
    expect(documentPercent({ stage: "download", at })).toBe(0);
    expect(documentPercent({ stage: "extract", done: 57, total: 114, at })).toBe(43);
    expect(documentPercent({ stage: "extract", done: 114, total: 114, at })).toBe(80);
    expect(documentPercent({ stage: "finalize", done: 1, total: 1, at })).toBe(100);
  });

  it("says what is happening in plain words", () => {
    expect(documentStageText({ stage: "extract", done: 41, total: 114, ocr: 3, at })).toBe(
      "Reading pages: 41 of 114 (3 read from drawn text)",
    );
    expect(documentStageText({ stage: "embed", at })).toBe("Making the drawings searchable…");
  });

  it("never shows a raw code, even one this client does not know", () => {
    expect(problemText("ai_credits").title).toMatch(/run out of credit/);
    expect(problemText("something_new")).toEqual(problemText("unknown"));
  });
});

describe("job problems from raw error text", () => {
  it("reads the errors summaries and reviews store as text", () => {
    expect(classifyProblem("Error code: 400 - Your credit balance is too low")).toBe("ai_credits");
    expect(classifyProblem("no ANTHROPIC_API_KEY")).toBe("ai_key");
    expect(classifyProblem("summaries disabled")).toBe("disabled");
    expect(classifyProblem("no pages with extracted text")).toBe("nothing_to_read");
    expect(classifyProblem("the portion rollup produced nothing citable")).toBe("ai_reply");
    expect(classifyProblem("the worker stopped responding during this review")).toBe("worker_stopped");
    expect(classifyProblem("Error code: 529 overloaded_error")).toBe("ai_busy");
    expect(classifyProblem("something odd")).toBe("unknown");
    expect(classifyProblem(null)).toBe("unknown");
  });
});

describe("summary progress", () => {
  it("counts pages, then sections, then the rollup", () => {
    expect(summaryProgressView(null)).toEqual({ percent: 0, text: "Getting started…" });
    expect(summaryProgressView({ stage: "pages", done: 10, total: 40, at })).toEqual({
      percent: 20,
      text: "Summarising each page: 10 of 40",
    });
    expect(summaryProgressView({ stage: "rollup", at }).text).toBe("Writing the discipline summary…");
  });
});
