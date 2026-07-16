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
}

export interface NumberedSource extends ChunkSourceRecord {
  /** 1-based citation number, ordered by first appearance in the answer. */
  index: number;
  /** Clickable label, e.g. "S201.pdf — page 17". */
  label: string;
}

const CITATION_RE = /\[chunk:([0-9a-fA-F][0-9a-fA-F-]{34}[0-9a-fA-F])\]/g;

export function extractCitedChunkIds(answer: string): string[] {
  const seen = new Set<string>();
  const ordered: string[] = [];
  for (const match of answer.matchAll(CITATION_RE)) {
    const id = match[1]!.toLowerCase();
    if (!seen.has(id)) {
      seen.add(id);
      ordered.push(id);
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

  const text = answer
    .replace(CITATION_RE, (_full, rawId: string) => {
      const id = rawId.toLowerCase();
      const record = records.get(id);
      if (!record) return "";
      let index = indexByChunk.get(id);
      if (index === undefined) {
        index = sources.length + 1;
        indexByChunk.set(id, index);
        sources.push({ ...record, index, label: sourceLabel(record) });
      }
      return `[${index}]`;
    })
    .replace(/[ \t]+([.,;:)])/g, "$1") // tidy space left by stripped citations
    .replace(/[ \t]{2,}/g, " ")
    .trim();

  return { text, sources };
}
