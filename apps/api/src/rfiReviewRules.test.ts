import { describe, expect, it } from "vitest";
import { RFI_REVIEW_CHECK_IDS, RFI_REVIEW_DEPTHS } from "@cdip/shared";
import {
  checkQuery,
  comparability,
  costDifference,
  cropAnchors,
  elementFamily,
  estimateReview,
  levelOf,
  mentions,
  normalizeSheet,
  referencedSheets,
  sheetShape,
  rankScope,
  reviewIsActive,
  scopeHash,
  selectRfiChecks,
  staleReason,
  STALE_REVIEW_MS,
  type CandidateChunk,
  type CandidatePage,
  type EstimateSettings,
  type RankInput,
} from "./rfiReviewRules.js";

const page = (id: string, sheet: string, over: Partial<CandidatePage> = {}): CandidatePage => ({
  pageId: id,
  documentId: `doc-${id}`,
  pageNumber: 1,
  combinedPageNumber: 1,
  sheetNumber: sheet,
  discipline: null,
  pdfWidth: 2592,
  pdfHeight: 1728,
  ...over,
});
const chunk = (id: string, pageId: string, over: Partial<CandidateChunk> = {}): CandidateChunk => ({
  chunkId: id,
  pageId,
  kind: "text",
  text: `text ${id}`,
  bbox: { x: 10, y: 10, width: 100, height: 40 },
  tokenCount: 100,
  ...over,
});

describe("normalizeSheet", () => {
  it("treats separators and case as one sheet", () => {
    expect(normalizeSheet("a-3.27")).toBe(normalizeSheet("A3.27"));
    expect(normalizeSheet(" S2.105 ")).toBe("S2105");
  });
});

describe("selectRfiChecks", () => {
  it("auto always runs G01 and G02 and says why", () => {
    const { checkIds, reasons } = selectRfiChecks("TYPICAL COLUMN AT GRID", "auto");
    expect(checkIds.slice(0, 2)).toEqual(["G01", "G02"]);
    expect(reasons.G01).toMatch(/every plan/);
  });

  it("auto adds a family when the target names it, and leaves the rest out WITH a reason", () => {
    const { checkIds, reasons, plan } = selectRfiChecks("TYP. SHEAR WALL SW-3 AT CORE", "auto");
    expect(checkIds).toContain("C02");
    expect(reasons.C02).toMatch(/SHEAR WALL|CORE|SW-/);
    expect(checkIds).not.toContain("F04");
    expect(plan.F04).toMatchObject({ selected: false, applicability: "unknown" });
    expect(plan.F04!.reason).toMatch(/Custom|All original/);
  });

  it("auto runs every objective when the target names no family at all", () => {
    const { checkIds, plan } = selectRfiChecks("S2.105", "auto");
    expect(checkIds).toEqual(RFI_REVIEW_CHECK_IDS);
    expect(plan.B02!.reason).toMatch(/does not say/);
  });

  it("auto matches keywords as WORDS, not inside other words", () => {
    expect(selectRfiChecks("COLOR LEGEND MATERIAL SPC", "auto").plan.C01!.applicability).toBe("unknown");
    expect(selectRfiChecks("COLOR LEGEND MATERIAL SPC", "auto").plan.F03!.applicability).toBe("unknown");
    expect(selectRfiChecks("core wall", "auto").checkIds).toContain("C02");
  });

  it("an element mark seeds its family's checks", () => {
    const { checkIds, reasons } = selectRfiChecks("", "auto", [], "PC1");
    expect(checkIds).toEqual(["G01", "G02", "F04"]);
    expect(reasons.F04).toMatch(/pile/);
    expect(selectRfiChecks("", "auto", [], "C-6").checkIds).toEqual(["G01", "G02", "C01", "C03"]);
  });

  it("custom runs exactly what was asked, in catalogue order, and marks the rest not selected", () => {
    const { checkIds, plan } = selectRfiChecks("", "custom", ["C02", "G01"]);
    expect(checkIds).toEqual(["G01", "C02"]);
    expect(plan.G02).toMatchObject({ selected: false });
  });

  it("custom refuses an id the catalogue does not have, and an empty choice", () => {
    expect(() => selectRfiChecks("", "custom", ["G01", "X99"])).toThrow(/X99/);
    expect(() => selectRfiChecks("", "custom", [])).toThrow(/at least one/);
  });

  it("all_original runs all 16 and still reports applicability", () => {
    const { checkIds, plan } = selectRfiChecks("BEAM FRAMING", "all_original");
    expect(checkIds).toEqual(RFI_REVIEW_CHECK_IDS);
    expect(plan.B01!.applicability).toBe("applicable");
    expect(plan.F03!.applicability).toBe("unknown");
    expect(Object.keys(plan)).toHaveLength(16);
  });
});

