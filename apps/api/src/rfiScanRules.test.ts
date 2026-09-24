import { describe, expect, it } from "vitest";
import { meetsConfidence, scanIsActive, scanUsage, STALE_SCAN_MS, toScanDto } from "./rfiScanRules.js";

const NOW = new Date("2026-06-01T12:00:00Z");
const ago = (ms: number) => new Date(NOW.getTime() - ms);

describe("scanIsActive", () => {
  it("is false with no scan at all", () => {
    expect(scanIsActive(null, NOW)).toBe(false);
  });

  it("is true for a fresh queued or running scan", () => {
    expect(scanIsActive({ status: "queued", createdAt: ago(1000), startedAt: null }, NOW)).toBe(true);
    expect(scanIsActive({ status: "running", createdAt: ago(1000), startedAt: ago(500) }, NOW)).toBe(true);
  });

  it("is false once a scan finished, however recently", () => {
    expect(scanIsActive({ status: "completed", createdAt: ago(1), startedAt: ago(1) }, NOW)).toBe(false);
    expect(scanIsActive({ status: "failed", createdAt: ago(1), startedAt: ago(1) }, NOW)).toBe(false);
  });

  /**
   * A worker that died mid-scan leaves the row `running` forever. Without the
   * timeout the button stays disabled for the life of the project, which
   * looks exactly like a scan that is merely slow.
   */
  it("gives up on a scan stuck past the stale limit", () => {
    const stuck = { status: "running" as const, createdAt: ago(STALE_SCAN_MS * 2), startedAt: ago(STALE_SCAN_MS + 1) };
    expect(scanIsActive(stuck, NOW)).toBe(false);
  });

  it("measures from when the scan STARTED, not when it was queued", () => {
    // Queued long ago behind other work, started a minute ago: still live.
    const running = { status: "running" as const, createdAt: ago(STALE_SCAN_MS * 2), startedAt: ago(60_000) };
    expect(scanIsActive(running, NOW)).toBe(true);
  });
});

describe("meetsConfidence", () => {
  it("accepts at or above the bar", () => {
    expect(meetsConfidence("high", "high")).toBe(true);
    expect(meetsConfidence("high", "medium")).toBe(true);
    expect(meetsConfidence("medium", "medium")).toBe(true);
    expect(meetsConfidence("low", "low")).toBe(true);
  });

  it("refuses below it", () => {
    // "Accept all high" must never sweep in a medium finding.
    expect(meetsConfidence("medium", "high")).toBe(false);
    expect(meetsConfidence("low", "high")).toBe(false);
    expect(meetsConfidence("low", "medium")).toBe(false);
  });
});

describe("toScanDto", () => {
  const row = {
    id: "s",
    status: "completed",
    findings: 3,
    modelWorded: 2,
    byCheck: { dangling_reference: 1 },
    notes: ["one", 2, "three"],
    error: null,
    startedAt: NOW,
    finishedAt: NOW,
    createdAt: NOW,
  };

  it("keeps only string notes", () => {
    expect(toScanDto(row).notes).toEqual(["one", "three"]);
  });

  it("tolerates JSON columns that are not the expected shape", () => {
    const dto = toScanDto({ ...row, byCheck: [1, 2], notes: null });
    expect(dto.byCheck).toBeNull();
    expect(dto.notes).toEqual([]);
  });
});

describe("scanUsage", () => {
  const fromWorker = {
    provider: "gemini",
    model: "gemini-3.1-pro-preview",
    thinkingSetting: "minimal",
    thinkingSent: ["thinking_level=low"],
    thinkingAdjusted: true,
    calls: 1,
    failedCalls: 0,
    inputTokens: 2400,
    outputTokens: 3100,
    thinkingTokens: 2200,
    cacheReadTokens: 0,
    cacheWriteTokens: 0,
  };

  it("passes the worker's figures through and prices them", () => {
    const priced: unknown[] = [];
    const usage = scanUsage(fromWorker, (row) => {
      priced.push(row);
      return 0.25;
    });
    expect(usage).toEqual({ ...fromWorker, costUsd: 0.25 });
    expect(priced).toHaveLength(1);
  });

  it("keeps an unreported thinking count as null, not zero", () => {
    // Anthropic folds reasoning into output. Zero would claim it did not think.
    expect(scanUsage({ ...fromWorker, thinkingTokens: null }, () => 0)!.thinkingTokens).toBeNull();
  });

  it("costs nothing, without pricing anything, when no call was made", () => {
    const usage = scanUsage({ ...fromWorker, calls: 0 }, () => {
      throw new Error("should not price a scan that made no call");
    });
    expect(usage!.costUsd).toBe(0);
  });

  it("reads a malformed record as numbers it can trust, never a crash", () => {
    const usage = scanUsage(
      { model: 7, calls: "2", inputTokens: -5, outputTokens: Number.NaN, thinkingSent: "low" },
      () => 1,
    );
    expect(usage).toMatchObject({
      model: null,
      calls: 0,
      inputTokens: 0,
      outputTokens: 0,
      thinkingSent: [],
      thinkingAdjusted: false,
      costUsd: 0,
    });
  });

  it("is null for a scan with no record at all", () => {
    expect(scanUsage(null, () => 0)).toBeNull();
    expect(scanUsage([], () => 0)).toBeNull();
    expect(toScanDto({ ...baseRow(), usage: undefined }).usage).toBeNull();
  });
});

function baseRow() {
  return {
    id: "s",
    status: "completed",
    findings: 0,
    modelWorded: 0,
    byCheck: null,
    notes: [],
    error: null,
    startedAt: null,
    finishedAt: null,
    createdAt: NOW,
  };
}
