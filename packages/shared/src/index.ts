/**
 * Shared types for the Construction Drawing AI Platform.
 * Mirrors the vocabulary in CLAUDE.md; the Python workers duplicate the
 * queue names and payload shapes in workers/src/contracts.py — keep in sync.
 */

export type DocumentStatus = "uploaded" | "processing" | "completed" | "failed";

export type SummaryLevel = "page" | "section" | "portion" | "project";

/** Portion (discipline) buckets detected from sheet-number prefixes + title blocks. */
export type Discipline =
  | "architectural"
  | "structural"
  | "plumbing"
  | "electrical"
  | "hvac"
  | "fire_protection"
  | "civil"
  | "site_landscape"
  | "details_legends_schedules";

/** Bounding box in PDF page coordinates. */
export interface BBox {
  x: number;
  y: number;
  width: number;
  height: number;
}

/** Metadata carried by every chunk (see "Chunking strategy" in CLAUDE.md). */
export interface ChunkMetadata {
  chunk_id: string;
  document_id: string;
  page: number;
  portion: string;
  discipline: Discipline;
  bbox: BBox;
  text: string;
  image_ref: string | null;
  revision: number;
  token_count: number;
}

/** Page manifest entry: virtual merge of (document, page) → combined page number. */
export interface PageManifestEntry {
  documentId: string;
  pageNumber: number;
  combinedPageNumber: number;
}

// --- BullMQ queues (consumed by the Python workers) ---

export const QUEUES = {
  processDocument: "process-document",
} as const;

export interface ProcessDocumentJob {
  projectId: string;
  documentId: string;
  spacesKey: string;
}
