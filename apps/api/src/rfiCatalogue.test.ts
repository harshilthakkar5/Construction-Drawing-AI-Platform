import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import {
  RFI_REVIEW_CATALOGUE_VERSION,
  RFI_REVIEW_CHECKS,
  RFI_REVIEW_CHECK_IDS,
  reviewThinkingCapability,
  type RfiReviewProvider,
} from "@cdip/shared";

/**
 * The review catalogue against the golden transcription of the 16 original
 * questions. A question dropped, renumbered, reworded or "tidied" fails here —
 * the verbatim strings are the specification the client wrote, spelling and all.
 */
const fixture = (name: string) =>
  JSON.parse(readFileSync(new URL(`../../../packages/shared/fixtures/${name}`, import.meta.url), "utf8"));

describe("the RFI review catalogue", () => {
  const golden = fixture("rfi-original-questions.json").questions as {
    id: string;
    section: string;
    number: number;
    question: string;
  }[];

  it("carries exactly the 16 original questions, in order, verbatim", () => {
    expect(golden).toHaveLength(16);
    expect(RFI_REVIEW_CHECKS.map((c) => c.id)).toEqual(golden.map((q) => q.id));
    for (const [i, check] of RFI_REVIEW_CHECKS.entries()) {
      expect(check.originalQuestion).toBe(golden[i]!.question);
      expect(check.sourceSection).toBe(golden[i]!.section);
      expect(check.sourceQuestionNumber).toBe(golden[i]!.number);
    }
  });

  it("has unique ids and 2 + 4 + 4 + 3 + 3 objectives by section", () => {
    expect(new Set(RFI_REVIEW_CHECK_IDS).size).toBe(16);
    const bySection: Record<string, number> = {};
    for (const c of RFI_REVIEW_CHECKS) bySection[c.sourceSection] = (bySection[c.sourceSection] ?? 0) + 1;
    expect(bySection).toEqual({ General: 2, Foundations: 4, Floor: 4, "Columns & Shear walls": 3, Beams: 3 });
  });

  it("gives every objective what routing, prompts and the UI need", () => {
    for (const c of RFI_REVIEW_CHECKS) {
      expect(c.label.length).toBeGreaterThan(3);
      expect(c.requiredObservations.length).toBeGreaterThan(0);
      expect(c.query.length).toBeGreaterThan(3);
      expect(c.comparisonRules.length).toBeGreaterThan(10);
      expect(c.candidateRules.length).toBeGreaterThan(10);
      expect(c.applicability.always || c.applicability.keywords.length > 0).toBe(true);
    }
  });

  it("is versioned", () => {
    expect(RFI_REVIEW_CATALOGUE_VERSION).toMatch(/^\d{4}-\d{2}-\d{2}\.\d+$/);
  });
});

describe("reviewThinkingCapability", () => {
  const cases = fixture("thinking-capability.json").cases as { provider: RfiReviewProvider; model: string; budget: boolean }[];
  it.each(cases)("$provider $model takes a numeric budget: $budget", ({ provider, model, budget }) => {
    const cap = reviewThinkingCapability(provider, model);
    expect(cap.budget).toBe(budget);
    expect(cap.effort).toBe(true);
  });
});