describe("mentions", () => {
  it("needs a word edge, except after a mark prefix", () => {
    expect(mentions("SEE COL SCHEDULE", "COL")).toBe(true);
    expect(mentions("COLOR", "COL")).toBe(false);
    expect(mentions("AT C-6", "C-")).toBe(true);
    expect(mentions("ABC-6", "C-")).toBe(false);
    expect(mentions("SLOPE 2%", "%")).toBe(true);
  });
});

describe("elementFamily", () => {
  it("reads the family off the mark's shape", () => {
    expect(elementFamily("C-6")!.family).toBe("column");
    expect(elementFamily("PC1")!.family).toBe("pile or pile cap");
    expect(elementFamily("WF2")!.family).toBe("wall footing");
    expect(elementFamily("F3")!.family).toBe("footing");
    expect(elementFamily("SW-3")!.family).toBe("shear or core wall");
    expect(elementFamily("B12")!.family).toBe("beam");
    expect(elementFamily("SR-4")).toBeNull();
    expect(elementFamily("DOOR")).toBeNull();
  });
});

describe("levelOf and sheetShape", () => {
  it("finds the level a title names", () => {
    expect(levelOf("LEVEL 14 FLOOR PLAN")).toBe("LEVEL 14");
    expect(levelOf("level 5 forming plan")).toBe("LEVEL 5");
    expect(levelOf("FOUNDATION PLAN")).toBeNull();
  });

  it("gives sheets of one numbering the same shape and a column mark another", () => {
    expect(sheetShape("A335")).toBe(sheetShape("A327"));
    expect(sheetShape("C6")).not.toBe(sheetShape("S2105"));
  });
});

describe("referencedSheets", () => {
  const sheets = new Map([
    ["S2105", ["p1"]],
    ["A301", ["p2"]],
    ["S6001", ["p3"]],
  ]);

  it("resolves the sheets the target names, most-mentioned first, and never the target itself", () => {
    const got = referencedSheets(new Map([["A301", 1], ["S6001", 3], ["S2105", 9]]), sheets, new Set(["S2105"]));
    expect(got.resolved.map((r) => r.sheet)).toEqual(["S6001", "A301"]);
  });

  it("calls a sheet-shaped identifier that is not in the project unresolved, and a column mark nothing", () => {
    const got = referencedSheets(new Map([["S5001", 1], ["C6", 4]]), sheets, new Set());
    expect(got.unresolved).toEqual(["S5001"]);
  });
});

describe("checkQuery", () => {
  it("puts the sheet numbers first so the exact-identifier arm sees them", () => {
    expect(checkQuery("C01", ["S2.105", "A3.01"]).startsWith("S2.105 A3.01 column")).toBe(true);
  });

  it("every catalogue check has a query", () => {
    for (const id of RFI_REVIEW_CHECK_IDS) expect(checkQuery(id, []).length).toBeGreaterThan(5);
  });
});

function input(over: Partial<RankInput> = {}): RankInput {
  const t = page("t", "S2.105");
  const r1 = page("r1", "S6.01");
  const r2 = page("r2", "A3.01");
  const chunks = new Map<string, CandidateChunk>([
    ["h1", chunk("h1", "r1")],
    ["h2", chunk("h2", "r2")],
    ["t1", chunk("t1", "t")],
  ]);
  return {
    depth: "standard",
    sides: [{ label: "S2.105", pages: [t] }],
    targetChunks: [chunk("t1", "t"), chunk("tg", "t", { kind: "gridmarks" }), chunk("ts", "t", { kind: "summary" })],
    hitLists: [["h1", "t1", "h2"]],
    chunks,
    pages: new Map([
      ["r1", r1],
      ["r2", r2],
    ]),
    ...over,
  };
}

