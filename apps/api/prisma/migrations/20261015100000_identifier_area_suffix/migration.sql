-- An AREA suffix is part of a sheet number: "S2.30-A" and "S2.30-B" are two
-- sheets (one floor drawn in halves). The extractor stopped at the dash, so
-- both became "S230", and a note pointing at S2.30-B could not be told from
-- one pointing at S2.30-A — nor matched to either sheet, whose number the
-- title-block reader now keeps whole. "-B" is taken only when it ends the
-- token, so "S2.30-AREA" stays "S230".
CREATE OR REPLACE FUNCTION cdip_identifiers(src text) RETURNS text[]
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $fn$
  SELECT COALESCE(
           array_agg(DISTINCT upper(regexp_replace(m[1], '[^A-Za-z0-9]', '', 'g'))),
           ARRAY[]::text[])
    FROM regexp_matches(
           COALESCE(src, ''),
           '\y([A-Z]{1,3}[-.]?[0-9]{1,4}(?:\.[0-9]+)?(?:-[A-Z]{1,2}\y)?[A-Z]?|[A-Z][0-9]{1,3}[xX][0-9]{1,3})\y',
           'g') AS m
$fn$;

-- Re-index what is stored. Pure SQL over text this database already holds; only
-- the chunks a suffix can change are touched.
DELETE FROM chunk_identifiers ci
 USING chunks c
 WHERE c.id = ci."chunkId" AND c.text ~ '[0-9]-[A-Z]';
INSERT INTO chunk_identifiers ("chunkId", identifier)
SELECT c.id, unnest(cdip_identifiers(c.text))
  FROM chunks c
 WHERE c.text ~ '[0-9]-[A-Z]' AND c.kind <> 'description'
ON CONFLICT DO NOTHING;
