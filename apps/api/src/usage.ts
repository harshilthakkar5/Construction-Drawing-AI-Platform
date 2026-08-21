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
 * 5-minute ephemeral TTL this app uses). Those two multipliers are Anthropic's;
 * Gemini's implicit caching discounts differently, so a Gemini row's cache
 * portion is an approximation — the input/output bulk of the figure is right.
 *
 * A model missing from the table falls back by FAMILY, not to one global
 * default: pricing a `gemini-*-flash-lite` run at Sonnet's $3/$15 was wrong by
 * more than an order of magnitude, and it is the "Generate summary" dialog —
 * the one screen whose whole job is telling someone what they are about to
 * spend — that showed the number. Model names move faster than this table, so
 * the fallback has to be roughly right rather than merely non-zero. Add the
 * real published rates for any model you actually run; the fallback is a
 * guardrail, not a substitute.
 */
const RATES: Record<string, { input: number; output: number }> = {
  // Anthropic (models used here: chat/summaries on Sonnet, sheet reads on Haiku)
  "claude-sonnet-5": { input: 3, output: 15 },
  "claude-opus-5": { input: 5, output: 25 },
  "claude-haiku-4-5": { input: 1, output: 5 },
  "claude-haiku-4-5-20251001": { input: 1, output: 5 },
  // Google (any stage running on Gemini: SHEET_PROVIDER / SUMMARY_PROVIDER /
  // CHAT_PROVIDER). Published list rates — check them against your own billing
  // tier before trusting the dashboard's spend figure for these.
  "gemini-2.5-pro": { input: 1.25, output: 10 },
  "gemini-2.5-flash": { input: 0.3, output: 2.5 },
  "gemini-2.5-flash-lite": { input: 0.1, output: 0.4 },
  "gemini-2.0-flash": { input: 0.1, output: 0.4 },
  // Newer Gemini models you run go here. Until a real rate is added, they are
  // priced by the family fallback below — close enough to keep the dialog
  // honest, not close enough to bill from.
  // Voyage embeddings bill input tokens only.
  "voyage-3": { input: 0.06, output: 0 },
  "voyage-3-large": { input: 0.18, output: 0 },
};
const DEFAULT_RATE = { input: 3, output: 15 };

/**
 * Family fallbacks for a model not in RATES, matched longest-prefix-first so
 * "flash-lite" wins over "flash". Each is the cheapest published rate in that
 * family: an under-estimate of an unknown model is a smaller lie than pricing
 * a flash tier as a frontier one, and the warning below says which happened.
 */
const FAMILY_RATES: ReadonlyArray<[string, { input: number; output: number }]> = [
  ["flash-lite", { input: 0.1, output: 0.4 }],
  ["flash", { input: 0.3, output: 2.5 }],
  ["gemini", { input: 1.25, output: 10 }],
  ["haiku", { input: 1, output: 5 }],
  ["opus", { input: 5, output: 25 }],
  ["voyage", { input: 0.06, output: 0 }],
];

/** Warn once per unknown model, not once per row — a dashboard query is thousands. */
const warned = new Set<string>();

export function rateFor(model: string): { input: number; output: number } {
  const exact = RATES[model];
  if (exact) return exact;

  const name = model.toLowerCase();
  const family = FAMILY_RATES.find(([key]) => name.includes(key));
  if (!warned.has(model)) {
    warned.add(model);
    console.warn(
      `[usage] no published rate for "${model}" — estimating at the ${
        family ? `${family[0]} family` : "default Sonnet"
      } rate. Add it to RATES in apps/api/src/usage.ts for an accurate figure.`,
    );
  }
  return family ? family[1] : DEFAULT_RATE;
}

export function estimateCostUsd(row: {
  model: string;
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  cacheWriteTokens: number;
}): number {
  const rate = rateFor(row.model);
  return (
    (row.inputTokens * rate.input +
      row.cacheReadTokens * rate.input * 0.1 +
      row.cacheWriteTokens * rate.input * 1.25 +
      row.outputTokens * rate.output) /
    1_000_000
  );
}
