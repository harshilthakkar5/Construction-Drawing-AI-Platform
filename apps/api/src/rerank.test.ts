import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./db.js", () => ({ prisma: { usageEvent: { create: vi.fn() } } }));

import {
  DEFAULT_RERANK_MODELS,
  rerank,
  rerankCandidates,
  rerankEnabled,
  rerankModel,
  rerankProvider,
} from "./rerank.js";
import { rateFor } from "./usage.js";

/**
 * The reranker is the last thing to touch the order before the model reads it,
 * so two properties matter more than the ranking itself: it must never fail a
 * question, and it must never mis-attribute a rank to the wrong chunk.
 */

const saved = { ...process.env };
afterEach(() => {
  process.env = { ...saved };
  vi.unstubAllGlobals();
});

beforeEach(() => {
  process.env.COHERE_API_KEY = "cohere-key";
  process.env.VOYAGE_API_KEY = "voyage-key";
  delete process.env.RERANK_PROVIDER;
  delete process.env.RERANK_MODEL;
  delete process.env.RERANK_CANDIDATES;
});

function stubFetch(payload: unknown, ok = true) {
  const calls: { url: string; body: any }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit) => {
      calls.push({ url, body: JSON.parse(init.body as string) });
      return {
        ok,
        status: ok ? 200 : 500,
        json: async () => payload,
        text: async () => JSON.stringify(payload),
      } as Response;
    }),
  );
  return calls;
}

const docs = [
  { id: "a", text: "first chunk" },
  { id: "b", text: "second chunk" },
  { id: "c", text: "third chunk" },
];

describe("configuration", () => {
  it("is off unless asked for — it costs latency and money on every question", () => {
    expect(rerankProvider()).toBe("none");
    expect(rerankModel()).toBeNull();
    expect(rerankEnabled()).toBe(false);
  });

  it("selects a provider and its default model", () => {
    process.env.RERANK_PROVIDER = "cohere";
    expect(rerankModel()).toBe(DEFAULT_RERANK_MODELS.cohere);
    process.env.RERANK_PROVIDER = "voyage";
    expect(rerankModel()).toBe(DEFAULT_RERANK_MODELS.voyage);
  });

  it("stays off when the provider has no key, rather than failing questions", () => {
    process.env.RERANK_PROVIDER = "cohere";
    delete process.env.COHERE_API_KEY;
    expect(rerankProvider()).toBe("cohere");
    expect(rerankEnabled()).toBe(false);
  });

  it("falls back to off on a typo", () => {
    process.env.RERANK_PROVIDER = "coher";
    expect(rerankProvider()).toBe("none");
  });

  it("bounds the candidate pool", () => {
    expect(rerankCandidates()).toBe(60);
    process.env.RERANK_CANDIDATES = "500";
    expect(rerankCandidates()).toBe(200);
    process.env.RERANK_CANDIDATES = "nonsense";
    expect(rerankCandidates()).toBe(60);
  });
});

describe("rerank", () => {
  it("returns the provider's order, not the input order", async () => {
    process.env.RERANK_PROVIDER = "cohere";
    const calls = stubFetch({
      results: [
        { index: 2, relevance_score: 0.9 },
        { index: 0, relevance_score: 0.4 },
      ],
    });

    expect(await rerank("q", docs, 3)).toEqual(["c", "a"]);
    expect(calls[0]!.url).toContain("/v2/rerank");
    expect(calls[0]!.body.documents).toEqual(["first chunk", "second chunk", "third chunk"]);
    expect(calls[0]!.body.model).toBe("rerank-v3.5");
  });

  it("reads Voyage's differently-shaped reply", async () => {
    process.env.RERANK_PROVIDER = "voyage";
    const calls = stubFetch({ data: [{ index: 1, relevance_score: 0.8 }] });

    expect(await rerank("q", docs, 3)).toEqual(["b"]);
    expect(calls[0]!.url).toContain("/v1/rerank");
    expect(calls[0]!.body.top_k).toBe(3);
  });

  it("honours the requested cut", async () => {
    process.env.RERANK_PROVIDER = "cohere";
    stubFetch({
      results: [
        { index: 0, relevance_score: 0.9 },
        { index: 1, relevance_score: 0.8 },
        { index: 2, relevance_score: 0.7 },
      ],
    });
    expect(await rerank("q", docs, 2)).toEqual(["a", "b"]);
  });

  it("discards an out-of-range index rather than mis-attributing a rank", async () => {
    // An index past the end would otherwise throw, or worse, silently shift
    // one chunk's rank onto another.
    process.env.RERANK_PROVIDER = "cohere";
    stubFetch({
      results: [
        { index: 99, relevance_score: 0.9 },
        { index: 1, relevance_score: 0.8 },
      ],
    });
    expect(await rerank("q", docs, 3)).toEqual(["b"]);
  });

  it("returns null on a provider error, so the caller keeps its order", async () => {
    process.env.RERANK_PROVIDER = "cohere";
    stubFetch({ message: "boom" }, false);
    expect(await rerank("q", docs, 3)).toBeNull();
  });

  it("returns null when the network throws", async () => {
    process.env.RERANK_PROVIDER = "cohere";
    vi.stubGlobal("fetch", vi.fn(async () => {
      throw new Error("ECONNRESET");
    }));
    expect(await rerank("q", docs, 3)).toBeNull();
  });

  it("never calls the provider when disabled or given nothing", async () => {
    const calls = stubFetch({ results: [] });
    expect(await rerank("q", docs, 3)).toBeNull(); // provider none
    process.env.RERANK_PROVIDER = "cohere";
    expect(await rerank("q", [], 3)).toBeNull(); // no documents
    expect(calls).toHaveLength(0);
  });
});

describe("rerank pricing", () => {
  it("prices a Cohere search at its per-search rate, not per token", () => {
    // rerank.ts records one search as one unit; the table is per-million-units,
    // so $2000/M x 1 = $0.002 — Cohere's actual per-search price.
    expect(rateFor("rerank-v3.5").input / 1_000_000).toBeCloseTo(0.002);
  });

  it("prices Voyage per token", () => {
    expect(rateFor("rerank-2.5").input).toBeLessThan(1);
  });
});
