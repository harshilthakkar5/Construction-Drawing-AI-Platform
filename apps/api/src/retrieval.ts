import { prisma } from "./db.js";
import { embedQuery } from "./embedding.js";
import { searchChunks } from "./qdrant.js";

/**
 * Hybrid retrieval: dense embeddings + keyword search, fused by rank.
 *
 * Dense alone is the wrong tool for half the questions this app gets. A
 * drawing set is full of exact tokens — sheet numbers (S102A), details
 * (A-301), member sizes (W12x26), panel tags — and a question naming one wants
 * THAT string, not something semantically near it. Cosine similarity cannot
 * tell S102A from S201: both are "a structural sheet number", so the sheet the
 * user actually named can sit outside the top 18 while eighteen of its
 * neighbours are returned instead.
 *
 * Keyword search is the wrong tool for the other half — "what holds up the
 * canopy" shares no words with the note that answers it.
 *
 * So both run, and the two rankings are merged with Reciprocal Rank Fusion:
 *
 *     score(chunk) = Σ  1 / (RRF_K + rank_in_that_list)
 *
 * RRF needs no score calibration between the two systems, which is the point —
 * a cosine distance and a ts_rank are not comparable numbers, but their RANKS
 * are. A chunk both methods like beats one that only one of them found.
 *
 * The two searches run CONCURRENTLY, so hybrid retrieval costs the slower of
 * the two rather than their sum — in practice the embedding round trip, which
 * dense-only already paid.
 *
 * HYBRID_RETRIEVAL=false falls back to dense alone.
 */

/** Rank offset. 60 is the value from the original RRF paper: high enough that
 * the top few results of one list cannot swamp the other's. */
const RRF_K = 60;

/** How many candidates each method contributes before fusion. Wider than the
 * final cut so a chunk ranked 20th by one method and 3rd by the other can
 * still win — which is the whole point of fusing. */
const CANDIDATES = 40;

export interface RetrievalOptions {
  portionId?: string;
  limit?: number;
}

export function hybridEnabled(): boolean {
  return (process.env.HYBRID_RETRIEVAL ?? "true").trim().toLowerCase() !== "false";
}

/**
 * Keyword half. Postgres FTS over chunks.text, scoped to the project and
 * (optionally) one portion, excluding superseded revisions exactly as the
 * dense half does via its payload filter.
 *
 * Two things here were established against a real Postgres rather than
 * assumed, and both are load-bearing:
 *
 * 1. The 'english' configuration, matching the index. 'simple' keeps stop
 *    words, so "what is S102A" becomes 'what' & 'is' & 's102a' and matches
 *    nothing at all — no chunk contains "what". 'english' reduces the same
 *    question to 's102a'.
 *
 * 2. The query is the lexemes OR'd together, not AND'd. Every builder
 *    Postgres ships (websearch_to_tsquery, plainto_tsquery) ANDs them, which
 *    demands a chunk containing every word of the question — a standard for
 *    a search box, hopeless for a sentence. OR'ing lets ts_rank do the
 *    ranking: a chunk matching four of the words outranks one matching two,
 *    and RRF only needs that order.
 *
 * Building the query from `tsvector_to_array(to_tsvector(...))` rather than
 * from the raw string is also what makes it injection-safe: the lexemes are
 * produced by Postgres itself, so a question full of `&`, `|`, `!` and quotes
 * comes out as ordinary words. NULLIF covers a question that is nothing BUT
 * stop words — to_tsquery('') is an error, NULL simply matches nothing.
 */
export async function searchKeyword(
  projectId: string,
  question: string,
  options: RetrievalOptions = {},
): Promise<string[]> {
  const limit = options.limit ?? CANDIDATES;
  const portionId = options.portionId ?? null;
  const rows = await prisma.$queryRaw<{ id: string }[]>`
    WITH q AS (
      SELECT to_tsquery(
               'english',
               NULLIF(
                 array_to_string(tsvector_to_array(to_tsvector('english', ${question})), ' | '),
                 ''
               )
             ) AS tsq
    )
    SELECT c.id, ts_rank(to_tsvector('english', c.text), q.tsq) AS rank
      FROM chunks c
      JOIN pages p ON c."pageId" = p.id
      JOIN documents d ON p."documentId" = d.id
      CROSS JOIN q
     WHERE d."projectId" = ${projectId}
       AND d."supersededAt" IS NULL
       AND (${portionId}::text IS NULL OR c."portionId" = ${portionId}::text)
       AND to_tsvector('english', c.text) @@ q.tsq
     ORDER BY rank DESC
     LIMIT ${limit}
  `;
  return rows.map((r) => r.id);
}

/**
 * Fuse ranked id lists into one. Lists are ranked best-first; the result is
 * ordered by summed reciprocal rank, best first.
 */
export function reciprocalRankFusion(lists: string[][], limit: number): string[] {
  const scores = new Map<string, number>();
  for (const list of lists) {
    list.forEach((id, index) => {
      scores.set(id, (scores.get(id) ?? 0) + 1 / (RRF_K + index + 1));
    });
  }
  return [...scores.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .slice(0, limit)
    .map(([id]) => id);
}

export interface RetrievalResult {
  chunkIds: string[];
  /** What each half contributed, for the log line and the metrics. */
  dense: number;
  keyword: number;
}

/**
 * Retrieve the chunk ids for one question. Dense and keyword run together;
 * a failure in the keyword half degrades to dense-only rather than failing
 * the question, because dense alone is what this app shipped with.
 */
export async function retrieveChunkIds(
  projectId: string,
  question: string,
  options: RetrievalOptions = {},
): Promise<RetrievalResult> {
  const limit = options.limit ?? 18;

  if (!hybridEnabled()) {
    const vector = await embedQuery(question, projectId);
    const dense = await searchChunks(projectId, vector, { ...options, limit });
    return { chunkIds: dense, dense: dense.length, keyword: 0 };
  }

  const [denseIds, keywordIds] = await Promise.all([
    embedQuery(question, projectId)
      .then((vector) => searchChunks(projectId, vector, { ...options, limit: CANDIDATES }))
      .catch((err) => {
        // Dense failing is the serious one — without it there is no semantic
        // recall at all — but a keyword-only answer beats no answer.
        console.warn("[retrieval] dense search failed:", (err as Error).message);
        return [] as string[];
      }),
    searchKeyword(projectId, question, { ...options, limit: CANDIDATES }).catch((err) => {
      console.warn("[retrieval] keyword search failed:", (err as Error).message);
      return [] as string[];
    }),
  ]);

  return {
    chunkIds: reciprocalRankFusion([denseIds, keywordIds], limit),
    dense: denseIds.length,
    keyword: keywordIds.length,
  };
}
