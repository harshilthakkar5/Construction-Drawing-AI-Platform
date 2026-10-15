-- A sheet suffix may be chained: "S2.33-B-PT" and "S2.33-B-R" are that half's
-- post-tensioning and reinforcing plans, distinct sheets of the same set. The
-- previous version kept one suffix ("S233B"), so all of them collapsed onto the
-- framing plan. Each suffix is taken only when it ends at a word boundary.
CREATE OR REPLACE FUNCTION cdip_identifiers(src text) RETURNS text[]
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $fn$
  SELECT COALESCE(
           array_agg(DISTINCT upper(regexp_replace(m[1], '[^A-Za-z0-9]', '', 'g'))),
           ARRAY[]::text[])
    FROM regexp_matches(
           COALESCE(src, ''),
           '\y([A-Z]{1,3}[-.]?[0-9]{1,4}(?:\.[0-9]+)?(?:-[A-Z]{1,3}\y)*[A-Z]?|[A-Z][0-9]{1,3}[xX][0-9]{1,3})\y',
           'g') AS m
$fn$;

DELETE FROM chunk_identifiers ci
 USING chunks c
 WHERE c.id = ci."chunkId" AND c.text ~ '[0-9]-[A-Z]';
INSERT INTO chunk_identifiers ("chunkId", identifier)
SELECT c.id, unnest(cdip_identifiers(c.text))
  FROM chunks c
 WHERE c.text ~ '[0-9]-[A-Z]' AND c.kind IN ('text', 'gridmarks')
ON CONFLICT DO NOTHING;
