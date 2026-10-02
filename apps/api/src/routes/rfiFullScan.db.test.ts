import { randomUUID } from "node:crypto";
import type { AddressInfo } from "node:net";
import type { Server } from "node:http";
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Full AI scan routes against a real, migrated database
 * (RFI_TEST_DATABASE_URL), queue stubbed. What only a request shows: one start
 * per click however many arrive, a budget in dollars becomes a token ceiling,
 * a resume never lowers the ceiling below what is spent, and another
 * project's scan cannot be read or started.
 */
const url = process.env.RFI_TEST_DATABASE_URL;

const added: { name: string; data: unknown }[] = [];
vi.mock("../queues.js", () => ({
  rfiFullScanQueue: { add: vi.fn(async (name: string, data: unknown) => (added.push({ name, data }), { id: "j" })) },
}));
vi.mock("../rateLimit.js", () => ({ summaryLimiter: (_req: unknown, _res: unknown, next: () => void) => next() }));

describe.skipIf(!url)("full scan routes against a real database", () => {
  let server: Server;
  let base: string;
  let prisma: typeof import("../db.js")["prisma"];
  const projectId = randomUUID();
  const otherProject = randomUUID();
  const userId = randomUUID();

  const post = (path: string, body: unknown = {}, headers: Record<string, string> = {}) =>
    fetch(`${base}${path}`, { method: "POST", headers: { "content-type": "application/json", ...headers }, body: JSON.stringify(body) });

  async function plannedScan(project = projectId, tiles = 3): Promise<string> {
    const id = randomUUID();
    await prisma.rfiFullScan.create({
      data: {
        id,
        projectId: project,
        createdById: userId,
        status: "planned",
        estimate: {
          calls: tiles, images: 2 * tiles, inputTokens: 5500 * tiles, outputTokens: 700 * tiles,
          verifyCalls: { low: 1, high: 2 }, verifyInputTokens: { low: 5600, high: 11200 }, verifyOutputTokens: { low: 600, high: 1200 },
        },
        pairs: [],
      },
    });
    for (let t = 0; t < tiles; t++) {
      await prisma.rfiFullScanTile.create({ data: { scanId: id, pairIndex: 0, tileIndex: t, windows: {} } });
    }
    return id;
  }

  beforeAll(async () => {
    process.env.DATABASE_URL = url;
    process.env.REDIS_URL ??= "redis://127.0.0.1:1";
    process.env.QDRANT_URL ??= "http://127.0.0.1:1";
    process.env.ANTHROPIC_API_KEY = "test-key";
    delete process.env.RFI_PROVIDER;
    ({ prisma } = await import("../db.js"));
    const express = (await import("express")).default;
    const { rfiFullScanRouter } = await import("./rfiFullScan.js");
    const app = express();
    app.use(express.json());
    app.use((req, _res, next) => {
      (req as unknown as { user: { id: string } }).user = { id: userId };
      next();
    });
    app.use("/projects/:projectId/rfis/full-scans", rfiFullScanRouter);
    app.use((err: Error & { code?: string }, _req: unknown, res: { status: (n: number) => { json: (b: unknown) => void } }, _next: unknown) => {
      res.status(err.name === "ZodError" ? 400 : err.code === "P2025" ? 404 : 500).json({ error: err.message });
    });
    server = app.listen(0);
    base = `http://127.0.0.1:${(server.address() as AddressInfo).port}/projects/${projectId}/rfis/full-scans`;
    await prisma.$executeRawUnsafe(`INSERT INTO users (id, email, name, "passwordHash") VALUES ($1, $2, 'fs test', 'x')`, userId, `${userId}@test.invalid`);
    for (const id of [projectId, otherProject]) await prisma.$executeRawUnsafe(`INSERT INTO projects (id, name) VALUES ($1, 'fs')`, id);
  });

  beforeEach(async () => {
    added.length = 0;
    delete process.env.RFI_FULL_SCAN;
    await prisma.rfiFullScan.deleteMany({ where: { projectId } });
  });

  afterAll(async () => {
    server?.close();
    if (!prisma) return;
    await prisma.$executeRawUnsafe(`DELETE FROM projects WHERE id = ANY($1::text[])`, [projectId, otherProject]);
    await prisma.$executeRawUnsafe(`DELETE FROM users WHERE id = $1`, userId);
    await prisma.$disconnect();
  });

  it("plans with no model call, and only one scan at a time", async () => {
    const first = await post("");
    expect(first.status).toBe(202);
    const scan = await first.json();
    expect(scan.status).toBe("planning");
    expect(added).toEqual([{ name: "plan", data: { scanId: scan.id, mode: "plan" } }]);
    expect((await post("")).status).toBe(409);
    const list = await (await fetch(base)).json();
    expect(list.availability).toBe("beta");
    expect(list.scans.map((s: { id: string }) => s.id)).toEqual([scan.id]);
  });

  it("is hidden when turned off", async () => {
    process.env.RFI_FULL_SCAN = "off";
    expect((await fetch(base)).status).toBe(404);
    expect((await post("")).status).toBe(404);
  });

  it("starts once per click, turning a dollar budget into a token ceiling", async () => {
    const id = await plannedScan();
    const headers = { "Idempotency-Key": "click-0001" };
    const responses = await Promise.all([1, 2, 3].map(() => post(`/${id}/start`, { budgetUsd: 2, useBatch: true }, headers)));
    expect(responses.map((r) => r.status).sort()).toEqual([200, 200, 202].sort());
    expect(added.filter((a) => a.name === "run")).toHaveLength(1);
    const scan = await (await fetch(`${base}/${id}`)).json();
    expect(scan.status).toBe("queued");
    expect(scan.limits.budgetUsd).toBe(2);
    expect(scan.limits.maxTotalTokens).toBeGreaterThan(100_000);
    expect(scan.estimate.costUsd.low).toBeGreaterThan(0);
    // A different click on an already-started scan is refused, not run twice.
    expect((await post(`/${id}/start`, { budgetUsd: 2 }, { "Idempotency-Key": "click-0002" })).status).toBe(409);
  });

  it("refuses to start without a ceiling, or with nothing to look at", async () => {
    const id = await plannedScan();
    expect((await post(`/${id}/start`, {})).status).toBe(400);
    const empty = await plannedScan(projectId, 0);
    expect((await post(`/${empty}/start`, { budgetUsd: 1 })).status).toBe(409);
    expect(added).toEqual([]);
  });

  it("resumes a stopped scan, never below what it has spent", async () => {
    const id = await plannedScan();
    await prisma.rfiFullScan.update({
      where: { id },
      data: { status: "partial", provider: "claude", model: "claude-sonnet-5", limits: { maxTotalTokens: 20_000, budgetUsd: null } },
    });
    await prisma.usageEvent.create({
      data: { projectId, kind: "rfi", model: "claude-sonnet-5", inputTokens: 18_000, outputTokens: 1_000, reviewRunId: id, stage: "discovery" },
    });
    expect((await post(`/${id}/resume`, { maxTotalTokens: 15_000 })).status).toBe(400);
    const ok = await post(`/${id}/resume`, { maxTotalTokens: 60_000 });
    expect(ok.status).toBe(202);
    const scan = await ok.json();
    expect(scan.status).toBe("queued");
    expect(scan.limits.maxTotalTokens).toBe(60_000);
    expect(scan.spent.inputTokens).toBe(18_000);
    expect(added.filter((a) => a.name === "run")).toHaveLength(1);
    // A finished scan cannot be resumed.
    await prisma.rfiFullScan.update({ where: { id }, data: { status: "ready" } });
    expect((await post(`/${id}/resume`)).status).toBe(409);
  });

  it("prices batch spend at half", async () => {
    const id = await plannedScan();
    for (const stage of ["discovery_batch", "verification"]) {
      await prisma.usageEvent.create({
        data: { projectId, kind: "rfi", model: "claude-sonnet-5", inputTokens: 1_000_000, outputTokens: 0, reviewRunId: id, stage },
      });
    }
    const scan = await (await fetch(`${base}/${id}`)).json();
    expect(scan.spent.costUsd).toBeCloseTo(2 * 0.5 + 2, 3); // $2 per million input on this model
  });

  it("cancels, and keeps another project's scan out of reach", async () => {
    const id = await plannedScan();
    expect((await post(`/${id}/cancel`)).status).toBe(200);
    expect((await post(`/${id}/cancel`)).status).toBe(409);
    const foreign = await plannedScan(otherProject);
    expect((await fetch(`${base}/${foreign}`)).status).toBe(404);
    expect((await post(`/${foreign}/start`, { budgetUsd: 1 })).status).toBe(404);
    expect(added).toEqual([]);
  });
});
