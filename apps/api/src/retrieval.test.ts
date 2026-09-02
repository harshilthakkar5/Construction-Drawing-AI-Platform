import { afterEach, describe, expect, it, vi } from "vitest";

// The module reaches Qdrant, Postgres and the embedding provider at import
// time through env.ts; the ranking rule under test needs none of them.
vi.mock("./qdrant.js", () => ({ searchChunks: vi.fn() }));
vi.mock("./db.js", () => ({ prisma: {} }));
vi.mock("./embedding.js", () => ({ embedQuery: vi.fn() }));

import { hybridEnabled, reciprocalRankFusion } from "./retrieval.js";

/**
 * Fusion is where hybrid retrieval either works or quietly makes things worse,
 * so it is a pure function tested without a database: the SQL half is exercised
 * against Postgres, the ranking rule is pinned here.
 */

const saved = { ...process.env };
afterEach(() => {
  process.env = { ...saved };
});

describe("reciprocalRankFusion", () => {
  it("puts a chunk both methods found above one only either found", () => {
    const dense = ["a", "b", "c"];
    const keyword = ["c", "d", "e"];
    // c is 3rd and 1st; a is 1st and absent. 1/61+1/63 > 1/61.
    expect(reciprocalRankFusion([dense, keyword], 5)[0]).toBe("c");
  });

  it("keeps a strong single-list result ahead of weak ones", () => {
    const dense = ["a", "b", "c", "d"];
    const keyword = ["e", "f", "g", "h"];
    const fused = reciprocalRankFusion([dense, keyword], 4);
    // Top of each list interleaves ahead of the tails.
    expect(fused.slice(0, 2).sort()).toEqual(["a", "e"]);
  });

  it("is a no-op ranking when one half returns nothing", () => {
    expect(reciprocalRankFusion([["a", "b", "c"], []], 3)).toEqual(["a", "b", "c"]);
    expect(reciprocalRankFusion([[], ["x", "y"]], 3)).toEqual(["x", "y"]);
  });

  it("never returns a duplicate", () => {
    const fused = reciprocalRankFusion([["a", "b"], ["a", "b"], ["b", "a"]], 10);
    expect(fused).toHaveLength(2);
    expect(new Set(fused).size).toBe(2);
  });

  it("respects the limit", () => {
    const many = Array.from({ length: 40 }, (_, i) => `c${i}`);
    expect(reciprocalRankFusion([many, many], 18)).toHaveLength(18);
  });

  it("is deterministic when scores tie", () => {
    const first = reciprocalRankFusion([["b"], ["a"]], 2);
    const second = reciprocalRankFusion([["b"], ["a"]], 2);
    expect(first).toEqual(second);
  });

  it("handles no results at all", () => {
    expect(reciprocalRankFusion([[], []], 18)).toEqual([]);
  });
});

describe("hybridEnabled", () => {
  it("is on by default", () => {
    delete process.env.HYBRID_RETRIEVAL;
    expect(hybridEnabled()).toBe(true);
  });

  it("falls back to dense-only when switched off", () => {
    process.env.HYBRID_RETRIEVAL = "false";
    expect(hybridEnabled()).toBe(false);
  });

  it("treats anything else as on, rather than taking retrieval offline", () => {
    process.env.HYBRID_RETRIEVAL = "yes please";
    expect(hybridEnabled()).toBe(true);
  });
});

describe("weighted fusion (the identifier arm)", () => {
  it("treats a bare array as weight 1, unchanged from before", () => {
    const plain = reciprocalRankFusion([["a", "b"], ["b", "c"]], 5);
    const weighted = reciprocalRankFusion(
      [{ ids: ["a", "b"], weight: 1 }, { ids: ["b", "c"], weight: 1 }],
      5,
    );
    expect(weighted).toEqual(plain);
  });

  it("lets an exact identifier hit outrank two similarity hits", () => {
    // "what is on S102A": dense and keyword agree on a plausible-looking
    // neighbour, the identifier arm alone found the sheet itself. The sheet
    // must win — that is the entire point of the third arm.
    const dense = ["neighbour", "other"];
    const keyword = ["neighbour", "other"];
    const identifiers = ["the-sheet"];
    const fused = reciprocalRankFusion(
      [
        { ids: dense, weight: 1 },
        { ids: keyword, weight: 1 },
        { ids: identifiers, weight: 3 },
      ],
      5,
    );
    expect(fused[0]).toBe("the-sheet");
  });

  it("does not let the weight bury everything else", () => {
    // The question usually asks something ABOUT the sheet, and that context
    // comes from the semantic arms — they must stay in the list.
    const fused = reciprocalRankFusion(
      [
        { ids: ["ctx1", "ctx2", "ctx3"], weight: 1 },
        { ids: ["ctx1", "ctx2"], weight: 1 },
        { ids: ["sheet"], weight: 3 },
      ],
      4,
    );
    expect(fused[0]).toBe("sheet");
    expect(fused).toContain("ctx1");
    expect(fused).toContain("ctx2");
  });

  it("ignores an arm that found nothing", () => {
    const fused = reciprocalRankFusion(
      [{ ids: ["a", "b"], weight: 1 }, { ids: [], weight: 3 }],
      5,
    );
    expect(fused).toEqual(["a", "b"]);
  });
});

describe("the identifier weight is arithmetic, not taste", () => {
  it("beats a two-arm consensus but loses to identifier + similarity agreeing", () => {
    const twoArmConsensus = reciprocalRankFusion(
      [
        { ids: ["neighbour"], weight: 1 },
        { ids: ["neighbour"], weight: 1 },
        { ids: ["sheet"], weight: 3 },
      ],
      2,
    );
    expect(twoArmConsensus[0]).toBe("sheet");

    // ...but a chunk the identifier arm AND a similarity arm both like is a
    // better answer still, and must stay on top.
    const bothAgree = reciprocalRankFusion(
      [
        { ids: ["sheet-and-relevant"], weight: 1 },
        { ids: [], weight: 1 },
        { ids: ["sheet-and-relevant", "sheet-only"], weight: 3 },
      ],
      2,
    );
    expect(bothAgree[0]).toBe("sheet-and-relevant");
  });
});
