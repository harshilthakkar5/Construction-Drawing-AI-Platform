import { prisma } from "./db.js";
import { embedQuery } from "./embedding.js";
import { searchChunks } from "./qdrant.js";
import { rerank, rerankCandidates, rerankEnabled } from "./rerank.js";

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
 * A third arm joins them when the question names an identifier — see
 * `searchIdentifiers` — and it is weighted above the other two, because
 * "what is on S102A" is a request for that sheet and very little else.
 *
 * Finally, when a reranker is configured (./rerank.ts), the fused list is
 * retrieved WIDE and cut narrow by a cross-encoder that reads the question and
 * each chunk together. Fusion decides what gets considered; the reranker
 * decides what survives.
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
 * Exact identifier arm. A question naming S102A, A-301 or W12x26 is asking for
 * that thing, and neither of the other two arms treats it as more than a word:
 * to the embedding it is "a structural sheet number", to FTS it is one lexeme
 * among the question's others.
 *
 * Both sides go through the same `cdip_identifiers()` SQL function, which is
 * the single definition of what an identifier is and how it normalizes — the
 * worker calls it when writing a chunk, this calls it on the question. A regex
 * duplicated in Python and TypeScript would drift, and a drift here means the
 * question's identifiers quietly stop matching the documents'.
 *
 * The question is uppercased first, which is what lets someone type "what is
 * s102a" in lower case: the extractor is deliberately case-SENSITIVE so that
 * ordinary prose ("no.5 bars") stays out of the index, and a question is short
 * enough that uppercasing it costs nothing.
 *
 * Ranked by how many of the question's identifiers a chunk carries, so a chunk
 * mentioning both S102A and A-301 outranks one mentioning either.
 */
export async function searchIdentifiers(
  projectId: string,
  question: string,
  options: RetrievalOptions = {},
): Promise<string[]> {
  const limit = options.limit ?? CANDIDATES;
  const portionId = options.portionId ?? null;
  const rows = await prisma.$queryRaw<{ id: string }[]>`
    WITH q AS (SELECT cdip_identifiers(upper(${question})) AS ids)
    SELECT c.id, count(*) AS hits
      FROM chunks c
      JOIN pages p ON c."pageId" = p.id
      JOIN documents d ON p."documentId" = d.id
      JOIN chunk_identifiers ci ON ci."chunkId" = c.id
      CROSS JOIN q
     WHERE d."projectId" = ${projectId}
       AND d."supersededAt" IS NULL
       AND (${portionId}::text IS NULL OR c."portionId" = ${portionId}::text)
       AND ci.identifier = ANY(q.ids)
     GROUP BY c.id
     ORDER BY hits DESC, c.id
     LIMIT ${limit}
  `;
  return rows.map((r) => r.id);
}

/** A list to fuse, and how much its opinion counts. */
export interface WeightedList {
  ids: string[];
  weight: number;
}

/**
 * Fuse ranked id lists into one. Lists are ranked best-first; the result is
 * ordered by summed reciprocal rank, best first.
 *
 * Weights multiply a list's contribution. They exist for one case: an exact
 * identifier match is stronger evidence than any similarity score, so that arm
 * counts for more. Unweighted lists (weight 1) behave exactly as before.
 */
