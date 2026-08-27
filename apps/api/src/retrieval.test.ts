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
