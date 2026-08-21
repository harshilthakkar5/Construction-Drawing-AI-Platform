import { afterEach, describe, expect, it } from "vitest";
import {
  chatAvailable,
  chatModel,
  chatProvider,
  DEFAULT_CHAT_GEMINI_MODEL,
  DEFAULT_CHAT_MODEL,
} from "./llm.js";
import { estimateCostUsd, rateFor } from "./usage.js";

/**
 * The invariant these pin is the one workers/tests/test_llm.py pins on the
 * Python side: CHAT_PROVIDER changes who answers, never what an answer is
 * allowed to be. The prompt, the chunk serialization and the [chunk:id]
 * citation contract live in answer.ts and are shared by both transports, so
 * what remains testable here is the switch itself and its failure behaviour.
 */

const saved = { ...process.env };
afterEach(() => {
  process.env = { ...saved };
});

describe("chatProvider", () => {
  it("defaults to claude when unset", () => {
    delete process.env.CHAT_PROVIDER;
    expect(chatProvider()).toBe("claude");
  });

  it("selects gemini from the env var", () => {
    process.env.CHAT_PROVIDER = "gemini";
    expect(chatProvider()).toBe("gemini");
  });

  it("tolerates case and whitespace", () => {
    process.env.CHAT_PROVIDER = "  GEMINI ";
    expect(chatProvider()).toBe("gemini");
  });

  it("falls back rather than crashing on a typo", () => {
    // A bad env var must not take chat offline.
    process.env.CHAT_PROVIDER = "gpt4";
    expect(chatProvider()).toBe("claude");
  });

  it("is read per call, so a change takes effect without a restart", () => {
    process.env.CHAT_PROVIDER = "claude";
    expect(chatModel()).toBe(DEFAULT_CHAT_MODEL);
    process.env.CHAT_PROVIDER = "gemini";
    expect(chatModel()).toBe(DEFAULT_CHAT_GEMINI_MODEL);
  });

  it("the model override is read per call too, not frozen at import", () => {
    // The provider was resolved per call while the model was captured at
    // import, so a CHAT_MODEL set after startup was silently ignored.
    process.env.CHAT_PROVIDER = "claude";
    process.env.CHAT_MODEL = "claude-opus-5";
    expect(chatModel()).toBe("claude-opus-5");
  });
});

describe("chatAvailable", () => {
  it("checks the key the ACTIVE provider needs", () => {
    process.env.CHAT_PROVIDER = "gemini";
    process.env.GEMINI_API_KEY = "g";
    delete process.env.ANTHROPIC_API_KEY;
    expect(chatAvailable()).toBe(true);
  });

  it("is false when only the other provider's key is set", () => {
    // The 503 gate must not wave a request through to a provider that has no
    // key just because the unused one is configured.
    process.env.CHAT_PROVIDER = "gemini";
    process.env.ANTHROPIC_API_KEY = "a";
    delete process.env.GEMINI_API_KEY;
    expect(chatAvailable()).toBe(false);
  });
});

describe("cost estimation covers both vendors", () => {
  it("prices a Gemini model from its own rate, not the Sonnet fallback", () => {
    const row = {
      inputTokens: 1_000_000,
      outputTokens: 0,
      cacheReadTokens: 0,
      cacheWriteTokens: 0,
    };
    const gemini = estimateCostUsd({ ...row, model: DEFAULT_CHAT_GEMINI_MODEL });
    const sonnet = estimateCostUsd({ ...row, model: "claude-sonnet-5" });
    expect(gemini).toBeGreaterThan(0);
    expect(gemini).not.toBe(sonnet);
  });

  it("a cache hit is never dearer than a miss", () => {
    // Gemini reports cached tokens INSIDE promptTokenCount; llm.ts subtracts
    // them before recording, so a hit must come out cheaper here.
    const miss = estimateCostUsd({
      model: DEFAULT_CHAT_GEMINI_MODEL,
      inputTokens: 1000,
      outputTokens: 0,
      cacheReadTokens: 0,
      cacheWriteTokens: 0,
    });
    const hit = estimateCostUsd({
      model: DEFAULT_CHAT_GEMINI_MODEL,
      inputTokens: 0,
      outputTokens: 0,
      cacheReadTokens: 1000,
      cacheWriteTokens: 0,
    });
    expect(hit).toBeLessThan(miss);
  });
});


describe("rates for models not in the table", () => {
  /**
   * Model names move faster than the rate table. Pricing an unknown
   * `gemini-*-flash-lite` at Sonnet's $3/$15 was wrong by more than an order of
   * magnitude — in the dialog whose only job is saying what a run will cost.
   */
  it("prices an unknown flash-lite as a flash-lite, not a frontier model", () => {
    expect(rateFor("gemini-3.5-flash-lite")).toEqual(rateFor("gemini-2.5-flash-lite"));
  });

  it("flash-lite wins over flash — longest match first", () => {
    expect(rateFor("gemini-9-flash-lite").input).toBeLessThan(
      rateFor("gemini-9-flash").input,
    );
  });

  it("an unknown gemini pro tier is priced as a gemini pro tier", () => {
    expect(rateFor("gemini-3.1-pro-preview")).toEqual(rateFor("gemini-2.5-pro"));
  });

  it("an exact entry always beats the family fallback", () => {
    expect(rateFor("gemini-2.5-flash")).toEqual({ input: 0.3, output: 2.5 });
  });

  it("something wholly unrecognised still costs more than nothing", () => {
    const cost = estimateCostUsd({
      model: "some-new-vendor-model",
      inputTokens: 1_000_000,
      outputTokens: 0,
      cacheReadTokens: 0,
      cacheWriteTokens: 0,
    });
    expect(cost).toBeGreaterThan(0);
  });

  it("an unknown model is never quoted as free", () => {
    // The original reason for a fallback at all: $0.00 in the dialog reads as
    // "this is free", which is the one answer that is never true.
    for (const model of ["gemini-3.1-pro-preview", "claude-something-new"]) {
      expect(rateFor(model).input).toBeGreaterThan(0);
    }
  });
});
