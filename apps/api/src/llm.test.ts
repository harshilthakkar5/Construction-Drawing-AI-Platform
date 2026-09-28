import { afterEach, describe, expect, it } from "vitest";
import {
  CHAT_THINKING_HEADROOM,
  chatAvailable,
  chatModel,
  chatProvider,
  chatThinking,
  claudeThinkingParam,
  geminiThinkingLadder,
  refusesThinking,
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
    // Asserted on the RATE, not on a cost computed with outputTokens: 0. That
    // older form compared input rates alone and called them "different
    // pricing" — so it went red the day Sonnet 5 was corrected to its real $2
    // and happened to meet gemini-3.1-pro-preview's $2 input. The models are
    // still priced from different rows; only the input halves coincide, which
    // is exactly what a cost at zero output tokens cannot tell apart.
    const gemini = rateFor(DEFAULT_CHAT_GEMINI_MODEL);
    const sonnet = rateFor("claude-sonnet-5");
    expect(gemini.input).toBeGreaterThan(0);
    expect(gemini).not.toEqual(sonnet);

    const row = {
      inputTokens: 1_000_000,
      outputTokens: 1_000_000,
      cacheReadTokens: 0,
      cacheWriteTokens: 0,
    };
    expect(estimateCostUsd({ ...row, model: DEFAULT_CHAT_GEMINI_MODEL })).not.toBe(
      estimateCostUsd({ ...row, model: "claude-sonnet-5" }),
    );
  });

  it("prices Sonnet 5 at its published rate, not Sonnet 4.6's", () => {
    // The regression this file now guards: claude-sonnet-5 sat at $3/$15 —
    // Sonnet 4.6's price — so every chat answer, summary and drawing
    // description the dashboard priced was overstated by half.
    expect(rateFor("claude-sonnet-5")).toEqual({ input: 2, output: 10 });
  });

  it("falls back on the sonnet family rather than the $3/$15 default", () => {
    expect(rateFor("claude-sonnet-9-unreleased")).toEqual({ input: 2, output: 10 });
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
    // The example must be a version nobody will ever add to RATES. This test
    // previously used gemini-3.5-flash-lite and stopped testing the fallback
    // the day that model got a real rate — it passed by exact match while the
    // behaviour it guards went unchecked.
    expect(rateFor("gemini-9.5-flash-lite")).toEqual(rateFor("gemini-2.5-flash-lite"));
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

describe("chat thinking is sent, never left to the model's default", () => {
  it("defaults to off, and reads the vocabulary per call", () => {
    delete process.env.CHAT_THINKING;
    expect(chatThinking()).toBe("off");
    process.env.CHAT_THINKING = " Medium ";
    expect(chatThinking()).toBe("medium");
    process.env.CHAT_THINKING = "minimal";
    expect(chatThinking()).toBe("off");
    process.env.CHAT_THINKING = "maximum";
    expect(chatThinking()).toBe("off"); // a typo must not buy the top of the scale
  });

  it("Claude off is an explicit disabled — omission is ADAPTIVE on Sonnet 5", () => {
    expect(claudeThinkingParam("off")).toEqual({ type: "disabled" });
    expect(claudeThinkingParam("high")).toBeUndefined();
  });

  it("Gemini 3 off is the bottom LEVEL, with omission only as the last rung", () => {
    const ladder = geminiThinkingLadder("models/gemini-3.1-pro-preview", "off");
    expect(ladder[0]).toEqual({ thinkingLevel: "MINIMAL" });
    expect(ladder[1]).toEqual({ thinkingLevel: "LOW" }); // 3.1 Pro has no minimal
    expect(ladder[ladder.length - 1]).toBeUndefined();
  });

  it("Gemini 2.5 off is a zero budget", () => {
    expect(geminiThinkingLadder("gemini-2.5-flash", "off")[0]).toEqual({ thinkingBudget: 0 });
  });

  it("a raised setting starts at its own level", () => {
    expect(geminiThinkingLadder("gemini-3.6-flash", "medium")[0]).toEqual({ thinkingLevel: "MEDIUM" });
  });

  it("only a refusal of the thinking field itself is retried", () => {
    expect(refusesThinking({ status: 400, message: "thinking.type: disabled is not supported" })).toBe(true);
    expect(refusesThinking(new Error("400 INVALID_ARGUMENT: thinking_level MINIMAL not supported"))).toBe(true);
    expect(refusesThinking({ status: 429, message: "rate limited while thinking" })).toBe(false);
    expect(refusesThinking({ status: 400, message: "max_tokens too large" })).toBe(false);
  });

  it("reasoning gets headroom on top of the answer's cap", () => {
    expect(CHAT_THINKING_HEADROOM.off).toBe(0);
    expect(CHAT_THINKING_HEADROOM.high).toBeGreaterThan(CHAT_THINKING_HEADROOM.low);
  });
});
