import { recordUsage } from "./usage.js";

/**
 * Cross-encoder reranking — the last, and most accurate, ordering step.
 *
 * Retrieval fuses two rankings and cuts to the top N. That cut is blind: RRF
 * only knows each half's ORDER, never whether a chunk actually answers the
 * question. A reranker reads the question and the chunk TOGETHER, which is
 * what lets it separate a chunk that mentions S102A in a cross-reference from
 * the chunk that IS S102A — a distinction no embedding can make, because by
 * the time a chunk is a vector the question is not in the room.
 *
 * So the shape is: retrieve wide, rerank, cut narrow. It is the only quality
 * lever here that needs no re-index and no change to anything already stored.
 *
 *   RERANK_PROVIDER = none (default) | cohere | voyage
 *   RERANK_MODEL      overrides the per-provider default
 *   RERANK_CANDIDATES how many chunks are judged (cost and latency scale here)
 *
 * Default off: it adds a round trip to every question and a bill to every
 * search, and this app shipped without it. Turn it on once the evaluation set
 * (benchmarks/retrieval_eval.mjs) says it earns its latency.
 */
export type RerankProvider = "none" | "cohere" | "voyage";

const PROVIDERS: readonly RerankProvider[] = ["none", "cohere", "voyage"] as const;

export const DEFAULT_RERANK_MODELS: Record<Exclude<RerankProvider, "none">, string> = {
  cohere: "rerank-v3.5",
  voyage: "rerank-2.5",
};

const KEY_ENV: Record<Exclude<RerankProvider, "none">, string> = {
  cohere: "COHERE_API_KEY",
  voyage: "VOYAGE_API_KEY",
};

const BASE_URLS: Record<Exclude<RerankProvider, "none">, string> = {
  cohere: "https://api.cohere.com",
  voyage: "https://api.voyageai.com",
};

export function rerankProvider(): RerankProvider {
  const name = (process.env.RERANK_PROVIDER ?? "none").trim().toLowerCase();
  return (PROVIDERS as readonly string[]).includes(name) ? (name as RerankProvider) : "none";
}

export function rerankModel(): string | null {
  const provider = rerankProvider();
  if (provider === "none") return null;
  return process.env.RERANK_MODEL || DEFAULT_RERANK_MODELS[provider];
}

/**
 * Whether reranking will actually run. A provider selected without its key is
 * NOT an error — retrieval simply keeps the fused order, the same way the
 * keyword half degrades to dense. A missing key must never fail a question.
 */
export function rerankEnabled(): boolean {
  const provider = rerankProvider();
  return provider !== "none" && Boolean(process.env[KEY_ENV[provider]]);
}

/**
 * Candidates sent to the reranker. Wider than the final cut is the entire
 * point — a chunk ranked 40th by fusion and 1st by the reranker is exactly the
 * case this exists to catch — but every extra candidate is tokens and
 * milliseconds, so it is bounded.
 */
export function rerankCandidates(): number {
  const raw = Number(process.env.RERANK_CANDIDATES);
  return Number.isFinite(raw) && raw > 0 ? Math.min(200, Math.floor(raw)) : 60;
}

export interface RerankDocument {
  id: string;
  text: string;
}

interface ProviderCall {
  url: string;
  headers: Record<string, string>;
  body: Record<string, unknown>;
  /** Indexes into the documents array, best first. */
  read: (json: unknown) => { index: number; score: number }[];
}

function buildCall(
  provider: Exclude<RerankProvider, "none">,
  query: string,
  documents: string[],
  model: string,
  topN: number,
): ProviderCall {
  const key = process.env[KEY_ENV[provider]] ?? "";

  if (provider === "voyage") {
    return {
      url: `${process.env.VOYAGE_BASE_URL || BASE_URLS.voyage}/v1/rerank`,
      headers: { Authorization: `Bearer ${key}` },
      body: { query, documents, model, top_k: topN },
      read: (json) =>
        ((json as { data?: { index: number; relevance_score: number }[] }).data ?? []).map(
          (r) => ({ index: r.index, score: r.relevance_score }),
        ),
    };
  }

  return {
    url: `${process.env.COHERE_BASE_URL || BASE_URLS.cohere}/v2/rerank`,
    headers: { Authorization: `Bearer ${key}` },
    body: { query, documents, model, top_n: topN },
    read: (json) =>
      ((json as { results?: { index: number; relevance_score: number }[] }).results ?? []).map(
        (r) => ({ index: r.index, score: r.relevance_score }),
      ),
  };
}

/**
 * What one rerank call costs, in the units its provider bills.
 *
 * Cohere bills PER SEARCH, not per token, which the token-shaped usage table
 * cannot express directly — so a search is recorded as one "token" and priced
 * at $2000 per million in apps/api/src/usage.ts. That reads oddly until you
 * notice the rate table is already per-million-units: one unit at $2000/M is
 * $0.002, which is exactly Cohere's per-search price. Voyage bills per token,
 * so its rows carry the real token estimate.
 */
function billableUnits(provider: Exclude<RerankProvider, "none">, documents: string[]): number {
  if (provider === "cohere") return 1; // one search
  return documents.reduce((sum, d) => sum + Math.max(1, Math.floor(d.length / 4)), 0);
}

/**
 * Reorder `documents` by relevance to `query`, best first, keeping at most
 * `topN`. Returns ids in the new order.
 *
 * Never throws: a reranker failure returns null so the caller keeps the order
 * it already had. Reranking is an improvement on a working system, not a
 * dependency of one.
 */
export async function rerank(
  query: string,
  documents: RerankDocument[],
  topN: number,
  projectId?: string,
): Promise<string[] | null> {
  const provider = rerankProvider();
  if (provider === "none" || !rerankEnabled() || documents.length === 0) return null;

  const model = rerankModel()!;
  const texts = documents.map((d) => d.text);
  const call = buildCall(provider, query, texts, model, Math.min(topN, documents.length));

  try {
    const res = await fetch(call.url, {
      method: "POST",
      headers: { ...call.headers, "Content-Type": "application/json" },
      body: JSON.stringify(call.body),
    });
    if (!res.ok) {
      console.warn(`[rerank] ${provider} failed: ${res.status} ${await res.text()}`);
      return null;
    }

    const results = call.read(await res.json());
    if (results.length === 0) return null;

    await recordUsage(projectId ?? null, "rerank", model, {
      inputTokens: billableUnits(provider, texts),
    });

    // An out-of-range index would silently drop a chunk or, worse, attribute
    // one chunk's rank to another. Discard rather than trust.
    return results
      .filter((r) => r.index >= 0 && r.index < documents.length)
      .map((r) => documents[r.index]!.id)
      .slice(0, topN);
  } catch (err) {
    console.warn("[rerank] call failed:", (err as Error).message);
    return null;
  }
}
