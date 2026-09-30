import { randomUUID } from "node:crypto";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

/**
 * planReview against a real, MIGRATED database, with the chat's own
 * retrieveChunkIds — skipped unless RFI_TEST_DATABASE_URL is set (the same
 * variable the worker's database tests read). The pure ranking is covered in
 * rfiReviewRules.test.ts; what only SQL can show is which pages the scope
 * query lets in: a superseded revision, a document taken out of RFI analysis
 * and another project's drawings must never reach a plan.
 *
 * With no embedding key the dense arm fails and retrieval runs on its
 * keyword and exact-identifier arms, which is what the plan note reports.
 */
const url = process.env.RFI_TEST_DATABASE_URL;

describe.skipIf(!url)("planReview against a real database", () => {
  // Assigned in beforeAll, AFTER DATABASE_URL points at the test database:
  // the Prisma client reads it when db.ts is first imported.
  let planner: typeof import("./rfiReviewPlanner.js");
  let prisma: typeof import("./db.js")["prisma"];
  const projectId = randomUUID();
  const otherProject = randomUUID();
  const userId = randomUUID();
  const page: Record<string, string> = {};

  async function doc(project: string, over: { superseded?: boolean; excluded?: boolean } = {}) {
    const id = randomUUID();
    await prisma.$executeRawUnsafe(
      `INSERT INTO documents (id, "projectId", filename, "spacesKey", pages, status, "supersededAt", "includeInRfiAnalysis")
       VALUES ($1, $2, 'set.pdf', 'k', 1, 'completed', $3, $4)`,
      id,
      project,
      over.superseded ? new Date() : null,
      !over.excluded,
    );
    return id;
  }

  async function sheet(
    name: string,
    documentId: string,
    sheetNumber: string,
    n: number,
    texts: string[],
    over: { region?: string; discipline?: string } = {},
  ) {
    const id = randomUUID();
    page[name] = id;
    await prisma.$executeRawUnsafe(
      `INSERT INTO pages (id, "documentId", "pageNumber", "combinedPageNumber", "sheetNumber", "sheetRegionText", discipline)
       VALUES ($1, $2, $3, $3, $4, $5, $6)`,
      id,
      documentId,
      n,
      sheetNumber,
      over.region ?? null,
      over.discipline ?? null,
    );
    for (const text of texts) {
      const chunk = randomUUID();
      await prisma.$executeRawUnsafe(
        `INSERT INTO chunks (id, "pageId", text, bbox, "tokenCount", kind)
         VALUES ($1, $2, $3, '{"x":100,"y":100,"width":80,"height":20}'::jsonb, 30, 'text')`,
        chunk,
        id,
        text,
      );
      await prisma.$executeRawUnsafe(
        `INSERT INTO chunk_identifiers ("chunkId", identifier)
         SELECT $1, unnest(cdip_identifiers($2)) ON CONFLICT DO NOTHING`,
        chunk,
        text,
      );
    }
  }

  beforeAll(async () => {
    process.env.DATABASE_URL = url;
    // Required by env.ts; nothing needs them to answer. A refused Qdrant
    // connection is the dense arm failing, which retrieval already tolerates.
    process.env.REDIS_URL ??= "redis://127.0.0.1:1";
    process.env.QDRANT_URL ??= "http://127.0.0.1:1";
    ({ prisma } = await import("./db.js"));
    planner = await import("./rfiReviewPlanner.js");
    await prisma.$executeRawUnsafe(
      `INSERT INTO users (id, email, name, "passwordHash") VALUES ($1, $2, 'planner test', 'x')`,
      userId,
      `${userId}@test.invalid`,
    );
    for (const id of [projectId, otherProject]) {
      await prisma.$executeRawUnsafe(`INSERT INTO projects (id, name) VALUES ($1, 'review plan test')`, id);
    }
    const live = await doc(projectId);
    await sheet(
      "target",
      live,
      "S2.105",
      1,
      ["COLUMN C-6 (14 x 30) AT GRID 3/C", "LEVEL 5 FORMING PLAN", "REFER TO SHEET S6.01 AND S5.99 FOR COLUMN SCHEDULE"],
      { region: "S2.105 LEVEL 5 FORMING PLAN", discipline: "structural" },
    );
    await sheet("schedule", live, "S6.01", 2, ["COLUMN SCHEDULE C-6 14 x 30 CONCRETE COLUMN"], { discipline: "structural" });
    await sheet("arch", live, "A3.01", 3, ["ENLARGED UNIT PLAN COLUMN AT GRID 3/C"], {
      region: "A3.01 LEVEL 5 ENLARGED UNIT PLAN",
      discipline: "architectural",
    });
    await sheet("pilecap", live, "S1.01", 6, ["PILE CAP PC1 AT GRID 1/A"], { region: "S1.01 FOUNDATION PLAN", discipline: "structural" });
    await sheet("superseded", await doc(projectId, { superseded: true }), "S6.02", 4, ["COLUMN SCHEDULE C-6 OLD"]);
    await sheet("excluded", await doc(projectId, { excluded: true }), "S6.03", 5, ["COLUMN C-6 EXCLUDED"]);
    await sheet("foreign", await doc(otherProject), "S6.04", 1, ["COLUMN SCHEDULE C-6 OTHER PROJECT"]);
  });

  afterAll(async () => {
    if (!prisma) return;
    await prisma.$executeRawUnsafe(`DELETE FROM projects WHERE id = ANY($1::text[])`, [projectId, otherProject]);
    await prisma.$executeRawUnsafe(`DELETE FROM users WHERE id = $1`, userId);
    await prisma.$disconnect();
  });

  const request = (over: Partial<import("./rfiReviewPlanner.js").PlanRequest> = {}) => ({
    target: { type: "sheet" as const, value: "s-2.105" },
    checkMode: "auto" as const,
    checkIds: [],
    depth: "standard" as const,
    thinking: "medium" as const,
    excludePageIds: [],
    ...over,
  });

  it("scopes the named sheet first, adds what retrieval finds, and never a page it must not see", async () => {
    const run = await planner.planReview(projectId, userId, request());
    expect(run.status).toBe("planned");
    const dto = planner.toReviewDto(run, 0);
    expect(dto.pages[0]).toMatchObject({ pageId: page.target, role: "target", visual: true });
    const ids = dto.pages.map((p) => p.pageId);
    expect(ids).toContain(page.schedule);
    for (const never of ["superseded", "excluded", "foreign"]) expect(ids).not.toContain(page[never]);
    expect(dto.checkIds).toEqual(expect.arrayContaining(["G01", "G02", "C01", "C03"]));
    expect(dto.checkIds).not.toContain("F04");
    expect(dto.checkPlan.F04).toMatchObject({ selected: false, applicability: "unknown" });
    expect(Object.keys(dto.checkPlan)).toHaveLength(16);
    expect(dto.catalogueVersion).toMatch(/^\d{4}-\d{2}-\d{2}\.\d+$/);
    expect(dto.estimate?.modelCalls).toBe(3);
    expect(dto.pages.every((p) => p.reason.length > 0)).toBe(true);
    expect(dto.estimate?.imageParts).toBeGreaterThan(0);
    expect(dto.scopeHash).toMatch(/^[0-9a-f]{64}$/);
    expect(dto.notes.join(" ")).toMatch(/Semantic search returned nothing/);
  });

  it("forces the sheets the target refers to into the scope, and names the ones that are not in the project", async () => {
    const dto = planner.toReviewDto(await planner.planReview(projectId, userId, request()), 0);
    const ref = dto.pages.find((p) => p.pageId === page.schedule)!;
    expect(ref).toMatchObject({ role: "reference", visual: true });
    expect(ref.reason).toMatch(/refers to S6.01/);
    expect(dto.coverage.unresolvedReferences).toEqual(["S599"]);
    expect(dto.notes.join(" ")).toMatch(/S599/);
  });

  it("brings in the same level drawn by another discipline", async () => {
    const dto = planner.toReviewDto(await planner.planReview(projectId, userId, request()), 0);
    const arch = dto.pages.find((p) => p.pageId === page.arch)!;
    expect(arch).toMatchObject({ role: "correspondence" });
    expect(arch.reason).toMatch(/LEVEL 5.*architectural/);
  });

  it("an element target finds the mark's sheets and routes by its family", async () => {
    const dto = planner.toReviewDto(
      await planner.planReview(projectId, userId, request({ target: { type: "element", value: "c-6" } })),
      0,
    );
    const roles = dto.pages.filter((p) => p.role === "element").map((p) => p.pageId);
    expect(roles).toEqual(expect.arrayContaining([page.target, page.schedule]));
    for (const never of ["superseded", "excluded", "foreign"]) expect(roles).not.toContain(page[never]);
    expect(dto.checkIds).toEqual(["G01", "G02", "C01", "C03"]);
    expect(dto.ambiguous).toEqual(["c-6"]);
    const pile = planner.toReviewDto(
      await planner.planReview(projectId, userId, request({ target: { type: "element", value: "PC1" } })),
      0,
    );
    expect(pile.checkIds).toEqual(["G01", "G02", "F04"]);
  });

  it("an element narrowed to a level no sheet has is a 404 listing where it IS", async () => {
    const err = await planner
      .planReview(projectId, userId, request({ target: { type: "element", value: "C-6", level: "9" } }))
      .catch((e) => e);
    expect(err.status).toBe(404);
    expect(err.details.choices.length).toBeGreaterThan(0);
  });

  it("an unknown mark is a 404", async () => {
    const err = await planner
      .planReview(projectId, userId, request({ target: { type: "element", value: "C-77" } }))
      .catch((e) => e);
    expect(err.status).toBe(404);
  });

  it("stores the model, limits and planning spend on the run, tagged in the ledger", async () => {
    const { recordUsage } = await import("./usage.js");
    const { retrieveChunkIds } = await import("./retrieval.js");
    const run = await planner.planReview(projectId, userId, request({ provider: "claude", model: "claude-haiku-4-5", maxInputTokens: 50_000, maxThinkingTokens: 4096 }), {
      retrieve: async (p, q, o) => {
        await recordUsage(p, "embedding", "voyage-3", { inputTokens: 1_000 });
        return retrieveChunkIds(p, q, o);
      },
    });
    const dto = planner.toReviewDto(run, 0);
    expect(dto).toMatchObject({ provider: "claude", model: "claude-haiku-4-5" });
    expect(dto.limits).toMatchObject({ maxInputTokens: 50_000, maxThinkingTokens: 4096, thinkingEffort: "medium" });
    const rows = await prisma.usageEvent.findMany({ where: { reviewRunId: run.id } });
    expect(rows.length).toBe(dto.checkIds.length);
    expect(rows.every((r) => r.stage === "planning")).toBe(true);
    expect(dto.estimate?.planningTokens).toBe(1_000 * rows.length);
    expect(dto.estimate?.planningCostUsd).toBeGreaterThan(0);
    expect(dto.coverage.searchLog).toHaveLength(dto.checkIds.length);
  });

  it("refuses a thinking token limit on a model that takes only an effort", async () => {
    const err = await planner
      .planReview(projectId, userId, request({ provider: "gemini", model: "gemini-3.6-flash", maxThinkingTokens: 4096 }))
      .catch((e) => e);
    expect(err.status).toBe(400);
    expect(err.message).toMatch(/thinking token limit/);
    const unknown = await planner
      .planReview(projectId, userId, request({ provider: "claude", model: "claude-typo-9" }))
      .catch((e) => e);
    expect(unknown.status).toBe(400);
  });

  it("a new plan replaces the person's earlier unstarted one", async () => {
    const first = await planner.planReview(projectId, userId, request());
    const second = await planner.planReview(projectId, userId, request());
    const statuses = await prisma.rfiReviewRun.findMany({
      where: { id: { in: [first.id, second.id] } },
      select: { id: true, status: true },
    });
    expect(Object.fromEntries(statuses.map((r) => [r.id, r.status]))).toEqual({
      [first.id]: "cancelled",
      [second.id]: "planned",
    });
  });

  it("a removed page stays out of the re-plan", async () => {
    const run = await planner.planReview(projectId, userId, request({ excludePageIds: [page.schedule!] }));
    expect(planner.toReviewDto(run, 0).pages.map((p) => p.pageId)).not.toContain(page.schedule);
  });

  it("compares two named sheets as two labelled sides", async () => {
    const run = await planner.planReview(
      projectId,
      userId,
      request({ target: { type: "compare", values: ["S2.105", "A3.01"] } }),
    );
    const dto = planner.toReviewDto(run, 0);
    expect(dto.pages.some((p) => p.role === "correspondence")).toBe(false);
    expect(dto.pages.slice(0, 2).map((p) => [p.pageId, p.role])).toEqual([
      [page.target, "side:0"],
      [page.arch, "side:1"],
    ]);
  });

  it("an unknown sheet is a 404 that lists the sheets that do exist", async () => {
    const err = await planner
      .planReview(projectId, userId, request({ target: { type: "sheet", value: "S9.99" } }))
      .catch((e) => e);
    expect(err).toBeInstanceOf(planner.PlanError);
    expect(err.status).toBe(404);
    expect(err.details.knownSheets).toEqual(expect.arrayContaining(["S2.105", "S6.01", "A3.01"]));
    expect(err.details.knownSheets).not.toContain("S6.02");
  });
});
