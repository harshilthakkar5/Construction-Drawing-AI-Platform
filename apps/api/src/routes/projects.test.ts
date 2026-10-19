import { describe, expect, it } from "vitest";
import { RFI_SETTINGS_DEFAULTS, rfiSettingsOf } from "@cdip/shared";
import { updateProjectSchema } from "./projects.js";

/** Project Settings tab: RFI settings saved on the project, refused when a run would refuse them. */
describe("project RFI settings", () => {
  const valid = {
    review: { provider: "gemini", model: "gemini-3.8-flash", depth: "deep", thinking: "low", maxInputTokens: 60_000, maxThinkingTokens: null },
    fullScan: { provider: null, model: null, useBatch: false, budgetUsd: 4 },
  };

  it("accepts settings a run would accept, and null to reset them", () => {
    expect(updateProjectSchema.parse({ rfiSettings: valid }).rfiSettings).toEqual(valid);
    expect(updateProjectSchema.parse({ rfiSettings: null }).rfiSettings).toBeNull();
  });

  it("refuses limits outside a run's bounds, unknown fields and an empty update", () => {
    const bad = (review: object) => updateProjectSchema.safeParse({ rfiSettings: { ...valid, review: { ...valid.review, ...review } } }).success;
    expect(bad({ maxInputTokens: 10 })).toBe(false);
    expect(bad({ depth: "extreme" })).toBe(false);
    expect(bad({ provider: "openai" })).toBe(false);
    expect(bad({ extra: 1 })).toBe(false);
    expect(updateProjectSchema.safeParse({}).success).toBe(false);
  });

  it("reads a partly saved row over the defaults", () => {
    expect(rfiSettingsOf(null)).toEqual(RFI_SETTINGS_DEFAULTS);
    const s = rfiSettingsOf({ review: { depth: "quick" } });
    expect(s.review).toEqual({ ...RFI_SETTINGS_DEFAULTS.review, depth: "quick" });
    expect(s.fullScan).toEqual(RFI_SETTINGS_DEFAULTS.fullScan);
  });
});
