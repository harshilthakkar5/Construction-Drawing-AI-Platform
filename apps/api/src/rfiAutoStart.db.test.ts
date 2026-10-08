import { randomUUID } from "node:crypto";
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * maybeAutoStart against a real, MIGRATED database — skipped unless
 * RFI_TEST_DATABASE_URL is set. The pure decision is covered in
 * rfiFullScanRules.test.ts; what only SQL shows is that a decision is taken
 * once, that a start is the same conditional claim the Start button makes
 * (so two callers start one run), and that nothing starts before the
 * code-check scan has saved the findings the run reads.
 */
const url = process.env.RFI_TEST_DATABASE_URL;

const added: unknown[] = [];
vi.mock("./queues.js", () => ({ rfiFullScanQueue: { add: async (...args: unknown[]) => void added.push(args) } }));
vi.mock("./redis.js", () => ({ redis: { duplicate: () => ({}) } }));

describe.skipIf(!url)("one-click step 2 against a real database", () => {
  let auto: typeof import("./rfiAutoStart.js");
  let prisma: typeof import("./db.js")["prisma"];
  const projectId = randomUUID();
  const userId = randomUUID();
  const estimate = {
    calls: 4,
    images: 8,
    inputTokens: 20_000,
    outputTokens: 2_000,
    verifyCalls: { low: 1, high: 2 },
    verifyInputTokens: { low: 5_000, high: 10_000 },
    verifyOutputTokens: { low: 500, high: 1_000 },
  };

  beforeAll(async () => {
    process.env.DATABASE_URL = url;
    process.env.REDIS_URL ??= "redis://127.0.0.1:1";
    process.env.QDRANT_URL ??= "http://127.0.0.1:1";
    process.env.RFI_PROVIDER = "claude";
    process.env.ANTHROPIC_API_KEY = "test-key";
    ({ prisma } = await import("./db.js"));
    auto = await import("./rfiAutoStart.js");
    await prisma.$executeRawUnsafe(
      `INSERT INTO users (id, email, name, "passwordHash") VALUES ($1, $2, 'auto test', 'x')`,
      userId,
      `${userId}@test.invalid`,
    );
    await prisma.$executeRawUnsafe(`INSERT INTO projects (id, name, "ownerId") VALUES ($1, 'auto test', $2)`, projectId, userId);
  });

  afterAll(async () => {
    await prisma?.project.deleteMany({ where: { id: projectId } });
    await prisma?.user.deleteMany({ where: { id: userId } });
  });

  beforeEach(() => void (added.length = 0));

  async function plan(limitUsd: number, codeScan: "completed" | "running" = "completed") {
    const scan = await prisma.rfiFullScan.create({
      data: { projectId, createdById: userId, status: "planned", estimate, autoStart: { limitUsd } },
    });
    for (const [i, status] of ["pending", "pending", "skipped"].entries()) {
      await prisma.rfiFullScanTile.create({ data: { scanId: scan.id, pairIndex: 0, tileIndex: i, windows: {}, status } });
    }
    await prisma.rfiScan.create({ data: { projectId, requestedById: userId, status: codeScan, fullScanId: scan.id } });
    return scan.id;
  }

  it("starts a plan under the limit exactly once, with the limit as its budget", async () => {
    const id = await plan(1000);
    await Promise.all([auto.maybeAutoStart(id), auto.maybeAutoStart(id), auto.maybeAutoStart(id)]);
    const scan = await prisma.rfiFullScan.findUniqueOrThrow({ where: { id } });
    expect(scan.status).toBe("queued");
    expect(scan.useBatch).toBe(true);
    expect((scan.limits as { budgetUsd: number }).budgetUsd).toBe(1000);
    expect((scan.autoStart as { decision: string }).decision).toBe("started");
    expect(scan.idempotencyKey).toBe(`auto-${id}`);
    expect(added).toHaveLength(1);
  });

  it("leaves a plan over the limit waiting, and says why", async () => {
    const id = await plan(0.0001);
    await auto.maybeAutoStart(id);
    const scan = await prisma.rfiFullScan.findUniqueOrThrow({ where: { id } });
    expect(scan.status).toBe("planned");
    expect(scan.autoStart).toMatchObject({ decision: "over_limit", limitUsd: 0.0001 });
    expect(added).toHaveLength(0);
  });

  it("does nothing until the code-check scan has finished", async () => {
    const id = await plan(1000, "running");
    await auto.maybeAutoStart(id);
    const scan = await prisma.rfiFullScan.findUniqueOrThrow({ where: { id } });
    expect(scan.status).toBe("planned");
    expect((scan.autoStart as { decision?: string }).decision).toBeUndefined();
    expect(added).toHaveLength(0);
  });
});
