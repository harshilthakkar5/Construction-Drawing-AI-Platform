-- A sheet number may carry a second dotted group: "A2.81.01" .. "A2.81.06" are
-- six enlarged unit plans, one sheet each. One group made all six "A281".
CREATE OR REPLACE FUNCTION cdip_identifiers(src text) RETURNS text[]
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $fn$
  SELECT COALESCE(
           array_agg(DISTINCT upper(regexp_replace(m[1], '[^A-Za-z0-9]', '', 'g'))),
           ARRAY[]::text[])
    FROM regexp_matches(
           COALESCE(src, ''),
           '\y([A-Z]{1,3}[-.]?[0-9]{1,4}(?:\.[0-9]+){0,2}(?:-[A-Z]{1,3}\y)*[A-Z]?|[A-Z][0-9]{1,3}[xX][0-9]{1,3})\y',
           'g') AS m
$fn$;

DELETE FROM chunk_identifiers ci
 USING chunks c
 WHERE c.id = ci."chunkId" AND c.text ~ '[A-Z][-.]?[0-9]+\.[0-9]+\.[0-9]';
INSERT INTO chunk_identifiers ("chunkId", identifier)
SELECT c.id, unnest(cdip_identifiers(c.text))
  FROM chunks c
 WHERE c.text ~ '[A-Z][-.]?[0-9]+\.[0-9]+\.[0-9]' AND c.kind IN ('text', 'gridmarks')
ON CONFLICT DO NOTHING;
