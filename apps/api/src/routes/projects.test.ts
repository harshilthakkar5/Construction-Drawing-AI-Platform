import { describe, expect, it } from "vitest";
import { RFI_SETTINGS_DEFAULTS, rfiSettingsOf } from "@cdip/shared";
import { updateProjectSchema } from "./projects.js";

/** Project Settings tab: RFI settings saved on the project, refused when a run would refuse them. */
describe("project RFI settings", () => {
  const valid = {
    review: { provider: "gemini", model: "gemini-3.8-flash", depth: "deep", thinking: "low", maxInputTokens: 60_000, maxThinkingTokens: null, useBatch: true },
    fullScan: { provider: null, model: null, useBatch: false, budgetUsd: 4 },
  };

  it("accepts settings a run would accept, and null to reset them", () => {
    expect(updateProjectSchema.parse({ rfiSettings: valid }).rfiSettings).toEqual(valid);
    expect(updateProjectSchema.parse({ rfiSettings: null }).rfiSettings).toBeNull();
    // Settings saved before batch mode existed carry no choice: direct, as they ran.
    const { useBatch: _, ...before } = valid.review;
    expect(updateProjectSchema.parse({ rfiSettings: { ...valid, review: before } }).rfiSettings!.review.useBatch).toBe(false);
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

  it("defaults both RFI runs to Claude Sonnet 5.5, and keeps a model saved as Server default", () => {
    const sonnet = { provider: "claude", model: "claude-sonnet-5-5" };
    expect(rfiSettingsOf(null).review).toMatchObject(sonnet);
    expect(rfiSettingsOf({ review: { depth: "quick" } }).fullScan).toMatchObject(sonnet);
    const server = rfiSettingsOf({ review: { provider: null, model: null }, fullScan: { provider: null, model: null } });
    expect(server.review).toMatchObject({ provider: null, model: null });
    expect(server.fullScan).toMatchObject({ provider: null, model: null });
    const gemini = { provider: "gemini" as const, model: "gemini-3.8-flash" };
    expect(rfiSettingsOf({ review: gemini }).review).toMatchObject(gemini);
  });
});
