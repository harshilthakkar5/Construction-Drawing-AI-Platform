import { describe, expect, it } from "vitest";
import {
  fullScope,
  isNarrowScope,
  RFI_REVIEW_CHECK_IDS,
  recommendedChecks,
  suggestWiderScan,
  type RfiScanScopeDto,
} from "@cdip/shared";
import { asksQuestions, describeScope, normalizeScope, scopeInput, scopeOf } from "./rfiScanScope.js";

const sheets = { structural: 42, architectural: 61, mechanical: 9 };
const norm = (input: object) => normalizeScope(scopeInput.parse(input), sheets);

describe("a scan's scope", () => {
  it("defaults to everything, which is what every scan before this did", () => {
    const out = norm({});
    expect(out).toEqual({ scope: fullScope() });
    expect(isNarrowScope(fullScope())).toBe(false);
    expect(describeScope(null)).toBe("every discipline, every question");
  });

  it("puts 'all' second, so one comparison has one shape", () => {
    const out = norm({ disciplineA: "all", disciplineB: "structural", checkIds: ["G01"] });
    expect("scope" in out && [out.scope.disciplineA, out.scope.disciplineB]).toEqual(["structural", "all"]);
  });

  it("refuses a discipline with no sheets — it could only ever say 'no pairs'", () => {
    const out = norm({ disciplineA: "structural", disciplineB: "plumbing", checkIds: ["G01"] });
    expect(out).toEqual({ error: expect.stringContaining("no Plumbing sheets") });
  });

  it("keeps questions in catalogue order, once each", () => {
    const out = norm({ disciplineA: "structural", disciplineB: "architectural", checkIds: ["C01", "G01", "C01"] });
    expect("scope" in out && out.scope.checkIds).toEqual(["G01", "C01"]);
  });

  it("tidies the person's own questions and drops repeats and blanks", () => {
    const out = norm({
      disciplineA: "structural",
      disciplineB: "architectural",
      checkIds: [],
      customQuestions: ["  Do slab   openings match shafts? ", "", "do slab openings match shafts?", "Are stair cores aligned?"],
    });
    expect("scope" in out && out.scope.customQuestions).toEqual(["Do slab openings match shafts?", "Are stair cores aligned?"]);
  });

  it("refuses a scope that asks nothing", () => {
    expect(norm({ checkIds: [], customQuestions: ["   "], projectChecks: false })).toEqual({
      error: expect.stringContaining("at least one question"),
    });
    // Project checks alone are a valid scan: no AI comparison is prepared.
    const only = norm({ checkIds: [], projectChecks: true });
    expect("scope" in only && asksQuestions(only.scope)).toBe(false);
  });

  it("bounds the person's own questions", () => {
    expect(() => scopeInput.parse({ customQuestions: ["x".repeat(301)] })).toThrow(/300 characters/);
    expect(() => scopeInput.parse({ customQuestions: ["a", "b", "c", "d", "e", "f"] })).toThrow(/at most 5/);
  });

  it("reads a stored scope back, and nothing from a malformed one", () => {
    const out = norm({ disciplineA: "structural", disciplineB: "architectural", checkIds: ["G01", "C01"] });
    if (!("scope" in out)) throw new Error("expected a scope");
    expect(scopeOf(JSON.parse(JSON.stringify(out.scope)))).toEqual(out.scope);
    expect(scopeOf(null)).toBeNull();
    expect(scopeOf({ disciplineA: "nonsense" })).toBeNull();
    expect(describeScope(out.scope)).toBe("Structural vs Architectural · 2 questions");
  });
});

describe("recommended questions for a pair", () => {
  it("asks Structural and Architectural only what both draw", () => {
    expect(recommendedChecks("structural", "architectural")).toEqual(["G01", "G02", "FL01", "FL02", "FL03", "FL04", "C01", "C02"]);
  });

  it("asks a discipline compared with itself everything it draws", () => {
    expect(recommendedChecks("structural", "structural")).toEqual([...RFI_REVIEW_CHECK_IDS]);
  });

  it("asks a consultant discipline only grids and levels", () => {
    expect(recommendedChecks("mechanical", "architectural")).toEqual(["G01", "G02"]);
  });

  it("asks every question when either side is every discipline", () => {
    expect(recommendedChecks("all", "all")).toEqual([...RFI_REVIEW_CHECK_IDS]);
    expect(recommendedChecks("architectural", "all")).toContain("C01");
  });
});

describe("offering the full scan after a narrow one", () => {
  const narrow: RfiScanScopeDto = {
    disciplineA: "structural",
    disciplineB: "architectural",
    checkIds: ["G01"],
    customQuestions: [],
    projectChecks: false,
  };
  const scan = { status: "completed" as const, scope: narrow as RfiScanScopeDto | null, findings: 0, fullScanId: "f1" as string | null };
  const summary = {
    newFindings: 0, foundAgain: [], confirmsCode: [], possibleProblems: 0, rejected: 0, unclear: 0, notChecked: 0,
    unplaceable: 0, areas: { agree: 4 }, areasTotal: 4, pagesCompared: 2, pagesRead: 10,
  };
  const ready = { id: "f1", status: "ready" as const, pairs: [], summary };

  it("offers it when a finished comparison found nothing new", () => {
    expect(suggestWiderScan(scan, ready)).toBe("nothing_found");
  });

  it("offers it when there was no sheet pair to compare", () => {
    expect(suggestWiderScan(scan, { id: "f1", status: "planned", pairs: [], summary: null })).toBe("no_pairs");
  });

  it("offers it when only the code checks ran and found nothing", () => {
    expect(suggestWiderScan({ ...scan, fullScanId: null }, null)).toBe("nothing_found");
  });

  it("does not offer it when anything was found, or the run did not finish looking", () => {
    expect(suggestWiderScan({ ...scan, findings: 2 }, ready)).toBeNull();
    expect(suggestWiderScan(scan, { ...ready, summary: { ...summary, newFindings: 1 } })).toBeNull();
    expect(suggestWiderScan(scan, { ...ready, summary: { ...summary, foundAgain: [{ subject: "s", where: "w", fingerprint: null }] } })).toBeNull();
    expect(suggestWiderScan(scan, { ...ready, status: "partial" })).toBeNull();
    expect(suggestWiderScan(scan, { ...ready, id: "another plan" })).toBeNull();
    expect(suggestWiderScan({ ...scan, status: "running" }, ready)).toBeNull();
  });

  it("never offers it after a scan that already covered everything", () => {
    expect(suggestWiderScan({ ...scan, scope: fullScope() }, ready)).toBeNull();
    expect(suggestWiderScan({ ...scan, scope: null }, ready)).toBeNull();
  });
});
