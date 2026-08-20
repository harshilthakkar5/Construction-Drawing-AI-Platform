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

export const CHAT_MODEL = process.env.CHAT_MODEL ?? "claude-sonnet-5";
/** Chat is the reasoning path, so the pro tier rather than flash. */
export const CHAT_GEMINI_MODEL = process.env.CHAT_GEMINI_MODEL ?? "gemini-2.5-pro";

/**
 * Read per call rather than captured at import, so flipping the env var takes
 * effect on the next request. An unrecognised value falls back to Claude: a
 * typo must not take chat offline.
 */
export function chatProvider(): Provider {
  const name = (process.env.CHAT_PROVIDER ?? "claude").trim().toLowerCase();
  return (PROVIDERS as readonly string[]).includes(name) ? (name as Provider) : "claude";
}

export function chatModel(): string {
  return chatProvider() === "gemini" ? CHAT_GEMINI_MODEL : CHAT_MODEL;
}

/** Whether the ACTIVE provider has a key. The 503 gate in routes/chat.ts. */
export function chatAvailable(): boolean {
  return Boolean(
    chatProvider() === "gemini" ? process.env.GEMINI_API_KEY : process.env.ANTHROPIC_API_KEY,
  );
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
}

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

  const response = await anthropicClient().messages.create({
    model: CHAT_MODEL,
    max_tokens: request.maxTokens,
    system: [{ type: "text", text: request.system, cache_control: { type: "ephemeral" } }],
    messages,
  });

  return {
    model: CHAT_MODEL,
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
  const response = await geminiClient().models.generateContent({
    model: CHAT_GEMINI_MODEL,
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
      maxOutputTokens: request.maxTokens,
      temperature: 0,
    },
  });

  const meta = response.usageMetadata;
  const cached = meta?.cachedContentTokenCount ?? 0;
  return {
    model: CHAT_GEMINI_MODEL,
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
