import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import {
  RFI_EVENT_KINDS,
  RFI_PRIORITIES,
  RFI_STATUSES,
  type RfiPriority,
  type RfiStatus,
} from "./src/index.js";

/**
 * The RFI vocabulary is declared twice — as a Postgres enum in schema.prisma
 * and as a union here, which the API and the web client both branch on. A
 * value missing from the enum is an INSERT that fails; a value missing from
 * the union is a status the UI cannot render and a filter that silently drops
 * rows. Neither announces itself, so this does.
 *
 * This is the same check `workers/tests/test_usage.py` makes over `UsageKind`,
 * and it exists for the same reason: that enum and its union HAD drifted
 * (`rerank` was in one and not the other), and the cost was reranker spend
 * reaching the dashboard with no label, for as long as the feature had existed.
 */
const SCHEMA = fileURLToPath(new URL("../../apps/api/prisma/schema.prisma", import.meta.url));

/** The members of a Prisma enum block, ignoring comments and blank lines. */
function enumMembers(name: string): string[] {
  const source = readFileSync(SCHEMA, "utf8");
  const block = new RegExp(`enum ${name} \\{(.*?)\\}`, "s").exec(source);
  if (!block) throw new Error(`enum ${name} not found in schema.prisma`);
  return block[1]
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.length > 0 && !line.startsWith("//"));
}

describe("RfiStatus", () => {
  it("matches the Prisma enum exactly", () => {
    expect(enumMembers("RfiStatus").sort()).toEqual([...RFI_STATUSES].sort());
  });

  it("has a union that is assignable from every declared value", () => {
    // Catches the half of a drift the runtime array cannot: RFI_STATUSES could
    // be widened without widening the type it is annotated with.
    const statuses: RfiStatus[] = RFI_STATUSES;
    expect(statuses).toContain("open");
  });

  /**
   * "void" is what the construction industry calls a withdrawn RFI, and it is
   * also a TypeScript keyword. The enum spells it `voided` in both languages;
   * a rename back would compile in Prisma and break here.
   */
  it("spells the withdrawn state in a way both languages can hold", () => {
    expect(RFI_STATUSES).toContain("voided");
    expect(RFI_STATUSES).not.toContain("void");
  });
});

describe("RfiPriority", () => {
  it("matches the Prisma enum exactly", () => {
    expect(enumMembers("RfiPriority").sort()).toEqual([...RFI_PRIORITIES].sort());
  });

  it("has a union that is assignable from every declared value", () => {
    const priorities: RfiPriority[] = RFI_PRIORITIES;
    expect(priorities).toContain("normal");
  });
});

describe("RFI_EVENT_KINDS", () => {
  /**
   * The counterpart assertion to the two above, and the reason they are not
   * one test: `rfi_events.kind` is deliberately TEXT and NOT an enum, because
   * the vocabulary grows with every phase that touches an RFI. If someone
   * "fixes" that asymmetry by adding an enum, this fails and says why.
   */
  it("is not mirrored by a Prisma enum, on purpose", () => {
    const source = readFileSync(SCHEMA, "utf8");
    expect(
      /enum RfiEventKind \{/.test(source),
      "rfi_events.kind is TEXT so the vocabulary can grow without a migration — " +
        "if an enum is genuinely wanted, delete this test and add a drift check like RfiStatus's",
    ).toBe(false);
  });

  it("carries the lifecycle events the log is built from", () => {
    for (const kind of ["created", "status_changed", "answered"]) {
      expect(RFI_EVENT_KINDS).toContain(kind);
    }
  });
});