export function reciprocalRankFusion(
  lists: (string[] | WeightedList)[],
  limit: number,
): string[] {
  const scores = new Map<string, number>();
  for (const entry of lists) {
    const { ids, weight } = Array.isArray(entry) ? { ids: entry, weight: 1 } : entry;
    ids.forEach((id, index) => {
      scores.set(id, (scores.get(id) ?? 0) + weight / (RRF_K + index + 1));
    });
  }
  return [...scores.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .slice(0, limit)
    .map(([id]) => id);
}

export interface RetrievalResult {
  chunkIds: string[];
  /** What each arm contributed, for the log line and the eval harness. */
  dense: number;
  keyword: number;
  identifier: number;
  /** Whether a cross-encoder decided the final order. */
  reranked: boolean;
}

/**
 * An exact identifier match is stronger evidence than any similarity score:
 * "what is on S102A" wants that sheet, not something like it. Weighting the
 * arm rather than short-circuiting to it keeps the other two contributing —
 * the question usually asks something ABOUT the sheet, and that context comes
 * from the semantic half.
 *
 * 3, and the number is arithmetic rather than taste. A chunk at rank 1 in BOTH
 * similarity arms scores 2/(K+1); an identifier hit at rank 1 with weight W
 * scores W/(K+1). At W=2 those are exactly equal, so a plausible-looking
 * neighbour that dense and keyword agree on ties with the sheet the user named
 * — and the tie breaks alphabetically, which is no answer at all. W=3 puts the
 * exact match clearly ahead while a chunk that is top of the identifier arm
 * AND top of a similarity arm still outranks it, which is the right order.
 */
const IDENTIFIER_WEIGHT = 3;

/**
 * Load the pool's text and let the cross-encoder decide the final order.
 * Returns null when reranking is off, unavailable, or failed — the caller then
 * keeps the order it already had.
 */
async function rerankPool(
  projectId: string,
  question: string,
  pool: string[],
  limit: number,
): Promise<string[] | null> {
  if (!rerankEnabled() || pool.length === 0) return null;

  // The reranker needs the TEXT, which neither search returned — one query for
  // the pool, then re-sorted into the order the pool was already in.
  const rows = await prisma.chunk.findMany({
    where: { id: { in: pool } },
    select: { id: true, text: true },
  });
  const textById = new Map(rows.map((r) => [r.id, r.text]));
  const documents = pool.flatMap((id) => {
    const text = textById.get(id);
    return text ? [{ id, text }] : [];
  });

  return rerank(question, documents, limit, projectId);
}

/**
 * Retrieve the chunk ids for one question. The arms run together; a failure in
 * any of them degrades to the others rather than failing the question, because
 * dense alone is what this app shipped with and still answers most questions.
 */
export async function retrieveChunkIds(
  projectId: string,
  question: string,
  options: RetrievalOptions = {},
): Promise<RetrievalResult> {
  const limit = options.limit ?? 18;

  // With a reranker, the searches' job changes: they no longer pick the final
  // N, they assemble the pool a cross-encoder will judge. So the pool widens —
  // in BOTH retrieval modes. Reranking is orthogonal to how candidates were
  // found, and tying it to HYBRID_RETRIEVAL made `RERANK_PROVIDER=voyage
  // HYBRID_RETRIEVAL=false` silently do nothing at all.
  const reranking = rerankEnabled();
  const poolSize = reranking ? rerankCandidates() : limit;

  if (!hybridEnabled()) {
    const vector = await embedQuery(question, projectId);
    const dense = await searchChunks(projectId, vector, { ...options, limit: poolSize });
    const ordered = await rerankPool(projectId, question, dense, limit);
    return {
      chunkIds: ordered ?? dense.slice(0, limit),
      dense: dense.length,
      keyword: 0,
      identifier: 0,
      reranked: ordered !== null,
    };
  }

  const armLimit = Math.max(CANDIDATES, poolSize);

  const [denseIds, keywordIds, identifierIds] = await Promise.all([
    embedQuery(question, projectId)
      .then((vector) => searchChunks(projectId, vector, { ...options, limit: armLimit }))
      .catch((err) => {
        // Dense failing is the serious one — without it there is no semantic
        // recall at all — but a keyword-only answer beats no answer.
        console.warn("[retrieval] dense search failed:", (err as Error).message);
        return [] as string[];
      }),
    searchKeyword(projectId, question, { ...options, limit: armLimit }).catch((err) => {
      console.warn("[retrieval] keyword search failed:", (err as Error).message);
      return [] as string[];
    }),
    searchIdentifiers(projectId, question, { ...options, limit: armLimit }).catch((err) => {
      // Most likely cause: the identifier migration has not been run yet.
      console.warn("[retrieval] identifier search failed:", (err as Error).message);
      return [] as string[];
    }),
  ]);

  const fused = reciprocalRankFusion(
    [
      { ids: denseIds, weight: 1 },
      { ids: keywordIds, weight: 1 },
      { ids: identifierIds, weight: IDENTIFIER_WEIGHT },
    ],
    poolSize,
  );

  const ordered = await rerankPool(projectId, question, fused, limit);
  return {
    chunkIds: ordered ?? fused.slice(0, limit),
    dense: denseIds.length,
    keyword: keywordIds.length,
    identifier: identifierIds.length,
    reranked: ordered !== null,
  };
}
