import { afterEach, describe, expect, it } from "vitest";
import {
  DEAD_RUN_MESSAGE,
  effectiveSummaryState,
  parseDetail,
  summaryRunIsDead,
} from "./summaryRunRules.js";

const now = new Date("2026-09-26T12:00:00Z");
const minutesAgo = (m: number) => new Date(now.getTime() - m * 60_000);

describe("summaryRunIsDead", () => {
  afterEach(() => {
    delete process.env.SUMMARY_STALE_MINUTES;
    delete process.env.SUMMARY_QUEUED_STALE_MINUTES;
  });

  it("keeps a running summary alive while its heartbeat is recent", () => {
    expect(
      summaryRunIsDead(
        { summaryStatus: "running", summaryRequestedAt: minutesAgo(300), summaryHeartbeatAt: minutesAgo(2) },
        now,
      ),
    ).toBe(false);
  });

  it("times a long run from its heartbeat, not from when it was pressed", () => {
    // Five hours in and still beating: a large discipline, not a dead one.
    expect(
      summaryRunIsDead(
        { summaryStatus: "running", summaryRequestedAt: minutesAgo(300), summaryHeartbeatAt: minutesAgo(1) },
        now,
      ),
    ).toBe(false);
  });

  it("declares a running summary dead once the heartbeat stops", () => {
    expect(
      summaryRunIsDead(
        { summaryStatus: "running", summaryRequestedAt: minutesAgo(30), summaryHeartbeatAt: minutesAgo(11) },
        now,
      ),
    ).toBe(true);
  });

  it("follows SUMMARY_STALE_MINUTES", () => {
    process.env.SUMMARY_STALE_MINUTES = "30";
    const state = {
      summaryStatus: "running" as const,
      summaryRequestedAt: minutesAgo(60),
      summaryHeartbeatAt: minutesAgo(20),
    };
    expect(summaryRunIsDead(state, now)).toBe(false);
    process.env.SUMMARY_STALE_MINUTES = "15";
    expect(summaryRunIsDead(state, now)).toBe(true);
  });

  it("releases a run from before heartbeats existed", () => {
    expect(
      summaryRunIsDead(
        { summaryStatus: "running", summaryRequestedAt: null, summaryHeartbeatAt: null },
        now,
      ),
    ).toBe(true);
  });

  it("gives a queued run hours, because it may be waiting for a worker slot", () => {
    const queued = { summaryStatus: "queued" as const, summaryHeartbeatAt: null };
    expect(summaryRunIsDead({ ...queued, summaryRequestedAt: minutesAgo(60) }, now)).toBe(false);
    expect(summaryRunIsDead({ ...queued, summaryRequestedAt: minutesAgo(7 * 60) }, now)).toBe(true);
  });

  it("never touches a finished or failed portion", () => {
    for (const summaryStatus of ["none", "ready", "failed", "stale"] as const) {
      expect(
        summaryRunIsDead({ summaryStatus, summaryRequestedAt: null, summaryHeartbeatAt: null }, now),
      ).toBe(false);
    }
  });
});

describe("effectiveSummaryState", () => {
  it("reports a dead run as failed, with what to do about it", () => {
    expect(
      effectiveSummaryState(
        {
          summaryStatus: "running",
          summaryRequestedAt: minutesAgo(40),
          summaryHeartbeatAt: minutesAgo(40),
          summaryError: null,
        },
        now,
      ),
    ).toEqual({ summaryStatus: "failed", summaryError: DEAD_RUN_MESSAGE });
  });

  it("passes a live one through untouched", () => {
    expect(
      effectiveSummaryState(
        {
          summaryStatus: "running",
          summaryRequestedAt: minutesAgo(3),
          summaryHeartbeatAt: minutesAgo(1),
          summaryError: null,
        },
        now,
      ),
    ).toEqual({ summaryStatus: "running", summaryError: null });
  });
});

describe("parseDetail", () => {
  it("accepts only the sizes the worker knows", () => {
    expect(parseDetail("full")).toBe("full");
    expect(parseDetail("huge")).toBeNull();
    expect(parseDetail(undefined)).toBeNull();
    expect(parseDetail(5)).toBeNull();
  });
});
