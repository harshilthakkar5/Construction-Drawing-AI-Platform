import { prisma } from "./db.js";

/**
 * Token accounting. Every model call writes one usage_events row so the
 * dashboard can report spend per project and per stage without re-reading
 * provider bills. The worker writes the same table from Python
 * (workers/src/usage.py) — keep the column set in sync.
 *
 * Recording must never break the request that produced it: failures are
 * logged and swallowed.
 */
export type UsageKind = "chat" | "summary" | "classification" | "embedding";

export interface TokenCounts {
  inputTokens?: number;
  outputTokens?: number;
  cacheReadTokens?: number;
  cacheWriteTokens?: number;
}

export async function recordUsage(
  projectId: string | null,
  kind: UsageKind,
  model: string,
  tokens: TokenCounts,
): Promise<void> {
  try {
    await prisma.usageEvent.create({
      data: {
        projectId,
        kind,
        model,
        inputTokens: tokens.inputTokens ?? 0,
        outputTokens: tokens.outputTokens ?? 0,
        cacheReadTokens: tokens.cacheReadTokens ?? 0,
        cacheWriteTokens: tokens.cacheWriteTokens ?? 0,
      },
    });
  } catch (err) {
    console.warn("[usage] could not record token usage:", (err as Error).message);
  }
}

/**
 * Rough USD estimate for the dashboard. Per-million-token published rates;
 * cache reads bill at 10% of the input rate and cache writes at 125% (the
 * 5-minute ephemeral TTL this app uses). Unknown models fall back to the
 * Sonnet rate so a model swap never reports $0.
 */
const RATES: Record<string, { input: number; output: number }> = {
  // Anthropic (models used here: chat/summaries on Sonnet, sheet reads on Haiku)
  "claude-sonnet-5": { input: 3, output: 15 },
  "claude-opus-5": { input: 5, output: 25 },
  "claude-haiku-4-5": { input: 1, output: 5 },
  "claude-haiku-4-5-20251001": { input: 1, output: 5 },
  // Google (sheet-number reads when SHEET_PROVIDER=gemini). Published list
  // rates — check them against your own billing tier before trusting the
  // dashboard's spend figure for these.
  "gemini-2.5-flash": { input: 0.3, output: 2.5 },
  "gemini-2.0-flash": { input: 0.1, output: 0.4 },
  // Voyage embeddings bill input tokens only.
  "voyage-3": { input: 0.06, output: 0 },
  "voyage-3-large": { input: 0.18, output: 0 },
};
const DEFAULT_RATE = { input: 3, output: 15 };

export function estimateCostUsd(row: {
  model: string;
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  cacheWriteTokens: number;
}): number {
  const rate = RATES[row.model] ?? DEFAULT_RATE;
  return (
    (row.inputTokens * rate.input +
      row.cacheReadTokens * rate.input * 0.1 +
      row.cacheWriteTokens * rate.input * 1.25 +
      row.outputTokens * rate.output) /
    1_000_000
  );
}
