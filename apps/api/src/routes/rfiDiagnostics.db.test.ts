import { randomUUID } from "node:crypto";
import type { AddressInfo } from "node:net";
import type { Server } from "node:http";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";

/**
 * Diagnostic-export download against a real, migrated database
 * (RFI_TEST_DATABASE_URL), storage stubbed. What only a request shows: a run
 * id from another project is a 404 (never a link to that project's drawings),
 * a run with no export says how to turn the export on, and a recorded one
 * hands out a link to the key the worker writes.
 */
const url = process.env.RFI_TEST_DATABASE_URL;

const stored = new Set<string>();
vi.mock("../s3.js", () => ({
  objectExists: vi.fn(async (key: string) => stored.has(key)),
  presignGetObject: vi.fn(async (key: string) => `https://files.test/${key}`),
}));

describe.skipIf(!url)("rfi diagnostics route against a real database", () => {
  let server: Server;
  let base: string;
  let prisma: typeof import("../db.js")["prisma"];
  const projectId = randomUUID();
  const otherProject = randomUUID();
  const runId = randomUUID();
  const foreignRun = randomUUID();

  beforeAll(async () => {
    process.env.DATABASE_URL = url;
    process.env.REDIS_URL ??= "redis://127.0.0.1:1";
    process.env.QDRANT_URL ??= "http://127.0.0.1:1";
    ({ prisma } = await import("../db.js"));
    const express = (await import("express")).default;
    const { rfiDiagnosticsRouter } = await import("./rfiDiagnostics.js");
    const app = express();
    app.use("/projects/:projectId/rfis/diagnostics", rfiDiagnosticsRouter);
    server = app.listen(0);
    base = `http://127.0.0.1:${(server.address() as AddressInfo).port}/projects/${projectId}/rfis/diagnostics`;
    for (const id of [projectId, otherProject]) await prisma.$executeRawUnsafe(`INSERT INTO projects (id, name) VALUES ($1, 'diag')`, id);
    for (const [id, project] of [[runId, projectId], [foreignRun, otherProject]] as const) {
      await prisma.$executeRawUnsafe(
        `INSERT INTO rfi_review_runs (id, "projectId", target, "checkIds", scope, "scopeHash") VALUES ($1, $2, '{}', '[]', '{}', 'h')`,
        id, project,
      );
    }
  });

  afterAll(async () => {
    server?.close();
    if (!prisma) return;
    await prisma.$executeRawUnsafe(`DELETE FROM projects WHERE id = ANY($1::text[])`, [projectId, otherProject]);
    await prisma.$disconnect();
  });

  it("refuses a run that belongs to another project, even when its export exists", async () => {
    stored.add(`projects/${otherProject}/rfi-diagnostics/review-${foreignRun}.zip`);
    const res = await fetch(`${base}/review/${foreignRun}`);
    expect(res.status).toBe(404);
  });

  it("says how to turn the export on when the run has none", async () => {
    const res = await fetch(`${base}/review/${runId}`);
    expect(res.status).toBe(404);
    expect(((await res.json()) as { error: string }).error).toMatch(/RFI_DIAGNOSTICS=on/);
  });

  it("links the key the worker writes", async () => {
    stored.add(`projects/${projectId}/rfi-diagnostics/review-${runId}.zip`);
    const res = await fetch(`${base}/review/${runId}`);
    expect(res.status).toBe(200);
    expect(((await res.json()) as { downloadUrl: string }).downloadUrl).toBe(
      `https://files.test/projects/${projectId}/rfi-diagnostics/review-${runId}.zip`,
    );
  });

  it("rejects an unknown kind", async () => {
    expect((await fetch(`${base}/other/${runId}`)).status).toBe(400);
  });
});