describe("rankScope", () => {
  it("keeps the target page and its measured geometry whatever the ranking says", () => {
    const scope = rankScope(input({ hitLists: [] }));
    expect(scope.pages.map((p) => [p.pageId, p.role])).toEqual([["t", "target"]]);
    expect(scope.chunks.map((c) => c.chunkId)).toContain("tg");
  });

  it("never lets a kind outside the evidence hierarchy in", () => {
    expect(rankScope(input()).chunks.map((c) => c.chunkId)).not.toContain("ts");
  });

  it("adds the pages retrieval found, best-ranked first", () => {
    const scope = rankScope(input());
    expect(scope.pages.map((p) => [p.pageId, p.role])).toEqual([
      ["t", "target"],
      ["r1", "related"],
      ["r2", "related"],
    ]);
  });

  it("caps chunks by rank, not by arrival order, and still keeps the target geometry", () => {
    const many = new Map<string, CandidateChunk>();
    const list: string[] = [];
    for (let i = 0; i < 100; i++) {
      many.set(`h${i}`, chunk(`h${i}`, "r1"));
      list.push(`h${i}`);
    }
    const scope = rankScope(input({ chunks: many, hitLists: [list] }));
    const cap = RFI_REVIEW_DEPTHS.standard.chunks;
    expect(scope.chunks.length).toBe(cap);
    const ids = scope.chunks.map((c) => c.chunkId);
    expect(ids).toContain("tg");
    expect(ids).toContain("h0");
    expect(ids).not.toContain("h99");
  });

  it("renders target pages always and related pages only while room remains", () => {
    const pages = new Map<string, CandidatePage>();
    const chunks = new Map<string, CandidateChunk>();
    const list: string[] = [];
    for (let i = 0; i < 12; i++) {
      pages.set(`r${i}`, page(`r${i}`, `S${i}`));
      chunks.set(`h${i}`, chunk(`h${i}`, `r${i}`));
      list.push(`h${i}`);
    }
    const scope = rankScope(input({ pages, chunks, hitLists: [list] }));
    const visual = scope.pages.filter((p) => p.visual);
    expect(visual.length).toBe(RFI_REVIEW_DEPTHS.standard.visualPages);
    expect(visual[0]!.pageId).toBe("t");
    expect(scope.pages.filter((p) => !p.visual).length).toBeGreaterThan(0);
  });

  it("keeps each side of a comparison labelled apart", () => {
    const a = page("a", "S2.105");
    const b = page("b", "A3.01");
    const scope = rankScope(
      input({
        sides: [
          { label: "S2.105", pages: [a] },
          { label: "A3.01", pages: [b] },
        ],
        targetChunks: [],
        hitLists: [],
      }),
    );
    expect(scope.sides).toEqual([
      { label: "S2.105", pageIds: ["a"] },
      { label: "A3.01", pageIds: ["b"] },
    ]);
    expect(scope.pages.map((p) => p.role)).toEqual(["side:0", "side:1"]);
  });
});

describe("rankScope — forced pages, element pages and omissions", () => {
  it("keeps a forced reference page with its reason, whatever retrieval ranked", () => {
    const ref = page("ref", "S6.01");
    const scope = rankScope(input({ forced: [{ page: ref, role: "reference", reason: "the target refers to S6.01" }], hitLists: [] }));
    const got = scope.pages.find((p) => p.pageId === "ref")!;
    expect(got).toMatchObject({ role: "reference", visual: true, reason: "the target refers to S6.01" });
  });

  it("puts an element's pages in the scope and its mark's chunks first", () => {
    const e = page("e", "S2.105");
    const scope = rankScope(
      input({
        sides: [],
        elementPages: [e],
        targetChunks: [chunk("m", "e"), chunk("other", "e")],
        elementChunkIds: new Set(["m"]),
        hitLists: [],
      }),
    );
    expect(scope.pages[0]).toMatchObject({ pageId: "e", role: "element" });
    expect(scope.pages[0]!.crops[0]!.chunkId).toBe("m");
  });

  it("says which matched pages the cap left out, and why", () => {
    const many = new Map<string, CandidateChunk>();
    const pages = new Map<string, CandidatePage>();
    const list: string[] = [];
    for (let i = 0; i < 80; i++) {
      many.set(`h${i}`, chunk(`h${i}`, `p${i}`));
      pages.set(`p${i}`, page(`p${i}`, `S${i}`));
      list.push(`h${i}`);
    }
    const scope = rankScope(input({ chunks: many, pages, hitLists: [list], hitChecks: ["C01"] }));
    const capped = scope.omitted.filter((o) => /cap/.test(o.reason));
    expect(capped.length).toBeGreaterThan(0);
    expect(capped[0]!.reason).toMatch(/C01/);
    expect(scope.omitted.some((o) => /text only/.test(o.reason))).toBe(true);
  });

  it("gives every related page a reason naming the check that found it", () => {
    const scope = rankScope(input({ hitChecks: ["C01"] }));
    expect(scope.pages.find((p) => p.pageId === "r1")!.reason).toMatch(/C01/);
  });
});

