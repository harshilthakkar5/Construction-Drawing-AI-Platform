-- Phase 02: exact identifier search.
--
-- Hybrid retrieval already runs a keyword search, but to Postgres FTS an
-- identifier is just another word: "S102A" is ranked by ts_rank alongside
-- "framing" and "plan", and a chunk that merely mentions the sheet in a
-- cross-reference scores the same as the sheet itself.
--
-- Worse, the 'english' configuration mangles some of them. "A-301" indexes as
-- the lexeme '-301' — the leading letter is dropped. It matches today only
-- because the query is mangled identically, and that coincidence breaks the
-- moment a chunk writes "A301" without the hyphen, which drawing sets do
-- constantly.
--
-- So identifiers get their own extraction, their own normalization and their
-- own index, and retrieval gains a third arm that looks them up exactly.

-- ONE definition of what an identifier is, in SQL, used by three callers: the
-- backfill below, the worker's chunk insert, and the API's query side. A regex
-- duplicated across Python and TypeScript would drift, and a drift here means
-- the question's identifiers stop matching the documents'.
--
-- Matched shapes, all uppercase because drawing text is and prose is not —
-- that case-sensitivity is what keeps "No.5 bars" and ordinary sentences out
-- of the index:
--   sheet / detail / door / panel tags   S102A, A-301, S-003.0, M2.1, D-102, P1
--   member sizes                         W18x97, W16x57
--
-- Normalization strips every separator and uppercases, so "A-301", "A301" and
-- "A.301" collapse to one key on both sides of the lookup.
CREATE OR REPLACE FUNCTION cdip_identifiers(src text) RETURNS text[]
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $fn$
  SELECT COALESCE(
           array_agg(DISTINCT upper(regexp_replace(m[1], '[^A-Za-z0-9]', '', 'g'))),
           ARRAY[]::text[])
    FROM regexp_matches(
           COALESCE(src, ''),
           '\y([A-Z]{1,3}[-.]?[0-9]{1,4}(?:\.[0-9]+)?[A-Z]?|[A-Z][0-9]{1,3}[xX][0-9]{1,3})\y',
           'g') AS m
$fn$;

CREATE TABLE IF NOT EXISTS chunk_identifiers (
  "chunkId"  TEXT NOT NULL,
  identifier TEXT NOT NULL,
  CONSTRAINT chunk_identifiers_pkey PRIMARY KEY ("chunkId", identifier),
  CONSTRAINT chunk_identifiers_chunk_fkey FOREIGN KEY ("chunkId")
    REFERENCES chunks(id) ON DELETE CASCADE ON UPDATE CASCADE
);

-- The lookup direction: identifier → chunks. The primary key already covers
-- (chunkId, identifier) for the cascade delete.
CREATE INDEX IF NOT EXISTS chunk_identifiers_identifier_idx
  ON chunk_identifiers (identifier);

-- Backfill every chunk already indexed. Pure SQL over text this database
-- already holds: no re-embedding, no Qdrant work, no provider cost.
INSERT INTO chunk_identifiers ("chunkId", identifier)
SELECT c.id, unnest(cdip_identifiers(c.text))
  FROM chunks c
ON CONFLICT DO NOTHING;

-- Phase 01: reranking is a fourth thing that costs money, and the dashboard
-- reports spend per stage. Adding the value here rather than at first use
-- keeps usage_events honest about which stage a cost came from.
ALTER TYPE "UsageKind" ADD VALUE IF NOT EXISTS 'rerank';
