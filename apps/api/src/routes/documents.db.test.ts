import { randomUUID } from "node:crypto";
import type { AddressInfo } from "node:net";
import type { Server } from "node:http";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";

const createMultipartUpload = vi.fn();
vi.mock("../queues.js", () => ({ processDocumentQueue: { add: vi.fn() } }));
vi.mock("../s3.js", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../s3.js")>()),
  createMultipartUpload: (...args: unknown[]) => createMultipartUpload(...args),
}));

/** A revision whose storage upload could not start leaves nothing behind, against a real, migrated database. */
const url = process.env.RFI_TEST_DATABASE_URL;

describe.skipIf(!url)("starting a document upload", () => {
  let server: Server;
  let base: string;
  let prisma: typeof import("../db.js")["prisma"];
  const projectId = randomUUID();
  const previousId = randomUUID();

  beforeAll(async () => {
    process.env.DATABASE_URL = url;
    process.env.REDIS_URL ??= "redis://127.0.0.1:1";
    process.env.QDRANT_URL ??= "http://127.0.0.1:1";
    ({ prisma } = await import("../db.js"));
    const express = (await import("express")).default;
    const { documentsRouter } = await import("./documents.js");
    const app = express();
    app.use(express.json());
    app.use("/projects/:projectId/documents", documentsRouter);
    server = app.listen(0);
    base = `http://127.0.0.1:${(server.address() as AddressInfo).port}/projects/${projectId}/documents`;
    await prisma.$executeRawUnsafe(`INSERT INTO projects (id, name) VALUES ($1, 'upload')`, projectId);
    await prisma.document.create({ data: { id: previousId, projectId, filename: "set.pdf", spacesKey: "k", pages: 1 } });
  });

  afterAll(async () => {
    server?.close();
    await prisma?.project.deleteMany({ where: { id: projectId } });
    await prisma?.$disconnect();
  });

  it("deletes the new revision's row when the upload cannot start, so a retry is not refused", async () => {
    // Victaulic: storage was down for the first attempt, and the retry hit "Unique constraint failed on previousVersionId".
    const start = () => fetch(base, {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ filename: "set rev 2.pdf", size: 1000, replacesDocumentId: previousId }),
    });
    createMultipartUpload.mockRejectedValueOnce(new Error("NoSuchBucket"));
    expect((await start()).status).toBe(500);
    expect(await prisma.document.count({ where: { previousVersionId: previousId } })).toBe(0);
    createMultipartUpload.mockResolvedValueOnce("upload-1");
    const res = await start();
    expect(res.status).toBe(201);
    expect(((await res.json()) as { uploadId: string }).uploadId).toBe("upload-1");
    expect(await prisma.document.count({ where: { previousVersionId: previousId } })).toBe(1);
  });
});
