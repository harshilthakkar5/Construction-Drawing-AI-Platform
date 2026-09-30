import { randomUUID } from "node:crypto";
import type { AddressInfo } from "node:net";
import type { Server } from "node:http";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";

/**
 * The review ROUTES against a real, migrated database (RFI_TEST_DATABASE_URL),
 * with the queue and the rate limiter stubbed — what only a request can show:
 * a Start that is repeated, raced or retried enqueues exactly ONE job, a plan
 * whose drawings changed is refused, and the report is built from stored rows.
 */
const url = process.env.RFI_TEST_DATABASE_URL;

const added: unknown[] = [];
vi.mock("../queues.js", () => ({
  rfiReviewQueue: {
    add: vi.fn(async (_name: string, data: unknown) => {
      added.push(data);
      return { id: `job-${added.length}` };
    }),
  },
}));
vi.mock("../rateLimit.js", () => ({ summaryLimiter: (_req: unknown, _res: unknown, next: () => void) => next() }));

describe.skipIf(!url)("review routes against a real database", () => {
  let server: Server;
  let base: string;
  let prisma: typeof import("../db.js")["prisma"];
  const projectId = randomUUID();
  const userId = randomUUID();
  let documentId = "";

  beforeAll(async () => {
    process.env.DATABASE_URL = url;
    process.env.REDIS_URL ??= "redis://127.0.0.1:1";
    process.env.QDRANT_URL ??= "http://127.0.0.1:1";
    process.env.ANTHROPIC_API_KEY ??= "test-key";
    ({ prisma } = await import("../db.js"));
    const express = (await import("express")).default;
    const { rfiReviewRouter } = await import("./rfiReview.js");
    const app = express();
    app.use(express.json());
    app.use((req, _res, next) => {
      (req as unknown as { user: { id: string } }).user = { id: userId };
      next();
    });
    app.use("/projects/:projectId/rfis/reviews", rfiReviewRouter);
    app.use((err: Error, _req: unknown, res: { status: (n: number) => { json: (b: unknown) => void } }, _next: unknown) => {
      res.status(500).json({ error: err.message });
    });
    server = app.listen(0);
    base = `http://127.0.0.1:${(server.address() as AddressInfo).port}/projects/${projectId}/rfis/reviews`;

    await prisma.$executeRawUnsafe(`INSERT INTO users (id, email, name, "passwordHash") VALUES ($1, $2, 'route test', 'x')`, userId, `${userId}@test.invalid`);
    await prisma.$executeRawUnsafe(`INSERT INTO projects (id, name) VALUES ($1, 'review route test')`, projectId);
    documentId = randomUUID();
    await prisma.$executeRawUnsafe(
      `INSERT INTO documents (id, "projectId", filename, "spacesKey", pages, status) VALUES ($1, $2, 'set.pdf', 'k', 1, 'completed')`,
      documentId,
      projectId,
    );
    const pageId = randomUUID();
    await prisma.$executeRawUnsafe(
      `INSERT INTO pages (id, "documentId", "pageNumber", "combinedPageNumber", "sheetNumber") VALUES ($1, $2, 1, 1, 'S2.105')`,
      pageId,
      documentId,
    );
    await prisma.$executeRawUnsafe(
      `INSERT INTO chunks (id, "pageId", text, bbox, "tokenCount", kind)
       VALUES ($1, $2, 'COLUMN C-6 AT GRID 3/C', '{"x":1,"y":1,"width":10,"height":10}'::jsonb, 20, 'text')`,
      randomUUID(),
      pageId,
    );
  });

  afterAll(async () => {
    server?.close();
    if (!prisma) return;
    await prisma.$executeRawUnsafe(`DELETE FROM projects WHERE id = $1`, projectId);
    await prisma.$executeRawUnsafe(`DELETE FROM users WHERE id = $1`, userId);
    await prisma.$disconnect();
  });

  const post = (path: string, body: unknown = {}, headers: Record<string, string> = {}) =>
    fetch(`${base}${path}`, { method: "POST", headers: { "content-type": "application/json", ...headers }, body: JSON.stringify(body) });
  const plan = async () => {
    const res = await post("/plan", { target: { type: "sheet", value: "S2.105" }, checkMode: "custom", checkIds: ["G01"] });
    expect(res.status).toBe(201);
    return (await res.json()) as { id: string };
  };

  it("offers the catalogue and the models with what each accepts", async () => {
    const body = (await (await fetch(`${base}/options`)).json()) as { checks: unknown[]; options: { model: string; capability: { budget: boolean } }[] };
    expect(body.checks).toHaveLength(16);
    expect(body.options.find((o) => o.model === "claude-sonnet-5")!.capability.budget).toBe(false);
    expect(body.options.find((o) => o.model === "claude-haiku-4-5")!.capability.budget).toBe(true);
  });

  it("a repeated Start with one key enqueues once and returns the same run", async () => {
    const run = await plan();
    const before = added.length;
    const headers = { "Idempotency-Key": "click-0001-abcdef" };
    const [a, b] = await Promise.all([post(`/${run.id}/start`, {}, headers), post(`/${run.id}/start`, {}, headers)]);
    expect([a.status, b.status].sort()).toEqual([200, 202]);
    expect(added.length - before).toBe(1);
    const again = await post(`/${run.id}/start`, {}, headers);
    expect(again.status).toBe(200);
    expect(((await again.json()) as { id: string }).id).toBe(run.id);
    expect(added.length - before).toBe(1);
  });

  it("the same key cannot start a second review", async () => {
    const run = await plan();
    const res = await post(`/${run.id}/start`, {}, { "Idempotency-Key": "click-0001-abcdef" });
    expect(res.status).toBe(409);
  });

  it("refuses a plan whose drawing got a new revision", async () => {
    const run = await plan();
    await prisma.$executeRawUnsafe(`UPDATE documents SET revision = revision + 1 WHERE id = $1`, documentId);
    const res = await post(`/${run.id}/start`);
    expect(res.status).toBe(409);
    expect(((await res.json()) as { error: string }).error).toMatch(/new revision/);
    await prisma.$executeRawUnsafe(`UPDATE documents SET revision = revision - 1 WHERE id = $1`, documentId);
  });

  it("re-estimates a planned scope on another model without storing anything", async () => {
    const run = await plan();
    const res = await post(`/${run.id}/estimate`, { provider: "gemini", model: "gemini-2.5-flash", thinking: "low" });
    const est = (await res.json()) as { model: string; costLowUsd: number };
    expect(est.model).toBe("gemini-2.5-flash");
    expect(est.costLowUsd).toBeGreaterThan(0);
    const stored = await prisma.rfiReviewRun.findUniqueOrThrow({ where: { id: run.id } });
    expect(stored.model).not.toBe("gemini-2.5-flash");
  });

  it("builds the report from stored rows, as JSON and as a PDF", async () => {
    const run = await plan();
    const json = (await (await fetch(`${base}/${run.id}/report.json?kind=draft`)).json()) as { kind: string; checks: unknown[]; disclaimer: string[] };
    expect(json.kind).toBe("draft");
    expect(json.checks).toHaveLength(16);
    expect(json.disclaimer[0]).toMatch(/DRAFT/);
    const pdf = await fetch(`${base}/${run.id}/report.pdf?kind=accepted`);
    expect(pdf.headers.get("content-type")).toBe("application/pdf");
    expect(Buffer.from(await pdf.arrayBuffer()).subarray(0, 5).toString()).toBe("%PDF-");
  });
});
