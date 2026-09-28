import Anthropic from "@anthropic-ai/sdk";
import { GoogleGenAI } from "@google/genai";
import type { TokenCounts } from "./usage.js";

/**
 * Provider-neutral chat transport: Claude or Gemini, chosen by CHAT_PROVIDER.
 *
 * The mirror of workers/src/llm.py, and it holds the same invariant. The
 * provider is a TRANSPORT detail: both are sent the same system prompt, the
 * same retrieved chunks and the same question, and both replies go through the
 * same `[chunk:<id>]` citation parser (apps/api/src/citations.ts) on the way
 * out. Swapping one for the other changes WHO answers and nothing about what
 * an answer is allowed to claim or cite — which is what makes the two directly
 * comparable, and what keeps FR-13's source-verification chain intact either
 * way.
 *
 *   CHAT_PROVIDER=claude  (default)  CHAT_MODEL        / ANTHROPIC_API_KEY
 *   CHAT_PROVIDER=gemini             CHAT_GEMINI_MODEL / GEMINI_API_KEY
 *
 * Prompt caching is the one place they genuinely differ: Anthropic needs an
 * explicit cache breakpoint on the retrieved-chunk block, Gemini caches
 * implicitly. The prompt CONTENT is identical, so this is packaging only.
 */
export type Provider = "claude" | "gemini";

const PROVIDERS: readonly Provider[] = ["claude", "gemini"] as const;

/**
 * Defaults, mirrored from the worker. The summary ones MUST match
 * workers/src/summarize.py (SUMMARY_MODEL / SUMMARY_GEMINI_MODEL): the worker
 * writes the summaries and this process only quotes what they will cost, so a
 * drift here shows a user one model's price for another model's work.
 */
export const DEFAULT_CHAT_MODEL = "claude-sonnet-5";
export const DEFAULT_CHAT_GEMINI_MODEL = "models/gemini-3.1-pro-preview";
export const DEFAULT_SUMMARY_MODEL = "claude-sonnet-5";
export const DEFAULT_SUMMARY_GEMINI_MODEL = "models/gemini-3.1-pro-preview";

/**
 * Every one of these reads its env var per call rather than capturing it at
 * import, so a config change takes effect on the next request — and so the
 * provider and the model can never disagree about which one is active.
 * An unrecognised provider falls back to Claude: a typo must not take a stage
 * offline.
 */
function resolveProvider(envVar: string): Provider {
  const name = (process.env[envVar] ?? "claude").trim().toLowerCase();
  return (PROVIDERS as readonly string[]).includes(name) ? (name as Provider) : "claude";
}

const envModel = (name: string, fallback: string) => process.env[name] || fallback;

export function chatProvider(): Provider {
  return resolveProvider("CHAT_PROVIDER");
}

export function chatModel(): string {
  return chatProvider() === "gemini"
    ? envModel("CHAT_GEMINI_MODEL", DEFAULT_CHAT_GEMINI_MODEL)
    : envModel("CHAT_MODEL", DEFAULT_CHAT_MODEL);
}

/**
 * Who writes the summaries. The work happens on the worker, not here — this
 * process reads the same env only so the cost estimate quotes the model that
 * will actually run.
 */
export function summaryProvider(): Provider {
  return resolveProvider("SUMMARY_PROVIDER");
}

export function summaryModel(): string {
  return summaryProvider() === "gemini"
    ? envModel("SUMMARY_GEMINI_MODEL", DEFAULT_SUMMARY_GEMINI_MODEL)
    : envModel("SUMMARY_MODEL", DEFAULT_SUMMARY_MODEL);
}

/** Mirrors summarize.USE_BATCH — both providers bill batched calls at 50%. */
export function summaryBatchEnabled(): boolean {
  return (process.env.SUMMARY_USE_BATCH ?? "false").toLowerCase() === "true";
}

export function keyFor(provider: Provider): "GEMINI_API_KEY" | "ANTHROPIC_API_KEY" {
  return provider === "gemini" ? "GEMINI_API_KEY" : "ANTHROPIC_API_KEY";
}

/** Whether the ACTIVE provider has a key. The 503 gate in routes/chat.ts. */
export function chatAvailable(): boolean {
  return Boolean(process.env[keyFor(chatProvider())]);
}

export interface Turn {
  role: "user" | "assistant";
  text: string;
}

export interface CompletionRequest {
  /** Frozen instructions, shared by every request for the process lifetime. */
  system: string;
  history: Turn[];
  /** The large, repeatable prefix (retrieved chunks) — cached where supported. */
  context: string;
  /** The volatile part, placed AFTER the cached prefix so it never busts it. */
  question: string;
  maxTokens: number;
}

