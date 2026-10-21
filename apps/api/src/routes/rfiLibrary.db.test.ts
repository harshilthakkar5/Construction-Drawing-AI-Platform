import { randomUUID } from "node:crypto";
import type { AddressInfo } from "node:net";
import type { Server } from "node:http";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import type { RfiLibraryDto, RfiLibraryQuestionDto } from "@cdip/shared";

/**
 * Library routes against a real, migrated database (RFI_TEST_DATABASE_URL):
 * a project's saved questions land under the question they follow up, never
 * under another element or another project, and a delete takes its
 * follow-ups with it.
 */
const url = process.env.RFI_TEST_DATABASE_URL;

describe.skipIf(!url)("rfi library routes against a real database", () => {
  let server: Server;
  let base: string;
  let other: string;
  let prisma: typeof import("../db.js")["prisma"];
  const projectId = randomUUID();
  const otherProject = randomUUID();
  const userId = randomUUID();

  beforeAll(async () => {
    process.env.DATABASE_URL = url;
    process.env.REDIS_URL ??= "redis://127.0.0.1:1";
    process.env.QDRANT_URL ??= "http://127.0.0.1:1";
    ({ prisma } = await import("../db.js"));
    const express = (await import("express")).default;
    const { rfiLibraryRouter } = await import("./rfiLibrary.js");
    const app = express();
    app.use(express.json());
    app.use((req, _res, next) => {
      (req as unknown as { user: { id: string } }).user = { id: userId };
      next();
    });
    app.use("/projects/:projectId/rfis/library", rfiLibraryRouter);
    app.use((err: Error & { code?: string }, _req: unknown, res: { status: (n: number) => { json: (b: unknown) => void } }, _next: unknown) => {
      res.status(err.name === "ZodError" ? 400 : err.code === "P2025" ? 404 : 500).json({ error: err.message });
    });
    server = app.listen(0);
    const root = `http://127.0.0.1:${(server.address() as AddressInfo).port}/projects`;
    base = `${root}/${projectId}/rfis/library`;
    other = `${root}/${otherProject}/rfis/library`;
    await prisma.$executeRawUnsafe(`INSERT INTO users (id, email, name, "passwordHash") VALUES ($1, $2, 'lib test', 'x')`, userId, `${userId}@test.invalid`);
    for (const id of [projectId, otherProject]) await prisma.$executeRawUnsafe(`INSERT INTO projects (id, name) VALUES ($1, 'lib')`, id);
  });

  afterAll(async () => {
    server?.close();
    await prisma.$executeRawUnsafe(`DELETE FROM projects WHERE id IN ($1, $2)`, projectId, otherProject);
    await prisma.$executeRawUnsafe(`DELETE FROM users WHERE id = $1`, userId);
    await prisma.$disconnect();
  });

  const send = (u: string, method: string, body?: unknown) =>
    fetch(u, { method, headers: { "content-type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body) });
  const add = async (body: unknown) => {
    const r = await send(`${base}/questions`, "POST", body);
    return { status: r.status, body: (await r.json()) as { id: string; error?: string } };
  };
  const tree = async (u = base) => (await (await fetch(u)).json()) as RfiLibraryDto;
  const grids = (dto: RfiLibraryDto) => dto.packages[0]!.elements[0]!.questions;
  const find = (qs: RfiLibraryQuestionDto[], id: string): RfiLibraryQuestionDto | undefined =>
    qs.find((q) => q.id === id) ?? qs.map((q) => find(q.children, id)).find(Boolean);

  it("saves follow-ups to any depth and shows them only to their project", async () => {
    const a = await add({ elementId: "CE-01-01", parentId: "CE-01-01-Q02", text: "  Between grids D and E only?  " });
    expect(a.status).toBe(201);
    const b = await add({ elementId: "CE-01-01", parentId: a.body.id, text: "At level 3?" });
    const c = await add({ elementId: "CE-01-01", parentId: "CE-01-01-U-04", text: "Top of pier?" });
    expect([b.status, c.status]).toEqual([201, 201]);

    const dto = await tree();
    expect(dto.version).toMatch(/^[0-9a-f]{12}$/);
    const first = find(grids(dto), "CE-01-01-Q02")!.children[0]!;
    expect([first.text, first.source]).toEqual(["Between grids D and E only?", "custom"]);
    expect(first.children.map((q) => q.text)).toEqual(["At level 3?"]);
    expect(find(grids(dto), "CE-01-01-U-04")!.children.map((q) => q.id)).toEqual([c.body.id]);
    expect(JSON.stringify(await tree(other))).not.toContain("Between grids D and E");
  });

  it("refuses a parent from another element, an unknown element, and an empty question", async () => {
    expect((await add({ elementId: "CE-01-01", parentId: "CE-01-02-Q01", text: "x" })).status).toBe(400);
    expect((await add({ elementId: "CE-99-99", text: "no such element" })).status).toBe(400);
    expect((await add({ elementId: "CE-01-01", text: "   " })).status).toBe(400);
  });

  it("edits only its own project's questions, and a delete takes the follow-ups", async () => {
    const top = await add({ elementId: "CE-07-01", category: "Location", text: "Column at 4/C?" });
    const child = await add({ elementId: "CE-07-01", parentId: top.body.id, text: "Offset dimensioned?" });
    const grandchild = await add({ elementId: "CE-07-01", parentId: child.body.id, text: "From which grid?" });
    const keep = await add({ elementId: "CE-07-01", text: "Unrelated" });

    expect((await send(`${other}/questions/${top.body.id}`, "PATCH", { text: "hijack" })).status).toBe(404);
    expect((await send(`${other}/questions/${top.body.id}`, "DELETE")).status).toBe(404);
    const patched = await send(`${base}/questions/${top.body.id}`, "PATCH", { text: "Column at 4/D?" });
    expect(((await patched.json()) as { text: string }).text).toBe("Column at 4/D?");

    const del = await send(`${base}/questions/${top.body.id}`, "DELETE");
    expect(((await del.json()) as { deleted: string[] }).deleted.sort()).toEqual([top.body.id, child.body.id, grandchild.body.id].sort());
    const left = await prisma.rfiCustomQuestion.findMany({ where: { projectId, elementId: "CE-07-01" } });
    expect(left.map((q) => q.id)).toEqual([keep.body.id]);
  });
});
