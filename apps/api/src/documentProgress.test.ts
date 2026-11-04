import { describe, expect, it } from "vitest";
import { documentPercent, documentProblemText, documentStageText } from "@cdip/shared";

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
    expect(documentProblemText("ai_credits").title).toMatch(/run out of credit/);
    expect(documentProblemText("something_new")).toEqual(documentProblemText("unknown"));
  });
});
