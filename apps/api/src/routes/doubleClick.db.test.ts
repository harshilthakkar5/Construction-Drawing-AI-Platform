import { randomUUID } from "node:crypto";
import type { AddressInfo } from "node:net";
import type { Server } from "node:http";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";

const summarizeAdd = vi.fn(async () => ({ id: "job-s" }));
const scrapeAdd = vi.fn(async () => ({ id: "job-r" }));
vi.mock("../queues.js", () => ({
  summarizePortionQueue: { add: summarizeAdd },
  scrapeRegionQueue: { add: scrapeAdd, getJobs: async () => [] },
}));
vi.mock("../rateLimit.js", () => ({ summaryLimiter: (_q: unknown, _s: unknown, next: () => void) => next() }));
vi.mock("../redis.js", () => ({ redis: { del: async () => 0 } }));

/** Two presses at once start ONE paid run, against a real, migrated database. */
const url = process.env.RFI_TEST_DATABASE_URL;

describe.skipIf(!url)("a double-click starts one run", () => {
  let server: Server;
  let base: string;
  let prisma: typeof import("../db.js")["prisma"];
  const projectId = randomUUID();
  const portionId = randomUUID();
  const userId = randomUUID();

  beforeAll(async () => {
    process.env.DATABASE_URL = url;
    ({ prisma } = await import("../db.js"));
    const express = (await import("express")).default;
    const { portionsRouter } = await import("./portions.js");
    const { regionRouter } = await import("./region.js");
    const app = express();
    app.use(express.json());
    app.use((req, _res, next) => ((req as unknown as { user: { id: string } }).user = { id: userId }, next()));
    app.use("/projects/:projectId/portions", portionsRouter);
    app.use("/projects/:projectId/region", regionRouter);
    server = app.listen(0);
    base = `http://127.0.0.1:${(server.address() as AddressInfo).port}/projects/${projectId}`;
    await prisma.user.create({ data: { id: userId, email: `${userId}@test.invalid`, name: "t", passwordHash: "x" } });
    await prisma.$executeRawUnsafe(`INSERT INTO projects (id, name) VALUES ($1, 'double click')`, projectId);
    await prisma.portion.create({
      data: { id: portionId, projectId, name: "Structural", discipline: "structural", startPage: 1, endPage: 1, pageCount: 1, summaryStatus: "ready" },
    });
    await prisma.sheetRegion.create({ data: { projectId, relX: 0.8, relY: 0.8, relW: 0.1, relH: 0.1, scrapeStatus: "completed" } });
  });

  afterAll(async () => {
    server?.close();
    await prisma?.project.deleteMany({ where: { id: projectId } });
    await prisma?.user.deleteMany({ where: { id: userId } });
    await prisma?.$disconnect();
  });

  const twice = (path: string) =>
    Promise.all([0, 1].map(() => fetch(`${base}${path}`, { method: "POST", headers: { "content-type": "application/json" }, body: "{}" })))
      .then((r) => r.map((x) => x.status).sort());

  it("queues one summary", async () => {
    expect(await twice(`/portions/${portionId}/summarize`)).toEqual([202, 409]);
    expect(summarizeAdd).toHaveBeenCalledOnce();
  });

  it("queues one sheet read", async () => {
    expect(await twice("/region/rescrape")).toEqual([202, 409]);
    expect(scrapeAdd).toHaveBeenCalledOnce();
  });
});
