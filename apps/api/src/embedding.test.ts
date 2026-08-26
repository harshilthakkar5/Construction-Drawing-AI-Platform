import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  DEFAULT_EMBEDDING_MODELS,
  embedQuery,
  embeddingKeyEnv,
  embeddingModel,
  embeddingProvider,
  embeddingsAvailable,
} from "./embedding.js";
import { rateFor } from "./usage.js";

/**
 * The mirror of workers/tests/test_embedllm.py. Two things are worth pinning
 * on this side:
 *
 *   1. The switch itself — provider, model, and the key the 503 names.
 *   2. That the QUESTION is embedded as a question. Every provider takes a
 *      different word for it, and sending a query as if it were a document
 *      costs recall silently, with no error anywhere to read.
 */

vi.mock("./db.js", () => ({ prisma: { usageEvent: { create: vi.fn() } } }));

const saved = { ...process.env };
afterEach(() => {
  process.env = { ...saved };
  vi.unstubAllGlobals();
});

beforeEach(() => {
  process.env.VOYAGE_API_KEY = "voyage-key";
  process.env.COHERE_API_KEY = "cohere-key";
  process.env.GEMINI_API_KEY = "gemini-key";
  delete process.env.EMBEDDING_PROVIDER;
  delete process.env.EMBEDDING_MODEL;
  delete process.env.EMBEDDING_DIM;
  delete process.env.EMBED_SEND_DIMENSION;
});

/** Captures the outgoing request and replies with `payload`. */
function stubFetch(payload: unknown, ok = true) {
  const calls: { url: string; headers: Record<string, string>; body: any }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit) => {
      calls.push({
        url,
        headers: init.headers as Record<string, string>,
        body: JSON.parse(init.body as string),
      });
      return {
        ok,
        status: ok ? 200 : 429,
        json: async () => payload,
        text: async () => JSON.stringify(payload),
      } as Response;
    }),
  );
  return calls;
}

describe("embeddingProvider", () => {
  it("defaults to voyage when unset", () => {
    expect(embeddingProvider()).toBe("voyage");
    expect(embeddingModel()).toBe(DEFAULT_EMBEDDING_MODELS.voyage);
  });

  it("selects a provider, case-insensitively", () => {
    process.env.EMBEDDING_PROVIDER = " Cohere ";
    expect(embeddingProvider()).toBe("cohere");
    expect(embeddingModel()).toBe("embed-v4.0");
  });

  it("falls back rather than crashing on a typo", () => {
    process.env.EMBEDDING_PROVIDER = "cohre";
    expect(embeddingProvider()).toBe("voyage");
  });

  it("names the key the ACTIVE provider needs", () => {
    process.env.EMBEDDING_PROVIDER = "gemini";
    expect(embeddingKeyEnv()).toBe("GEMINI_API_KEY");
    delete process.env.GEMINI_API_KEY;
    expect(embeddingsAvailable()).toBe(false);
    // ...and is unaffected by another provider's key being present
    process.env.VOYAGE_API_KEY = "still-here";
    expect(embeddingsAvailable()).toBe(false);
  });

  it("takes an explicit model override", () => {
    process.env.EMBEDDING_PROVIDER = "voyage";
    process.env.EMBEDDING_MODEL = "voyage-3.5";
    expect(embeddingModel()).toBe("voyage-3.5");
  });
});

describe("embedQuery", () => {
  it("asks Voyage for a query embedding, without a dimension", async () => {
    const calls = stubFetch({ data: [{ embedding: [0.1, 0.2] }], usage: { total_tokens: 6 } });

    expect(await embedQuery("where are the shear walls?")).toEqual([0.1, 0.2]);
    expect(calls[0]!.url).toContain("/v1/embeddings");
    expect(calls[0]!.body.input_type).toBe("query");
    // voyage-3 rejects output_dimension outright.
    expect(calls[0]!.body).not.toHaveProperty("output_dimension");
  });

  it("sends output_dimension once EMBED_SEND_DIMENSION is on", async () => {
    process.env.EMBED_SEND_DIMENSION = "true";
    process.env.EMBEDDING_DIM = "512";
    const calls = stubFetch({ data: [{ embedding: [0.1] }] });

    await embedQuery("q");
    expect(calls[0]!.body.output_dimension).toBe(512);
  });

  it("reads Cohere's float key and uses its query input type", async () => {
    process.env.EMBEDDING_PROVIDER = "cohere";
    const calls = stubFetch({
      embeddings: { float: [[0.3, 0.4]] },
      meta: { billed_units: { input_tokens: 4 } },
    });

    expect(await embedQuery("q")).toEqual([0.3, 0.4]);
    expect(calls[0]!.url).toContain("/v2/embed");
    expect(calls[0]!.body.input_type).toBe("search_query");
    expect(calls[0]!.body.output_dimension).toBe(1024);
  });

  it("normalizes Gemini vectors, as the worker does for documents", async () => {
    process.env.EMBEDDING_PROVIDER = "gemini";
    const calls = stubFetch({ embedding: { values: [3, 4] } });

    // A Matryoshka truncation comes back unnormalized; (3,4) has length 5.
    expect(await embedQuery("q")).toEqual([0.6, 0.8]);
    expect(calls[0]!.url).toContain("gemini-embedding-001:embedContent");
    expect(calls[0]!.body.taskType).toBe("RETRIEVAL_QUERY");
    expect(calls[0]!.headers["x-goog-api-key"]).toBe("gemini-key");
  });

  it("respects a base-URL override so an offline run can stub the provider", async () => {
    process.env.VOYAGE_BASE_URL = "http://localhost:9999";
    const calls = stubFetch({ data: [{ embedding: [1] }] });

    await embedQuery("q");
    expect(calls[0]!.url).toBe("http://localhost:9999/v1/embeddings");
  });

  it("throws on a provider error rather than returning an empty vector", async () => {
    stubFetch({ message: "rate limited" }, false);
    await expect(embedQuery("q")).rejects.toThrow(/voyage embeddings failed: 429/);
  });

  it("throws when the reply carries no embedding", async () => {
    stubFetch({ data: [] });
    await expect(embedQuery("q")).rejects.toThrow(/no embedding/);
  });
});

describe("embedding rates", () => {
  it("prices every default model from the table, not the family fallback", () => {
    for (const model of Object.values(DEFAULT_EMBEDDING_MODELS)) {
      expect(rateFor(model).output).toBe(0);
      expect(rateFor(model).input).toBeLessThan(1);
    }
  });

  it("prices a batched embedding run at half the synchronous rate", () => {
    expect(rateFor("gemini-embedding-001-batch").input).toBeCloseTo(
      rateFor("gemini-embedding-001").input / 2,
    );
  });

  it("never prices a Gemini embedding at Gemini Pro's chat rate", () => {
    // "gemini-embedding-001" contains "gemini" — the family fallback order is
    // what keeps this from being off by an order of magnitude.
    expect(rateFor("gemini-embedding-002").input).toBeLessThan(1);
  });
});