describe("cropAnchors", () => {
  it("crops around the best chunks with a box, never a grid-extent or boxless chunk", () => {
    const anchors = cropAnchors(
      [
        { ...chunk("a", "t"), score: 0.1 },
        { ...chunk("b", "t"), score: 0.3 },
        { ...chunk("g", "t", { kind: "gridmarks" }), score: 0.9 },
        { ...chunk("n", "t", { bbox: null }), score: 0.8 },
      ],
      3,
    );
    expect(anchors.map((a) => a.chunkId)).toEqual(["b", "a"]);
  });
});

describe("scopeHash", () => {
  const scope = rankScope(input());
  const base = scopeHash(scope, ["G01", "C01"], "standard");

  it("is stable for the same scope, whatever order the checks were listed in", () => {
    expect(scopeHash(scope, ["C01", "G01"], "standard")).toBe(base);
  });

  it("changes when the checks, the pages or the chunks change", () => {
    expect(scopeHash(scope, ["G01"], "standard")).not.toBe(base);
    expect(scopeHash({ ...scope, pages: scope.pages.slice(1) }, ["G01", "C01"], "standard")).not.toBe(base);
    expect(scopeHash({ ...scope, chunks: scope.chunks.slice(1) }, ["G01", "C01"], "standard")).not.toBe(base);
  });

  it("does not depend on the order a JSON column hands the settings back in", () => {
    expect(scopeHash(scope, ["G01"], "standard", { model: "a", limits: { b: 1, a: 2 } })).toBe(
      scopeHash(scope, ["G01"], "standard", { limits: { a: 2, b: 1 }, model: "a" }),
    );
  });

  it("changes when the model or limits change", () => {
    expect(scopeHash(scope, ["G01", "C01"], "standard", { model: "a" })).not.toBe(scopeHash(scope, ["G01", "C01"], "standard", { model: "b" }));
  });
});

const settings = (over: Partial<EstimateSettings> = {}): EstimateSettings => ({
  provider: "claude",
  model: "claude-sonnet-5",
  depth: "standard",
  maxInputTokens: 120_000,
  thinkingEffort: "medium",
  maxThinkingTokens: null,
  checkCount: 3,
  pricingVersion: "test rates",
  priced: true,
  planning: { tokens: 12, costUsd: 0.001 },
  ...over,
});

describe("estimateReview", () => {
  it("prices stored token counts and images as a range", () => {
    const scope = rankScope(input());
    const est = estimateReview(scope, settings(), (row) => row.inputTokens / 1e6 + row.outputTokens / 1e5);
    expect(est.modelCalls).toBe(3);
    expect(est.costLowUsd!).toBeLessThan(est.costUsd!);
    expect(est.costHighUsd!).toBeGreaterThan(est.costUsd!);
    expect(est).toMatchObject({ model: "claude-sonnet-5", provider: "claude", pricingVersion: "test rates", planningTokens: 12 });
    const visual = scope.pages.filter((p) => p.visual);
    const pairs = visual.length >= 2 ? 2 * RFI_REVIEW_DEPTHS.standard.pairWindows : 0;
    expect(est.imageParts).toBe(visual.reduce((n, p) => n + 1 + p.crops.length, 0) + pairs);
  });

  it("leaves an unknown price unknown rather than quoting a guess", () => {
    const est = estimateReview(rankScope(input()), settings({ priced: false, planning: { tokens: 0, costUsd: null } }), () => 1);
    expect(est.costUsd).toBeNull();
    expect(est.costLowUsd).toBeNull();
    expect(est.costHighUsd).toBeNull();
    expect(est.planningCostUsd).toBeNull();
    expect(est.pricingVersion).toMatch(/unknown/);
  });

  it("a thinking ceiling moves the high end, not the low end", () => {
    const cost = (row: { outputTokens: number }) => row.outputTokens;
    const low = estimateReview(rankScope(input()), settings({ maxThinkingTokens: 1024 }), cost);
    const high = estimateReview(rankScope(input()), settings({ maxThinkingTokens: 32_000 }), cost);
    expect(high.costHighUsd!).toBeGreaterThan(low.costHighUsd!);
    expect(high.costLowUsd).toBe(low.costLowUsd);
  });

  it("splits discovery into more calls when the input limit is small, up to the depth's cap", () => {
    const big = new Map<string, CandidateChunk>();
    const list: string[] = [];
    for (let i = 0; i < 60; i++) {
      big.set(`h${i}`, chunk(`h${i}`, "r1", { tokenCount: 3000 }));
      list.push(`h${i}`);
    }
    const scope = rankScope(input({ chunks: big, hitLists: [list] }));
    const wide = estimateReview(scope, settings({ maxInputTokens: 400_000 }), () => 0);
    const narrow = estimateReview(scope, settings({ maxInputTokens: 20_000 }), () => 0);
    expect(wide.modelCalls).toBe(3);
    expect(narrow.modelCalls).toBe(RFI_REVIEW_DEPTHS.standard.maxBatches + 2);
    expect(narrow.assumptions.join(" ")).toMatch(/partial/);
  });

  it("prices side-by-side pairs only when two sheets are rendered", () => {
    const one = estimateReview(rankScope(input({ hitLists: [] })), settings(), () => 0);
    expect(one.imageParts).toBe(1 + (rankScope(input({ hitLists: [] })).pages[0]?.crops.length ?? 0));
  });

  it("grows with the evidence it has to read", () => {
    const small = estimateReview(rankScope(input({ hitLists: [] })), settings(), () => 0);
    const large = estimateReview(rankScope(input()), settings(), () => 0);
    expect(large.inputTokens).toBeGreaterThan(small.inputTokens);
  });
});

