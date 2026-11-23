import { readFileSync } from "node:fs";
import { describe, expect, it, vi } from "vitest";

vi.mock("./redis.js", () => ({ redis: {} }));
vi.mock("./db.js", () => ({ prisma: {} }));

const { isAdmin } = await import("./auth.js");

describe("instance-wide operations are for named operators only", () => {
  it("matches ADMIN_EMAILS by address, ignoring case and spaces; empty means nobody", () => {
    expect(isAdmin("Ops@Example.com", " ops@example.com , lead@example.com")).toBe(true);
    expect(isAdmin("someone@example.com", "ops@example.com")).toBe(false);
    expect(isAdmin("ops@example.com", "")).toBe(false);
    expect(isAdmin("", " , ")).toBe(false);
  });

  it("the /queues router is mounted behind requireAdmin", () => {
    // Registration is open: a signed-in user alone could read and retry every project's jobs.
    const app = readFileSync(new URL("./app.ts", import.meta.url), "utf8").replace(/\/\/.*$/gm, "");
    expect(app).toContain('app.use("/queues", requireAdmin, queuesRouter)');
  });
});