export interface Completion {
  text: string;
  model: string;
  tokens: TokenCounts;
  /** The reply stopped at the output cap rather than finishing. The text is
   * whatever was written before the cut — possibly mid-sentence, possibly in
   * the middle of a `[chunk:` tag — and the caller must say so. */
  truncated: boolean;
}

/**
 * CHAT_THINKING — how much the chat model may reason before it answers.
 *
 * `off` by default, and SENT, never left out. Leaving the field out is not
 * "off" on either current default: Sonnet 5 runs ADAPTIVE thinking when the
 * `thinking` field is omitted, and from Gemini 3 an unspecified thinking level
 * is the TOP of the scale. Both spend that reasoning from the same output cap
 * as the answer, so a 1024-token answer on a transport that sent nothing was
 * cut off part-way — once in the middle of a citation, which put
 * "[chunk:8eb7546e-eb96-" in front of a reader. The worker learned this three
 * times (workers/src/llm.py); this is the chat side learning it once.
 */
export type ChatThinking = "off" | "low" | "medium" | "high";
const CHAT_THINKING_SETTINGS: readonly ChatThinking[] = ["off", "low", "medium", "high"];

export function chatThinking(): ChatThinking {
  const raw = (process.env.CHAT_THINKING ?? "").trim().toLowerCase();
  if (raw === "minimal" || raw === "none" || raw === "false" || raw === "0") return "off";
  return (CHAT_THINKING_SETTINGS as readonly string[]).includes(raw) ? (raw as ChatThinking) : "off";
}

/** Output room reasoning is allowed to spend on top of the answer's own cap —
 * mirrors the worker's `_CLAUDE_EFFORT_HEADROOM`. */
export const CHAT_THINKING_HEADROOM: Record<ChatThinking, number> = {
  off: 0,
  low: 1024,
  medium: 4096,
  high: 8192,
};

/** Gemini 3 and later take a LEVEL; earlier ones take a token budget. A
 * version sniff, like the worker's, because a list of model names expires. */
export function geminiTakesLevel(model: string): boolean {
  const found = /gemini-(\d+)/.exec(model);
  return found ? Number(found[1]) >= 3 : true;
}

/** Claude's `thinking` field for a setting, or undefined to omit it. */
export function claudeThinkingParam(setting: ChatThinking): { type: "disabled" } | undefined {
  // A raised setting is adaptive thinking — the model's default when the
  // field is absent — given headroom by the caller.
  return setting === "off" ? { type: "disabled" } : undefined;
}

/**
 * Gemini's thinking config, most-preferred first. A model that refuses a
 * rung (gemini-3.1-pro-preview has no `minimal`) is tried at the next; the
 * last rung is `undefined` — omit the field — which on a level-taking model
 * is the TOP of the scale, so it is reached only after everything else was
 * refused, and logged.
 */
export function geminiThinkingLadder(
  model: string,
  setting: ChatThinking,
): ({ thinkingLevel: string } | { thinkingBudget: number } | undefined)[] {
  if (!geminiTakesLevel(model)) {
    return setting === "off" ? [{ thinkingBudget: 0 }, undefined] : [undefined];
  }
  const levels = ["MINIMAL", "LOW", "MEDIUM", "HIGH"];
  const start = setting === "off" ? 0 : levels.indexOf(setting.toUpperCase());
  return [...levels.slice(start).map((thinkingLevel) => ({ thinkingLevel })), undefined];
}

/** Whether an API error is a model refusing the thinking field itself —
 * the only error a retry with a different thinking setting can cure. */
export function refusesThinking(err: unknown): boolean {
  const status = (err as { status?: number; code?: number })?.status ?? (err as { code?: number })?.code;
  const message = String((err as { message?: string })?.message ?? err);
  return (status === 400 || /INVALID_ARGUMENT|400/.test(message)) && /think/i.test(message);
}

/** The rung that worked, per (provider, model, setting), so a refusal costs
 * one extra request per process rather than one per question. */
const thinkingLatch = new Map<string, number>();

let anthropic: Anthropic | null = null;
function anthropicClient(): Anthropic {
  anthropic ??= new Anthropic({ baseURL: process.env.ANTHROPIC_BASE_URL || undefined });
  return anthropic;
}

let gemini: GoogleGenAI | null = null;
function geminiClient(): GoogleGenAI {
  gemini ??= new GoogleGenAI({ apiKey: process.env.GEMINI_API_KEY ?? "" });
  return gemini;
}

