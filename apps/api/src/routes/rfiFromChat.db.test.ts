import { randomUUID } from "node:crypto";
import type { AddressInfo } from "node:net";
import type { Server } from "node:http";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import { takeRfiSuggestion } from "../answer.js";

vi.mock("../queues.js", () => ({ rfiScanQueue: { add: vi.fn() }, rfiFullScanQueue: { add: vi.fn() } }));

describe("takeRfiSuggestion", () => {
  it("takes a last RFI line off the answer, citations stripped", () => {
    const r = takeRfiSuggestion("S2.30-B shows no mark at A1 [chunk:aaa].\n\nRFI: What is the type mark of the pile at A1/BK on S2.30-B? [chunk:aaa]");
    expect(r.text).toBe("S2.30-B shows no mark at A1 [chunk:aaa].");
    expect(r.rfi).toBe("What is the type mark of the pile at A1/BK on S2.30-B?");
  });
  it("leaves an answer with no RFI line, or one mid-answer, alone", () => {
    expect(takeRfiSuggestion("No issue here.")).toEqual({ text: "No issue here.", rfi: null });
    const mid = "RFI: not last\nThe answer goes on.";
    expect(takeRfiSuggestion(mid)).toEqual({ text: mid, rfi: null });
  });
});

/** Chat → Needs your review against a real, migrated database. */
const url = process.env.RFI_TEST_DATABASE_URL;

describe.skipIf(!url)("sending a chat answer to review", () => {
  let server: Server;
  let base: string;
  let prisma: typeof import("../db.js")["prisma"];
  const projectId = randomUUID();
  const otherProject = randomUUID();
  const userId = randomUUID();
  const docId = randomUUID();
  const rfiDocId = randomUUID();
  const sessionId = randomUUID();
  const answerId = randomUUID();
  const noCitations = randomUUID();
  const onlyOldRfis = randomUUID();
  const source = (documentId: string) => ({
    index: 1, label: "S2.30-B", chunkId: randomUUID(), documentId, filename: "s.pdf", pageNumber: 3,
    combinedPageNumber: 3, bbox: { x: 1, y: 2, width: 3, height: 4 }, sheetNumber: "S2.30-B",
  });

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
    base = `http://127.0.0.1:${(server.address() as AddressInfo).port}/projects`;
    await prisma.$executeRawUnsafe(`INSERT INTO users (id, email, name, "passwordHash") VALUES ($1, $2, 'chat rfi', 'x')`, userId, `${userId}@test.invalid`);
    for (const id of [projectId, otherProject]) await prisma.$executeRawUnsafe(`INSERT INTO projects (id, name) VALUES ($1, 'chat rfi')`, id);
    for (const [id, inRfi] of [[docId, true], [rfiDocId, false]] as const) {
      await prisma.$executeRawUnsafe(
        `INSERT INTO documents (id, "projectId", filename, "spacesKey", pages, "includeInRfiAnalysis") VALUES ($1, $2, 'x.pdf', $3, 5, $4)`,
        id, projectId, `k/${id}`, inRfi,
      );
    }
    await prisma.chatSession.create({ data: { id: sessionId, projectId } });
    const msg = (id: string, sources: unknown[]) =>
      prisma.message.create({
        data: { id, sessionId, role: "assistant", content: { displayedAnswer: "x", rfiSuggestion: "What is the pile mark at A1?" }, sources: sources as object[] },
      });
    await msg(answerId, [source(docId), source(rfiDocId)]);
    await msg(noCitations, []);
    await msg(onlyOldRfis, [source(rfiDocId)]);
  });

  afterAll(async () => {
    server?.close();
    await prisma.$executeRawUnsafe(`DELETE FROM projects WHERE id IN ($1, $2)`, projectId, otherProject);
    await prisma.$executeRawUnsafe(`DELETE FROM users WHERE id = $1`, userId);
    await prisma.$disconnect();
  });

  const send = (project: string, messageId: string, question = "What is the pile mark at A1?") =>
    fetch(`${base}/${project}/rfis/generated/from-chat`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ messageId, subject: "Pile mark at A1", question }),
    });

  it("files a medium finding pinned to the cited sheets the review can use, once", async () => {
    const r = await send(projectId, answerId);
    expect(r.status).toBe(201);
    const c = (await r.json()) as { origin: string; confidence: string; questionSource: string; evidence: { documentId: string }[] };
    expect([c.origin, c.confidence, c.questionSource]).toEqual(["chat", "medium", "model"]);
    expect(c.evidence.map((e) => e.documentId)).toEqual([docId]);
    expect((await send(projectId, answerId, "edited")).status).toBe(409);
  });

  it("refuses an answer with nothing to pin, one citing only old RFIs, and another project's answer", async () => {
    expect((await send(projectId, noCitations)).status).toBe(400);
    expect((await send(projectId, onlyOldRfis)).status).toBe(400);
    expect((await send(otherProject, answerId)).status).toBe(404);
  });
});
