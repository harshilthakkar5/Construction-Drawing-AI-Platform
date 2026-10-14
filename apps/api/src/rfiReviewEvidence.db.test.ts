import { randomUUID } from "node:crypto";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

/**
 * What a question needs besides the target, planned against a real MIGRATED
 * database (skipped without RFI_TEST_DATABASE_URL). The controlled case is
 * UT Law RFI 006: the forming plan defers every opening dimension to the
 * "ARCHITECTURAL SLAB DIMENSIONAL CONTROL PLAN" by title. Uploaded, the plan
 * must load it; absent, it must say exactly why — never review on and call
 * the openings fine.
 */
const url = process.env.RFI_TEST_DATABASE_URL;

describe.skipIf(!url)("planReview: the evidence each question needs", () => {
  let planner: typeof import("./rfiReviewPlanner.js");
  let prisma: typeof import("./db.js")["prisma"];
  const userId = randomUUID();
  const projects: string[] = [];

  async function project(): Promise<string> {
    const id = randomUUID();
    projects.push(id);
    await prisma.$executeRawUnsafe(`INSERT INTO projects (id, name) VALUES ($1, 'review evidence test')`, id);
    const doc = randomUUID();
    await prisma.$executeRawUnsafe(
      `INSERT INTO documents (id, "projectId", filename, "spacesKey", pages, status) VALUES ($1, $2, 'set.pdf', 'k', 1, 'completed')`,
      doc,
      id,
    );
    return doc;
  }

  async function sheet(doc: string, n: number, sheetNumber: string, discipline: string, kind: string, level: string | null, texts: string[]) {
    const id = randomUUID();
    await prisma.$executeRawUnsafe(
      `INSERT INTO pages (id, "documentId", "pageNumber", "combinedPageNumber", "sheetNumber", "sheetRegionText", discipline, "sheetKind", level)
       VALUES ($1, $2, $3, $3, $4, $4, $5, $6, $7)`,
      id,
      doc,
      n,
      sheetNumber,
      discipline,
      kind,
      level,
    );
    for (const text of texts) {
      await prisma.$executeRawUnsafe(
        `INSERT INTO chunks (id, "pageId", text, bbox, "tokenCount", kind)
         VALUES ($1, $2, $3, '{"x":100,"y":100,"width":80,"height":20}'::jsonb, 30, 'text')`,
        randomUUID(),
        id,
        text,
      );
    }
    return id;
  }

  const FORMING_NOTE =
    "2. REFER TO ARCHITECTURAL SLAB DIMENSIONAL CONTROL PLAN FOR ALL SLAB EDGE AND OPENING DIMENSIONS NOT SHOWN, TYPICAL.";
  const request = {
    target: { type: "sheet" as const, value: "S2.102" },
    checkMode: "custom" as const,
    checkIds: ["O01", "F04"] as ("O01" | "F04")[],
    depth: "standard" as const,
    thinking: "medium" as const,
    excludePageIds: [],
  };

  beforeAll(async () => {
    process.env.DATABASE_URL = url;
    process.env.REDIS_URL ??= "redis://127.0.0.1:1";
    process.env.QDRANT_URL ??= "http://127.0.0.1:1";
    ({ prisma } = await import("./db.js"));
    planner = await import("./rfiReviewPlanner.js");
    await prisma.$executeRawUnsafe(
      `INSERT INTO users (id, email, name, "passwordHash") VALUES ($1, $2, 'evidence test', 'x')`,
      userId,
      `${userId}@test.invalid`,
    );
  });

  afterAll(async () => {
    if (!prisma) return;
    await prisma.$executeRawUnsafe(`DELETE FROM projects WHERE id = ANY($1::text[])`, projects);
    await prisma.$executeRawUnsafe(`DELETE FROM users WHERE id = $1`, userId);
    await prisma.$disconnect();
  });

  it("loads a drawing the notes defer to by title, on the target's level", async () => {
    const doc = await project();
    await sheet(doc, 1, "S2.102", "structural", "plan", "LEVEL 2", [FORMING_NOTE, "SLAB OPENING AT GRID 3/C"]);
    const other = await sheet(doc, 2, "A2.11", "architectural", "plan", "LEVEL 3", ["SLAB DIMENSIONAL CONTROL PLAN - LEVEL 3"]);
    const wanted = await sheet(doc, 3, "A2.10", "architectural", "plan", "LEVEL 2", ["SLAB DIMENSIONAL CONTROL PLAN - LEVEL 2"]);
    await sheet(doc, 4, "S5.100", "structural", "schedule", null, ["PILE CAP SCHEDULE PC1 PC2"]);
    await sheet(doc, 5, "A3.01", "architectural", "plan", "LEVEL 2", ["FLOOR PLAN - LEVEL 2"]);
    // RFI 006's own shape: a comparison, which adds no same-level sheets — so
    // only the title reference can bring the control plan in.
    const run = await planner.planReview(projects.at(-1)!, userId, {
      ...request,
      target: { type: "compare", values: ["S2.102", "A3.01"] },
    });
    const dto = planner.toReviewDto(run, 0);
    const forced = dto.pages.find((p) => p.pageId === wanted);
    expect(forced?.reason).toMatch(/S2\.102 refers to the architectural slab dimensional control plan by title/);
    // The other level's control plan may come back from search, never as the reference.
    expect(dto.pages.find((p) => p.pageId === other)?.role).not.toBe("reference");
    expect(dto.coverage.evidenceGaps?.filter((g) => g.checkId === null)).toEqual([]);
  });

  it("names the exact reason when that drawing is not in the project", async () => {
    const doc = await project();
    await sheet(doc, 1, "S2.102", "structural", "plan", "LEVEL 2", [FORMING_NOTE, "SLAB OPENING AT GRID 3/C"]);
    await sheet(doc, 2, "A1.00", "architectural", "cover", null, ["SHEET INDEX A2.10 SLAB DIMENSIONAL CONTROL PLAN - LEVEL 2"]);
    const run = await planner.planReview(projects.at(-1)!, userId, request);
    const dto = planner.toReviewDto(run, 0);
    expect(dto.coverage.evidenceGaps).toContainEqual({
      checkId: null,
      need: "architectural slab dimensional control plan",
      reason: "S2.102 refers to it; the sheet index lists it as A2.10, which is not in this project",
    });
    // F04 needs a schedule and the project has none: a gap, never silence.
    expect(dto.coverage.evidenceGaps).toContainEqual({ checkId: "F04", need: "schedule", reason: "the project has no schedule sheet" });
    expect(dto.notes.join(" ")).toMatch(/refers to the architectural slab dimensional control plan: the sheet index lists it as A2\.10/);
  });
});