async function completeClaude(request: CompletionRequest): Promise<Completion> {
  const messages: Anthropic.MessageParam[] = [
    ...request.history.map((turn) => ({ role: turn.role, content: turn.text })),
    {
      role: "user" as const,
      // Prompt caching (Phase 5): the retrieved-chunk block is by far the
      // largest part of the prompt and repeats verbatim when the same or a
      // similar question is asked again (retrieval itself is Redis-cached, so
      // repeats serialize identically). The volatile question stays AFTER the
      // breakpoint so it never invalidates the cached prefix.
      content: [
        {
          type: "text" as const,
          text: request.context,
          cache_control: { type: "ephemeral" as const },
        },
        { type: "text" as const, text: request.question },
      ],
    },
  ];

  const model = chatModel();
  const setting = chatThinking();
  const key = `claude:${model}:${setting}`;
  const rungs = [claudeThinkingParam(setting), undefined];
  let rung = thinkingLatch.get(key) ?? 0;
  const send = (thinking: { type: "disabled" } | undefined) =>
    anthropicClient().messages.create({
      model,
      max_tokens: request.maxTokens + CHAT_THINKING_HEADROOM[setting],
      system: [{ type: "text", text: request.system, cache_control: { type: "ephemeral" } }],
      messages,
      ...(thinking ? { thinking } : {}),
    });
  let response: Anthropic.Message;
  for (;;) {
    try {
      response = await send(rungs[rung]);
      break;
    } catch (err) {
      if (rungs[rung] === undefined || !refusesThinking(err)) throw err;
      console.warn(
        `[chat] ${model} refused thinking=${JSON.stringify(rungs[rung])}; omitting it — on this ` +
          "model that is ADAPTIVE thinking, spent from the answer's own output cap",
      );
      rung += 1;
      thinkingLatch.set(key, rung);
    }
  }

  return {
    model,
    truncated: response.stop_reason === "max_tokens",
    text: response.content
      .filter((block): block is Anthropic.TextBlock => block.type === "text")
      .map((block) => block.text)
      .join("\n"),
    tokens: {
      inputTokens: response.usage.input_tokens,
      outputTokens: response.usage.output_tokens,
      cacheReadTokens: response.usage.cache_read_input_tokens ?? 0,
      cacheWriteTokens: response.usage.cache_creation_input_tokens ?? 0,
    },
  };
}

async function completeGemini(request: CompletionRequest): Promise<Completion> {
  const model = chatModel();
  const setting = chatThinking();
  const key = `gemini:${model}:${setting}`;
  const rungs = geminiThinkingLadder(model, setting);
  let rung = thinkingLatch.get(key) ?? 0;
  const send = (thinkingConfig: (typeof rungs)[number]) => geminiClient().models.generateContent({
    model,
    contents: [
      // Gemini calls the assistant "model"; the turns themselves are the same.
      ...request.history.map((turn) => ({
        role: turn.role === "assistant" ? "model" : "user",
        parts: [{ text: turn.text }],
      })),
      // No explicit breakpoint to place: context and question go in one turn,
      // in the same order, and Gemini caches the shared prefix on its own side.
      { role: "user", parts: [{ text: `${request.context}\n\n${request.question}` }] },
    ],
    config: {
      systemInstruction: request.system,
      maxOutputTokens: request.maxTokens + CHAT_THINKING_HEADROOM[setting],
      temperature: 0,
      ...(thinkingConfig ? { thinkingConfig: thinkingConfig as never } : {}),
    },
  });
  let response: Awaited<ReturnType<typeof send>>;
  for (;;) {
    try {
      response = await send(rungs[rung]);
      break;
    } catch (err) {
      if (rung >= rungs.length - 1 || !refusesThinking(err)) throw err;
      rung += 1;
      thinkingLatch.set(key, rung);
      const next = rungs[rung];
      (next ? console.warn : console.error)(
        `[chat] ${model} refused its thinking setting; retrying with ${JSON.stringify(next ?? "the field omitted")}` +
          (next ? "" : " — on a Gemini 3 model that is the TOP of the scale, spent from the answer's cap. Set CHAT_THINKING or CHAT_GEMINI_MODEL."),
      );
    }
  }

  const meta = response.usageMetadata;
  const cached = meta?.cachedContentTokenCount ?? 0;
  return {
    model,
    truncated: String(response.candidates?.[0]?.finishReason ?? "") === "MAX_TOKENS",
    text: response.text ?? "",
    tokens: {
      // promptTokenCount INCLUDES the cached tokens, but the dashboard bills
      // cache reads separately at a discount — subtract them or a cache hit
      // reports as MORE expensive than a miss.
      inputTokens: Math.max(0, (meta?.promptTokenCount ?? 0) - cached),
      outputTokens: meta?.candidatesTokenCount ?? 0,
      cacheReadTokens: cached,
      cacheWriteTokens: 0,
    },
  };
}

export async function complete(request: CompletionRequest): Promise<Completion> {
  return chatProvider() === "gemini" ? completeGemini(request) : completeClaude(request);
}