import { describe, expect, it } from "vitest";
import { packageStale, STALE_PACKAGE_MS } from "./rfiPackageRules.js";

describe("packageStale", () => {
  const asked = new Date("2026-10-21T10:00:00Z");
  const later = new Date(asked.getTime() + 3 * STALE_PACKAGE_MS); // long after the request

  it("never calls a package waiting its turn dead, however long ago it was requested", () => {
    expect(packageStale("queued", asked, { state: "waiting", processedOn: null }, later)).toBe(false);
    expect(packageStale("queued", asked, { state: "prioritized", processedOn: null }, later)).toBe(false);
  });

  it("measures a running package from when a worker started it", () => {
    const started = later.getTime() - 60_000;
    expect(packageStale("running", asked, { state: "active", processedOn: started }, later)).toBe(false);
    expect(packageStale("running", asked, { state: "active", processedOn: started - STALE_PACKAGE_MS }, later)).toBe(true);
  });

  it("calls it dead when its job is gone or failed, and falls back to the request time when the queue cannot be asked", () => {
    expect(packageStale("queued", asked, null, later)).toBe(true);
    expect(packageStale("running", asked, { state: "failed", processedOn: 1 }, later)).toBe(true);
    expect(packageStale("queued", asked, undefined, later)).toBe(true);
    expect(packageStale("queued", asked, undefined, new Date(asked.getTime() + 1000))).toBe(false);
    expect(packageStale("ready", asked, null, later)).toBe(false);
  });
});
