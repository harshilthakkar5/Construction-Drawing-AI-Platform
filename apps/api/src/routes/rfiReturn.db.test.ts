import { randomUUID } from "node:crypto";
import type { AddressInfo } from "node:net";
import type { Server } from "node:http";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";

vi.mock("../queues.js", () => ({ rfiScanQueue: { add: vi.fn() }, rfiFullScanQueue: { add: vi.fn() } }));

/** An RFI accepted by mistake goes back to "Needs your review", against a real, migrated database. */
const url = process.env.RFI_TEST_DATABASE_URL;

describe.skipIf(!url)("sending an accepted RFI back to review", () => {
  let server: Server;
  let base: string;
  let prisma: typeof import("../db.js")["prisma"];
  const projectId = randomUUID();
  const userId = randomUUID();

  beforeAll(async () => {
    process.env.DATABASE_URL = url;
    process.env.REDIS_URL ??= "redis://127.0.0.1:1";
    process.env.QDRANT_URL ??= "http://127.0.0.1:1";
    ({ prisma } = await import("../db.js"));
    const express = (await import("express")).default;
    const { rfiGeneratedRouter } = await import("./rfiGenerated.js");
    const app = express();
    app.use(express.json());
    app.use((req, _res, next) => {
      (req as unknown as { user: { id: string } }).user = { id: userId };
      next();
    });
    app.use("/projects/:projectId/rfis/generated", rfiGeneratedRouter);
    server = app.listen(0);
    base = `http://127.0.0.1:${(server.address() as AddressInfo).port}/projects/${projectId}/rfis/generated`;
    await prisma.$executeRawUnsafe(`INSERT INTO users (id, email, name, "passwordHash") VALUES ($1, $2, 'return', 'x')`, userId, `${userId}@test.invalid`);
    await prisma.$executeRawUnsafe(`INSERT INTO projects (id, name) VALUES ($1, 'return')`, projectId);
  });

  afterAll(async () => {
    server?.close();
    await prisma?.project.deleteMany({ where: { id: projectId } });
    await prisma?.user.deleteMany({ where: { id: userId } });
    await prisma?.$disconnect();
  });

  async function accepted() {
    const c = await prisma.rfiCandidate.create({
      data: {
        projectId, fingerprint: `t:${randomUUID()}`, checkType: "open_item_note", confidence: "high",
        subject: "TBD", question: "What is it?", questionSource: "template", evidence: [],
      },
    });
    const res = await fetch(`${base}/${c.id}/accept`, { method: "POST" });
    expect(res.status).toBe(201);
    return { candidateId: c.id, rfi: (await res.json()) as { id: string; number: number } };
  }
  const back = (rfiId: string) => fetch(`${base}/from-rfi/${rfiId}/return`, { method: "POST" });

  it("voids the RFI, keeps its number, and puts the finding back as pending", async () => {
    const { candidateId, rfi } = await accepted();
    expect((await back(rfi.id)).status).toBe(200);
    const after = await prisma.rfi.findUniqueOrThrow({ where: { id: rfi.id }, include: { events: true } });
    expect(after.status).toBe("voided");
    expect(after.number).toBe(rfi.number);
    expect(after.events.map((e) => e.kind)).toContain("returned_to_review");
    const c = await prisma.rfiCandidate.findUniqueOrThrow({ where: { id: candidateId } });
    expect([c.status, c.rfiId, c.decidedAt]).toEqual(["pending", null, null]);
    // Twice is refused: it is no longer linked to a finding.
    expect((await back(rfi.id)).status).toBe(409);
    // And it can be accepted again, under a new number.
    const again = await fetch(`${base}/${candidateId}/accept`, { method: "POST" });
    expect(again.status).toBe(201);
    expect(((await again.json()) as { number: number }).number).toBeGreaterThan(rfi.number);
  });

  it("refuses an RFI someone has answered, and leaves the finding accepted", async () => {
    const { candidateId, rfi } = await accepted();
    await prisma.rfi.update({ where: { id: rfi.id }, data: { answer: "Use 4000 psi.", status: "answered" } });
    expect((await back(rfi.id)).status).toBe(409);
    const c = await prisma.rfiCandidate.findUniqueOrThrow({ where: { id: candidateId } });
    expect([c.status, c.rfiId]).toEqual(["accepted", rfi.id]);
  });

  it("refuses an RFI typed by a person", async () => {
    const typed = await prisma.rfi.create({ data: { projectId, number: 900, subject: "s", question: "q", status: "open" } });
    expect((await back(typed.id)).status).toBe(409);
  });
});
