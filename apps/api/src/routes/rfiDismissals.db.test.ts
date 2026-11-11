import { randomUUID } from "node:crypto";
import type { AddressInfo } from "node:net";
import type { Server } from "node:http";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";

vi.mock("../queues.js", () => ({ rfiScanQueue: { add: vi.fn() }, rfiFullScanQueue: { add: vi.fn() } }));

/** Dismissing with a reason, and remembering it for an item or a mark family,
 * against a real, migrated database. */
const url = process.env.RFI_TEST_DATABASE_URL;

describe.skipIf(!url)("remembered dismissals", () => {
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
    await prisma.$executeRawUnsafe(`INSERT INTO users (id, email, name, "passwordHash") VALUES ($1, $2, 'dismiss', 'x')`, userId, `${userId}@test.invalid`);
    await prisma.$executeRawUnsafe(`INSERT INTO projects (id, name) VALUES ($1, 'dismiss')`, projectId);
  });

  afterAll(async () => {
    server?.close();
    await prisma?.project.deleteMany({ where: { id: projectId } });
    await prisma?.user.deleteMany({ where: { id: userId } });
    await prisma?.$disconnect();
  });

  const finding = (itemKey: string | null, checkType = "unscheduled_mark") =>
    prisma.rfiCandidate.create({
      data: {
        projectId, fingerprint: `t:${randomUUID()}`, checkType, confidence: "high", itemKey,
        subject: `${itemKey} not scheduled`, question: "?", questionSource: "template", evidence: [],
      },
    });
  const dismiss = (id: string, body: object) =>
    fetch(`${base}/${id}/dismiss`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  const status = async (id: string) => (await prisma.rfiCandidate.findUniqueOrThrow({ where: { id } })).status;

  it("keeps the reason and note on a plain dismissal and remembers nothing", async () => {
    const c = await finding("F6.0");
    const res = await dismiss(c.id, { reason: "answered", note: "on S2.01" });
    expect(res.status).toBe(200);
    const row = await prisma.rfiCandidate.findUniqueOrThrow({ where: { id: c.id } });
    expect([row.status, row.dismissReason, row.dismissNote]).toEqual(["dismissed", "answered", "on S2.01"]);
    expect(await prisma.rfiDismissalRule.count({ where: { projectId } })).toBe(0);
  });

  it("a family rule dismisses the family's waiting findings, and restore takes it back", async () => {
    const r7 = await finding("R7");
    const r12 = await finding("R12");
    const other = await finding("F2");
    const loop = await finding("R7", "schedule_plan_loop"); // another check: not covered
    const res = await dismiss(r7.id, { reason: "wrong_item", remember: "family" });
    expect(await res.json()).toEqual({ dismissed: true, alsoDismissed: 1 });
    expect([await status(r12.id), await status(other.id), await status(loop.id)]).toEqual(["dismissed", "pending", "pending"]);
    const rules = (await (await fetch(`${base}/dismissal-rules`)).json()) as { id: string; scope: string; key: string }[];
    expect(rules.map((r) => [r.scope, r.key])).toEqual([["family", "R"]]);

    expect((await fetch(`${base}/${r7.id}/restore`, { method: "POST" })).status).toBe(200);
    expect(await prisma.rfiDismissalRule.count({ where: { projectId } })).toBe(0);
    const back = await prisma.rfiCandidate.findUniqueOrThrow({ where: { id: r7.id } });
    expect([back.status, back.dismissReason]).toEqual(["pending", null]);
  });

  it("an item rule can be forgotten, and a finding with no item cannot be remembered", async () => {
    const c = await finding("RTU-3", "tag_value_conflict");
    expect((await dismiss(c.id, { remember: "item" })).status).toBe(200);
    const [rule] = (await (await fetch(`${base}/dismissal-rules`)).json()) as { id: string; key: string }[];
    expect(rule!.key).toBe("RTU-3");
    expect((await fetch(`${base}/dismissal-rules/${rule!.id}`, { method: "DELETE" })).status).toBe(200);
    expect(await prisma.rfiDismissalRule.count({ where: { projectId } })).toBe(0);

    const tbd = await finding(null, "open_item_note");
    expect((await dismiss(tbd.id, { remember: "item" })).status).toBe(400);
    // A tag has no family.
    const tag = await finding("RTU-4", "tag_value_conflict");
    expect((await dismiss(tag.id, { remember: "family" })).status).toBe(400);
    expect(await status(tag.id)).toBe("pending");
  });
});
