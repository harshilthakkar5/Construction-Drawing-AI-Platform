import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import {
  BATCH_STAGE,
  STALE_FULL_SCAN_MS,
  budgetToTokens,
  canResume,
  fullScanAvailability,
  priceEstimate,
  scanIsActive,
  shownStatus,
  spentOf,
  toFullScanDto,
  type ScanRow,
  type WorkerEstimate,
} from "./rfiFullScanRules.js";
import { estimateCostUsd } from "./usage.js";

const ESTIMATE: WorkerEstimate = {
  calls: 100,
  images: 200,
  inputTokens: 550_000,
  outputTokens: 70_000,
  verifyCalls: { low: 5, high: 35 },
  verifyInputTokens: { low: 28_000, high: 196_000 },
  verifyOutputTokens: { low: 3_000, high: 21_000 },
};

const cost = (model: string, input: number, output: number) =>
  estimateCostUsd({ model, inputTokens: input, outputTokens: output, cacheReadTokens: 0, cacheWriteTokens: 0 });

describe("availability", () => {
  it("defaults to beta and accepts only the three values", () => {
    expect(fullScanAvailability(undefined)).toBe("beta");
    expect(fullScanAvailability("OFF")).toBe("off");
    expect(fullScanAvailability("on")).toBe("on");
    expect(fullScanAvailability("yes")).toBe("beta");
  });
});

describe("priceEstimate", () => {
  it("prices the worker's tokens on the chosen model, as a range over verification", () => {
    const priced = priceEstimate(ESTIMATE, "claude-sonnet-5", false)!;
    const discovery = cost("claude-sonnet-5", 550_000, 70_000);
    expect(priced.costUsd!.low).toBeCloseTo(discovery + cost("claude-sonnet-5", 28_000, 3_000), 3);
    expect(priced.costUsd!.high).toBeCloseTo(discovery + cost("claude-sonnet-5", 196_000, 21_000), 3);
    expect(priced.calls).toBe(100);
  });

  it("halves only the first look when it goes through the batch API", () => {
    const direct = priceEstimate(ESTIMATE, "claude-sonnet-5", false)!.costUsd!;
    const batch = priceEstimate(ESTIMATE, "claude-sonnet-5", true)!.costUsd!;
    const half = cost("claude-sonnet-5", 550_000, 70_000) / 2;
    expect(direct.low - batch.low).toBeCloseTo(half, 3);
    expect(direct.high - batch.high).toBeCloseTo(half, 3);
  });

  it("leaves an unknown price unknown", () => {
    expect(priceEstimate(ESTIMATE, "someone-elses-model", false)!.costUsd).toBeNull();
    expect(priceEstimate(null, "claude-sonnet-5", false)).toBeNull();
  });
});

describe("budgetToTokens", () => {
  it("never lets the ceiling cost more than the budget at the full rate", () => {
    const tokens = budgetToTokens(10, "claude-sonnet-5", ESTIMATE)!;
    const share = ESTIMATE.inputTokens / (ESTIMATE.inputTokens + ESTIMATE.outputTokens);
    const spend = cost("claude-sonnet-5", tokens * share, tokens * (1 - share));
    expect(spend).toBeLessThanOrEqual(10.0001);
    expect(spend).toBeGreaterThan(9.9);
  });

  it("refuses a dollar budget on a model with no known price", () => {
    expect(budgetToTokens(10, "someone-elses-model", ESTIMATE)).toBeNull();
    expect(budgetToTokens(0, "claude-sonnet-5", ESTIMATE)).toBeNull();
  });
});

describe("spentOf", () => {
  const row = (stage: string | null, input = 1_000_000, output = 100_000) => ({
    model: "claude-sonnet-5",
    stage,
    inputTokens: input,
    outputTokens: output,
    cacheReadTokens: 0,
    cacheWriteTokens: 0,
  });

  it("prices batch rows at half", () => {
    const full = cost("claude-sonnet-5", 1_000_000, 100_000);
    expect(spentOf([row("discovery")]).costUsd).toBeCloseTo(full, 3);
    expect(spentOf([row(BATCH_STAGE)]).costUsd).toBeCloseTo(full / 2, 3);
    expect(spentOf([row("discovery"), row("verification")]).inputTokens).toBe(2_000_000);
  });

  it("will not show a partial sum as the total", () => {
    const spent = spentOf([row("discovery"), { ...row("discovery"), model: "someone-elses-model" }]);
    expect(spent.costUsd).toBeNull();
    expect(spent.inputTokens).toBe(2_000_000);
  });
});

const NOW = new Date("2026-10-02T12:00:00Z");
const scan = (over: Partial<ScanRow> = {}): ScanRow => ({
  id: "s",
  status: "running",
  stage: "first look",
  progress: 40,
  provider: "claude",
  model: "claude-sonnet-5",
  useBatch: true,
  catalogue: null,
  pairs: [],
  skipped: [],
  estimate: ESTIMATE,
  limits: { maxTotalTokens: 1000, budgetUsd: 5 },
  notes: ["a", 3],
  findings: 0,
  error: null,
  heartbeatAt: new Date(NOW.getTime() - 60_000),
  createdAt: new Date(NOW.getTime() - 3_600_000),
  plannedAt: null,
  startedAt: null,
  completedAt: null,
  ...over,
});

describe("liveness", () => {
  it("keys on the heartbeat, not on how long the scan has run", () => {
    expect(scanIsActive(scan(), NOW)).toBe(true);
    expect(scanIsActive(scan({ heartbeatAt: new Date(NOW.getTime() - STALE_FULL_SCAN_MS - 1) }), NOW)).toBe(false);
    expect(scanIsActive(scan({ status: "ready" }), NOW)).toBe(false);
  });

  it("shows a dead run as failed, with how to carry on", () => {
    const dead = shownStatus(scan({ heartbeatAt: new Date(0) }), NOW);
    expect(dead.status).toBe("failed");
    expect(dead.error).toMatch(/resume/);
    expect(shownStatus(scan(), NOW).status).toBe("running");
  });

  it("offers a resume only for a scan that stopped with tiles", () => {
    expect(canResume("partial", { total: 5, done: 2 })).toBe(true);
    expect(canResume("failed", { total: 5, done: 0 })).toBe(true);
    expect(canResume("stale", { total: 5, done: 2 })).toBe(false);
    expect(canResume("ready", { total: 5, done: 5 })).toBe(false);
    expect(canResume("partial", { total: 0, done: 0 })).toBe(false);
  });
});

describe("toFullScanDto", () => {
  it("prices the estimate and keeps only string notes", () => {
    const dto = toFullScanDto(scan(), { total: 4, done: 1, failed: 0 }, spentOf([]), NOW);
    expect(dto.estimate!.costUsd).not.toBeNull();
    expect(dto.notes).toEqual(["a"]);
    expect(dto.limits).toEqual({ maxTotalTokens: 1000, budgetUsd: 5 });
  });
});

describe("the worker's estimate shape", () => {
  it("carries every field this module prices", () => {
    // fullscan_plan.estimate writes the dict the API reads back; a renamed key
    // would price nothing and say nothing.
    const source = readFileSync(new URL("../../../workers/src/fullscan_plan.py", import.meta.url), "utf8");
    const body = source.slice(source.indexOf("def estimate("), source.indexOf("def skipped_list("));
    for (const key of Object.keys(ESTIMATE)) expect(body).toContain(`"${key}"`);
  });

  it("names the batch stage the worker tags", () => {
    const source = readFileSync(new URL("../../../workers/src/fullscan_run.py", import.meta.url), "utf8");
    expect(source).toContain(`"${BATCH_STAGE}"`);
  });
});