describe("comparability", () => {
  const run = { target: { type: "sheet", value: "S2.105" }, checkIds: ["G01", "C01"], sourceRevisions: { d: 1 }, complete: true };

  it("says outright when two runs are like for like — and that cost says nothing about findings", () => {
    const got = comparability(run, { ...run, checkIds: ["C01", "G01"] });
    expect(got).toMatchObject({ sameTarget: true, sameChecks: true, sameRevisions: true, sameCoverage: true });
    expect(got.caveats[0]).toMatch(/says nothing/);
  });

  it("names each thing that differs", () => {
    const got = comparability(run, { ...run, sourceRevisions: { d: 2 }, complete: false });
    expect(got.caveats.join(" ")).toMatch(/drawings changed/);
    expect(got.caveats.join(" ")).toMatch(/partial/);
  });

  it("never shows a percentage of nothing, nor a difference with an unknown", () => {
    expect(costDifference(0, 1)).toEqual({ usd: 1, percent: null });
    expect(costDifference(null, 1)).toEqual({ usd: null, percent: null });
    expect(costDifference(2, 3)).toEqual({ usd: 1, percent: 50 });
  });
});

describe("staleReason", () => {
  const scope = rankScope(input());
  const docs = new Set(scope.pages.map((p) => p.documentId));
  const chunks = new Set(scope.chunks.map((c) => c.chunkId));

  it("is null while every document and chunk is still live", () => {
    expect(staleReason(scope, { documentIds: docs, chunkIds: chunks })).toBeNull();
  });

  it("names a revised or excluded document", () => {
    const gone = new Set([...docs].slice(1));
    expect(staleReason(scope, { documentIds: gone, chunkIds: chunks })).toMatch(/revised/);
  });

  it("names a document whose revision number moved", () => {
    const id = [...docs][0]!;
    expect(staleReason(scope, { documentIds: docs, chunkIds: chunks, revisions: new Map([[id, 2]]) }, { [id]: 1 })).toMatch(/new revision/);
  });

  it("names re-processed evidence", () => {
    expect(staleReason(scope, { documentIds: docs, chunkIds: new Set() })).toMatch(/re-processed/);
  });
});

describe("reviewIsActive", () => {
  const now = new Date("2026-09-29T12:00:00Z");
  const run = (status: "queued" | "running" | "ready", beat: number) => ({
    status,
    createdAt: new Date(now.getTime() - 3_600_000),
    startedAt: new Date(now.getTime() - 3_600_000),
    heartbeatAt: new Date(now.getTime() - beat),
  });

  it("is live while the heartbeat is fresh, however long the run has taken", () => {
    expect(reviewIsActive(run("running", 5_000), now)).toBe(true);
  });

  it("is dead once the heartbeat goes quiet", () => {
    expect(reviewIsActive(run("running", STALE_REVIEW_MS + 1), now)).toBe(false);
  });

  it("is never active once finished", () => {
    expect(reviewIsActive(run("ready", 0), now)).toBe(false);
  });
});
