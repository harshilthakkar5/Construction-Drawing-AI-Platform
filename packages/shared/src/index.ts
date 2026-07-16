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

// --- Spaces/MinIO bucket layout (mirrored in workers/src/storage.py) ---

export const objectKeys = {
  originalPdf: (projectId: string, documentId: string) =>
    `projects/${projectId}/pdfs/${documentId}/original.pdf`,
  pageImage: (projectId: string, documentId: string, page: number) =>
    `projects/${projectId}/pdfs/${documentId}/pages/${page}.png`,
  pageThumb: (projectId: string, documentId: string, page: number) =>
    `projects/${projectId}/pdfs/${documentId}/thumbs/${page}.jpg`,
  pageText: (projectId: string, documentId: string, page: number) =>
    `projects/${projectId}/pdfs/${documentId}/text/${page}.txt`,
} as const;

// --- API DTOs ---

export interface ProjectDto {
  id: string;
  name: string;
  description: string | null;
  createdAt: string;
}

export interface DocumentDto {
  id: string;
  projectId: string;
  filename: string;
  pages: number;
  revision: number;
  status: DocumentStatus;
  createdAt: string;
}

/** One combined-viewer page, in manifest order. */
export interface ManifestEntryDto extends PageManifestEntry {
  filename: string;
  hasImage: boolean;
}

/** FR-15: contiguous run of one discipline in combined numbering. */
export interface PortionDto {
  id: string;
  projectId: string;
  name: string;
  discipline: Discipline | "unclassified" | null;
  startPage: number;
  endPage: number;
  summary: string | null;
}

export interface InitiateUploadResponse {
  documentId: string;
  uploadId: string;
  key: string;
  partSize: number;
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
