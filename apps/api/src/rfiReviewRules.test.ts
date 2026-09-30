import { describe, expect, it } from "vitest";
import { RFI_REVIEW_CHECK_IDS, RFI_REVIEW_DEPTHS } from "@cdip/shared";
import {
  checkQuery,
  cropAnchors,
  estimateReview,
  normalizeSheet,
  rankScope,
  reviewIsActive,
  scopeHash,
  selectRfiChecks,
  staleReason,
  STALE_REVIEW_MS,
  type CandidateChunk,
  type CandidatePage,
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
  it("auto runs the keyword-free checks on every plan", () => {
    const { checkIds, reasons } = selectRfiChecks("LEVEL 5 FORMING PLAN", "auto");
    expect(checkIds).toEqual(["G01", "C01"]);
    expect(reasons.G01).toMatch(/every plan/);
  });

  it("auto adds a keyworded check when the target says it applies, and says why", () => {
    const { checkIds, reasons } = selectRfiChecks("TYP. SHEAR WALL SW-3 AT CORE", "auto");
    expect(checkIds).toContain("C02");
    expect(reasons.C02).toMatch(/SHEAR WALL|CORE|SW-/);
  });

  it("auto is case-insensitive about the target's words", () => {
    expect(selectRfiChecks("core wall", "auto").checkIds).toContain("C02");
  });

  it("custom runs exactly what was asked, in catalogue order", () => {
    expect(selectRfiChecks("", "custom", ["C02", "G01"]).checkIds).toEqual(["G01", "C02"]);
  });

  it("custom refuses an id the catalogue does not have, and an empty choice", () => {
    expect(() => selectRfiChecks("", "custom", ["G01", "X99"])).toThrow(/X99/);
    expect(() => selectRfiChecks("", "custom", [])).toThrow(/at least one/);
  });

  it("every catalogue check can be chosen", () => {
    expect(selectRfiChecks("", "custom", RFI_REVIEW_CHECK_IDS).checkIds).toEqual(RFI_REVIEW_CHECK_IDS);
  });
});

describe("checkQuery", () => {
  it("puts the sheet numbers first so the exact-identifier arm sees them", () => {
    expect(checkQuery("C01", ["S2.105", "A3.01"]).startsWith("S2.105 A3.01 column")).toBe(true);
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
});

describe("estimateReview", () => {
  it("prices stored token counts and images, over three calls", () => {
    const scope = rankScope(input());
    const seen: { model: string; inputTokens: number }[] = [];
    const est = estimateReview(scope, "claude-sonnet-5", (row) => {
      seen.push(row);
      return 0.42;
    });
    expect(est.modelCalls).toBe(3);
    expect(est.costUsd).toBe(0.42);
    expect(est.model).toBe("claude-sonnet-5");
    const visual = scope.pages.filter((p) => p.visual);
    const pairs = visual.length >= 2 ? 2 * RFI_REVIEW_DEPTHS.standard.pairWindows : 0;
    expect(est.imageParts).toBe(visual.reduce((n, p) => n + 1 + p.crops.length, 0) + pairs);
    expect(seen[0]!.inputTokens).toBe(est.inputTokens);
  });

  it("prices side-by-side pairs only when two sheets are rendered", () => {
    const one = estimateReview(rankScope(input({ hitLists: [] })), "m", () => 0);
    expect(one.imageParts).toBe(1 + (rankScope(input({ hitLists: [] })).pages[0]?.crops.length ?? 0));
    const two = estimateReview(rankScope(input()), "m", () => 0);
    const visual = rankScope(input()).pages.filter((p) => p.visual);
    expect(visual.length).toBeGreaterThanOrEqual(2);
    expect(two.imageParts - visual.reduce((n, p) => n + 1 + p.crops.length, 0)).toBe(
      2 * RFI_REVIEW_DEPTHS.standard.pairWindows,
    );
  });

  it("grows with the evidence it has to read", () => {
    const small = estimateReview(rankScope(input({ hitLists: [] })), "m", () => 0);
    const large = estimateReview(rankScope(input()), "m", () => 0);
    expect(large.inputTokens).toBeGreaterThan(small.inputTokens);
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
