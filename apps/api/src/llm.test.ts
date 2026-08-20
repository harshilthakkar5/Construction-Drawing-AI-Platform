import { afterEach, describe, expect, it } from "vitest";
import {
  chatAvailable,
  chatModel,
  chatProvider,
  DEFAULT_CHAT_GEMINI_MODEL,
  DEFAULT_CHAT_MODEL,
} from "./llm.js";
import { estimateCostUsd } from "./usage.js";

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
