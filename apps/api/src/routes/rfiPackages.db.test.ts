import { randomUUID } from "node:crypto";
import type { AddressInfo } from "node:net";
import type { Server } from "node:http";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";

/**
 * Marked-up package routes against a real, migrated database
 * (RFI_TEST_DATABASE_URL), with the queue stubbed. What only a request shows:
 * nothing from another project can be put in a package, a review expands to
 * its findings, and a download link appears only once the worker is done.
 */
const url = process.env.RFI_TEST_DATABASE_URL;

const added: unknown[] = [];
vi.mock("../queues.js", () => ({
  rfiPackageQueue: { add: vi.fn(async (_n: string, data: unknown) => (added.push(data), { id: "j" })) },
}));
vi.mock("../s3.js", () => ({ presignGetObject: vi.fn(async (key: string) => `https://files.test/${key}`) }));

describe.skipIf(!url)("rfi package routes against a real database", () => {
  let server: Server;
  let base: string;
  let prisma: typeof import("../db.js")["prisma"];
  const projectId = randomUUID();
  const otherProject = randomUUID();
  const userId = randomUUID();
  const rfiId = randomUUID();
  const foreignRfi = randomUUID();
  const runId = randomUUID();
  const candidates = [randomUUID(), randomUUID(), randomUUID()];

  beforeAll(async () => {
    process.env.DATABASE_URL = url;
    process.env.REDIS_URL ??= "redis://127.0.0.1:1";
    process.env.QDRANT_URL ??= "http://127.0.0.1:1";
    ({ prisma } = await import("../db.js"));
    const express = (await import("express")).default;
    const { rfiPackagesRouter } = await import("./rfiPackages.js");
    const app = express();
    app.use(express.json());
    app.use((req, _res, next) => {
      (req as unknown as { user: { id: string } }).user = { id: userId };
      next();
    });
    app.use("/projects/:projectId/rfis/packages", rfiPackagesRouter);
    app.use((err: Error & { code?: string }, _req: unknown, res: { status: (n: number) => { json: (b: unknown) => void } }, _next: unknown) => {
      res.status(err.name === "ZodError" ? 400 : err.code === "P2025" ? 404 : 500).json({ error: err.message });
    });
    server = app.listen(0);
    base = `http://127.0.0.1:${(server.address() as AddressInfo).port}/projects/${projectId}/rfis/packages`;

    await prisma.$executeRawUnsafe(`INSERT INTO users (id, email, name, "passwordHash") VALUES ($1, $2, 'pkg test', 'x')`, userId, `${userId}@test.invalid`);
    for (const id of [projectId, otherProject]) await prisma.$executeRawUnsafe(`INSERT INTO projects (id, name) VALUES ($1, 'pkg')`, id);
    for (const [id, project, n] of [[rfiId, projectId, 1], [foreignRfi, otherProject, 1]] as const) {
      await prisma.$executeRawUnsafe(
        `INSERT INTO rfis (id, "projectId", number, subject, question, "updatedAt") VALUES ($1, $2, $3, 's', 'q', now())`,
        id, project, n,
      );
    }
    await prisma.$executeRawUnsafe(
      `INSERT INTO rfi_review_runs (id, "projectId", target, "checkIds", scope, "scopeHash") VALUES ($1, $2, '{}', '[]', '{}', 'h')`,
      runId, projectId,
    );
    for (const [i, id] of candidates.entries()) {
      await prisma.$executeRawUnsafe(
        `INSERT INTO rfi_candidates (id, "projectId", "reviewRunId", origin, fingerprint, "checkType", confidence, subject, question,
           "questionSource", evidence, status, "updatedAt")
         VALUES ($1, $2, $3, 'targeted_review', $4, 'column_mismatch', 'high', 's', 'q', 'template', '[]',
           $5::"RfiCandidateStatus", now())`,
        id, projectId, runId, `fp-${id}`, i === 2 ? "dismissed" : "pending",
      );
    }
  });

  afterAll(async () => {
    server?.close();
    if (!prisma) return;
    await prisma.$executeRawUnsafe(`DELETE FROM projects WHERE id = ANY($1::text[])`, [projectId, otherProject]);
    await prisma.$executeRawUnsafe(`DELETE FROM users WHERE id = $1`, userId);
    await prisma.$disconnect();
  });

  const post = (body: unknown) =>
    fetch(base, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });

  it("queues one RFI and offers no link until the worker is done", async () => {
    const res = await post({ items: [{ type: "rfi", id: rfiId }] });
    expect(res.status).toBe(202);
    const dto = (await res.json()) as { id: string; status: string; downloadUrl: string | null };
    expect(dto).toMatchObject({ status: "queued", downloadUrl: null });
    expect(added.at(-1)).toEqual({ packageId: dto.id });

    await prisma.rfiPackage.update({ where: { id: dto.id }, data: { status: "ready", key: "k/p.pdf", pages: 3 } });
    const ready = (await (await fetch(`${base}/${dto.id}`)).json()) as { downloadUrl: string; pages: number };
    expect(ready).toMatchObject({ downloadUrl: "https://files.test/k/p.pdf", pages: 3 });
  });

  it("refuses an RFI from another project before queueing anything", async () => {
    const before = added.length;
    const res = await post({ items: [{ type: "rfi", id: foreignRfi }] });
    expect(res.status).toBe(404);
    expect(added.length).toBe(before);
  });

  it("a review expands to its findings, leaving out the dismissed ones", async () => {
    const dto = (await (await post({ reviewRunId: runId })).json()) as { items: { type: string; id: string }[] };
    expect(dto.items.map((i) => i.id).sort()).toEqual(candidates.slice(0, 2).sort());
    expect(dto.items.every((i) => i.type === "candidate")).toBe(true);
  });

  it("refuses an empty request", async () => {
    expect((await post({ items: [] })).status).toBe(400);
  });

  it("reports a package the worker never finished as failed", async () => {
    const row = await prisma.rfiPackage.create({
      data: { projectId, items: [], createdAt: new Date(Date.now() - 60 * 60 * 1000) },
    });
    const dto = (await (await fetch(`${base}/${row.id}`)).json()) as { status: string; error: string };
    expect(dto.status).toBe("failed");
    expect(dto.error).toMatch(/did not finish/);
  });
});
