import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("./db.js", () => ({ prisma: { usageEvent: { create: vi.fn() } } }));

const complete = vi.fn();
vi.mock("./llm.js", async (original) => ({
  ...(await original<typeof import("./llm.js")>()),
  complete: (...args: unknown[]) => complete(...args),
}));

import { answerFromChunks, maxAnswerTokens, TRUNCATED_NOTE } from "./answer.js";

/**
 * An answer that stopped at the output cap used to reach the reader as if it
 * were whole — ending mid-sentence, once mid-citation. It must say it is
 * incomplete, and the cap itself must be large enough for the answers the
 * vision pass makes possible (a grid line's worth of cited intersections).
 */

const saved = { ...process.env };
afterEach(() => {
  process.env = { ...saved };
  complete.mockReset();
  vi.restoreAllMocks();
});

const reply = (text: string, truncated: boolean) => ({
  text,
  truncated,
  model: "claude-sonnet-5",
  tokens: { inputTokens: 1, outputTokens: 1, cacheReadTokens: 0, cacheWriteTokens: 0 },
});

describe("a truncated answer", () => {
  it("says so to the reader", async () => {
    vi.spyOn(console, "warn").mockImplementation(() => {});
    complete.mockResolvedValue(reply("6/B column C-5. The rest are in", true));
    const text = await answerFromChunks("what is on line 6?", [], []);
    expect(text.endsWith(TRUNCATED_NOTE)).toBe(true);
  });

  it("a whole answer carries no note", async () => {
    complete.mockResolvedValue(reply("6/B column C-5.", false));
    expect(await answerFromChunks("q", [], [])).toBe("6/B column C-5.");
  });
});

describe("maxAnswerTokens", () => {
  it("defaults above the 1024 that cut grid answers off", () => {
    delete process.env.CHAT_MAX_TOKENS;
    expect(maxAnswerTokens()).toBe(2048);
  });

  it("follows CHAT_MAX_TOKENS and refuses nonsense", () => {
    process.env.CHAT_MAX_TOKENS = "4096";
    expect(maxAnswerTokens()).toBe(4096);
    process.env.CHAT_MAX_TOKENS = "12";
    expect(maxAnswerTokens()).toBe(2048);
    process.env.CHAT_MAX_TOKENS = "lots";
    expect(maxAnswerTokens()).toBe(2048);
  });

  it("is what the transport is asked for", async () => {
    process.env.CHAT_MAX_TOKENS = "3000";
    complete.mockResolvedValue(reply("ok", false));
    await answerFromChunks("q", [], []);
    expect(complete.mock.calls[0]?.[0].maxTokens).toBe(3000);
  });
});
