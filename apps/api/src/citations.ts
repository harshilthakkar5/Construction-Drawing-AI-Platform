/**
 * Citation mapping — the source-verification chain (never break it):
 * answer → chunk_id → page → bounding box → original PDF.
 *
 * Claude is instructed to tag every claim with [chunk:<uuid>]. This module
 * extracts those tags, maps them to numbered sources with document/page/bbox
 * (FR-13, FR-21), and rewrites the answer to compact [n] markers the UI can
 * make clickable (FR-18).
 */

export interface BBox {
  x: number;
  y: number;
  width: number;
  height: number;
}

/** What the chat route loads from Postgres for each retrieved chunk. */
export interface ChunkSourceRecord {
  chunkId: string;
  documentId: string;
  filename: string;
  pageNumber: number;
  combinedPageNumber: number;
  bbox: BBox;
  /** PDF page size in points (bbox coordinate space, FR-19); null for pages
   * processed before Phase 5 — the UI then jumps without highlighting. */
  pageWidth?: number | null;
  pageHeight?: number | null;
}

export interface NumberedSource extends ChunkSourceRecord {
  /** 1-based citation number, ordered by first appearance in the answer. */
  index: number;
  /** Clickable label, e.g. "S201.pdf — page 17". */
  label: string;
}

const UUID = "[0-9a-fA-F][0-9a-fA-F-]{34}[0-9a-fA-F]";

/**
 * A whole bracketed citation group, however many ids it holds.
 *
 * The model does not always emit one id per bracket. Asked to cite two chunks
 * for one claim it writes `[chunk:a, chunk:b]`, and a pattern anchored on
 * `[chunk:<id>]` matches NEITHER half of that — the first id is followed by a
 * comma rather than `]`, the second preceded by a space rather than `[`. The
 * group then survived untouched into the UI, showing a reader two raw UUIDs
 * where a source chip belonged.
 *
 * So the bracket is matched first and the ids are pulled out of it, which
 * covers `[chunk:a]`, `[chunk:a, chunk:b]`, `[chunk:a; chunk:b]` and
 * `[chunk:a chunk:b]` alike.
 */
const CITATION_GROUP_RE = new RegExp(
  `\\[\\s*chunk:\\s*${UUID}(?:\\s*[,;]?\\s*(?:chunk:)?\\s*${UUID})*\\s*\\]`,
  // Case-insensitive: a model that writes [CHUNK:…] must not leak a raw id
  // just because it shouted.
  "gi",
);

/** Every id inside one matched group. */
const ID_RE = new RegExp(UUID, "g");

/**
 * A chunk id ANYWHERE else — `(chunk:a)`, `chunk: a`, a bare id in prose.
 * The last line of defence: whatever shape the model invents, a raw id must
 * never reach a reader, because it is meaningless to them and looks like a
 * bug. Applied after the groups, so a well-formed citation is numbered rather
 * than deleted.
 */
const STRAY_CITATION_RE = new RegExp(`\\[?\\s*chunk:\\s*${UUID}\\s*\\]?`, "gi");

export function extractCitedChunkIds(answer: string): string[] {
  const seen = new Set<string>();
  const ordered: string[] = [];
  for (const group of answer.match(CITATION_GROUP_RE) ?? []) {
    for (const rawId of group.match(ID_RE) ?? []) {
      const id = rawId.toLowerCase();
      if (!seen.has(id)) {
        seen.add(id);
        ordered.push(id);
      }
    }
  }
  return ordered;
}

export function sourceLabel(record: ChunkSourceRecord): string {
  return `${record.filename} — page ${record.combinedPageNumber}`;
}

/**
 * Rewrites [chunk:id] markers to [n] and returns the numbered sources in
 * first-appearance order. Citations of unknown chunk IDs (hallucinated or
 * not retrieved) are stripped from the text and excluded from sources.
 */
export function buildSources(
  answer: string,
  records: Map<string, ChunkSourceRecord>,
): { text: string; sources: NumberedSource[] } {
  const indexByChunk = new Map<string, number>();
  const sources: NumberedSource[] = [];

  const numberOf = (rawId: string): number | null => {
    const id = rawId.toLowerCase();
    const record = records.get(id);
    if (!record) return null; // hallucinated, or not among the retrieved chunks
    let index = indexByChunk.get(id);
    if (index === undefined) {
      index = sources.length + 1;
      indexByChunk.set(id, index);
      sources.push({ ...record, index, label: sourceLabel(record) });
    }
    return index;
  };

  const text = answer
    .replace(CITATION_GROUP_RE, (group) => {
      const numbers = (group.match(ID_RE) ?? [])
        .map(numberOf)
        .filter((n): n is number => n !== null);
      // A group of nothing but unknown ids disappears with the rest of its
      // brackets rather than leaving an empty "[]".
      return numbers.map((n) => `[${n}]`).join("");
    })
    // Anything still carrying an id was not a citation this code recognises;
    // it is removed outright. A reader must never see a UUID.
    .replace(STRAY_CITATION_RE, "")
    .replace(/[ \t]+([.,;:)])/g, "$1") // tidy space left by stripped citations
    .replace(/\(\s*\)/g, "") // ...and the empty brackets they leave behind
    .replace(/[ \t]{2,}/g, " ")
    .trim();

  return { text, sources };
}
