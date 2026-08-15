/**
 * Shared types for the Construction Drawing AI Platform.
 * Mirrors the vocabulary in CLAUDE.md; the Python workers duplicate the
 * queue names and payload shapes in workers/src/contracts.py — keep in sync.
 */

export type DocumentStatus = "uploaded" | "processing" | "completed" | "failed";

export type SummaryLevel = "page" | "section" | "portion" | "project";

/** Portion (discipline) buckets detected from sheet-number prefixes + title
 * blocks. Slugs mirror workers/src/classify.py PREFIX_TO_DISCIPLINE. */
export type Discipline =
  | "general"
  | "architectural"
  | "structural"
  | "civil"
  | "landscape"
  | "interiors"
  | "mechanical"
  | "hvac"
  | "plumbing"
  | "electrical"
  | "fire_protection"
  | "fire_alarm"
  | "telecommunications"
  | "information_technology"
  | "audio_visual"
  | "other";

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
  /** FR-4: document this row replaces (null for first revisions). */
  previousVersionId: string | null;
  /** FR-4: set once a newer revision finished processing; superseded
   * documents are hidden from the manifest and retrieval. */
  supersededAt: string | null;
  createdAt: string;
}

/** One combined-viewer page, in manifest order. */
export interface ManifestEntryDto extends PageManifestEntry {
  filename: string;
  hasImage: boolean;
  /** PDF page size in points (bbox coordinate space) — null until processed. */
  pageWidth: number | null;
  pageHeight: number | null;
  /** Discipline read off this page's sheet number; null until classified.
   * The viewer filters on it, so it is per PAGE — a discipline's pages are
   * routinely non-contiguous and a portion's span alone cannot express that. */
  discipline: Discipline | "unclassified" | null;
  /** The sheet number the discipline came from, e.g. "S-103.0". */
  sheetNumber: string | null;
}

/** FR-15: one portion per discipline, spanning all of its pages. */
export interface PortionDto {
  id: string;
  projectId: string;
  name: string;
  discipline: Discipline | "unclassified" | null;
  startPage: number;
  endPage: number;
  /** Pages carrying this discipline (differs from the span when interleaved). */
  pageCount: number;
  summary: string | null;
  /** Summaries are user-approved — "none" until someone presses the button. */
  summaryStatus: PortionSummaryStatus;
  summaryRequestedAt: string | null;
  summaryCompletedAt: string | null;
  summaryError: string | null;
  /** A sheet number scraped from this discipline's pages, for the UI label. */
  sheetNumberSample?: string | null;
}

// --- Title-block region (region-based discipline detection) ---

export type RegionScrapeStatus = "pending" | "running" | "completed" | "failed";

export type PortionSummaryStatus =
  | "none"
  | "queued"
  | "running"
  | "ready"
  | "failed"
  | "stale";

/** How a page's region text was obtained (see workers/src/region.py). */
export type RegionExtractionMethod = "vector" | "words" | "ocr" | "none";

/** The box the user drew over the title block, in relative page coordinates. */
export interface RegionBox {
  relX: number;
  relY: number;
  relW: number;
  relH: number;
}

export interface SheetRegionDto extends RegionBox {
  sampleDocumentId: string | null;
  samplePageNumber: number | null;
  version: number;
  scrapeStatus: RegionScrapeStatus;
  scrapedPages: number;
  totalPages: number;
  notFoundPages: number;
  lastError: string | null;
  lastScrapedAt: string | null;
  updatedAt: string;
}

/** One row of the "what does this box scrape?" dry run. */
export interface RegionPreviewRowDto {
  combinedPageNumber: number;
  documentId: string;
  filename: string;
  /** Empty when the box came back blank on this page. */
  text: string;
  method: RegionExtractionMethod;
}

export interface RegionPreviewDto {
  rows: RegionPreviewRowDto[];
  foundCount: number;
  notFoundCount: number;
}

/** A numbered, clickable chat source (FR-21): chunk → document/page/bbox. */
export interface ChatSourceDto {
  index: number;
  label: string;
  chunkId: string;
  documentId: string;
  filename: string;
  pageNumber: number;
  combinedPageNumber: number;
  bbox: BBox;
  /** PDF page size in points for FR-19 highlight scaling (null = no highlight). */
  pageWidth?: number | null;
  pageHeight?: number | null;
}

/** FR-19: chunk → viewer location, served by /projects/:id/chunks/:chunkId/location. */
export interface ChunkLocationDto {
  chunkId: string;
  documentId: string;
  filename: string;
  pageNumber: number;
  combinedPageNumber: number;
  bbox: BBox;
  pageWidth: number | null;
  pageHeight: number | null;
}

// --- Auth (Phase 5 RBAC) ---

export type ProjectRole = "owner" | "member";

export interface UserDto {
  id: string;
  email: string;
  /** Display name — derived from firstName + lastName on register/update. */
  name: string;
  firstName?: string | null;
  lastName?: string | null;
  company?: string | null;
  createdAt?: string | null;
}

export interface AuthResponseDto {
  token: string;
  user: UserDto;
}

export interface ProjectMemberDto extends UserDto {
  role: ProjectRole;
  addedAt: string;
}

export interface ChatResponseDto {
  sessionId: string;
  /** Answer text with [n] citation markers matching sources[].index. */
  answer: string;
  sources: ChatSourceDto[];
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
  scrapeRegion: "scrape-region",
  summarizePortion: "summarize-portion",
  summarizeProject: "summarize-project",
} as const;

export interface ProcessDocumentJob {
  projectId: string;
  documentId: string;
  spacesKey: string;
}

/** Apply the project's title-block region to its pages, then classify them. */
export interface ScrapeRegionJob {
  projectId: string;
  /** The worker discards the job when the stored region version moved on
   * (the user edited the box again while this one was queued). */
  regionVersion: number;
  /** Scope to one document — a new upload into a project that already has a
   * region. Omit to (re-)scrape the whole project. */
  documentId?: string;
}

/** Dry run: scrape a candidate box on a handful of sample pages so the user
 * can check it before paying for a whole-project scrape. Queued on the
 * scrape-region queue under the job name "preview"; the worker writes the
 * result to Redis and the API serves it from there — PDF bytes are never
 * touched inside an HTTP request. (The Redis key format lives with the route
 * that reads it, apps/api/src/routes/region.ts, mirroring how the summaries
 * cache key is arranged.) */
export interface RegionPreviewJob {
  projectId: string;
  previewId: string;
  box: RegionBox;
  sampleSize: number;
}

/** FR-10/12 on demand: summarize ONE discipline, because a user asked. */
export interface SummarizePortionJob {
  projectId: string;
  portionId: string;
  requestedById?: string;
}

export interface SummarizeProjectJob {
  projectId: string;
}

// --- Summaries (FR-10..13) ---

/** One statement of a summary; chunkIds are its FR-13 sources and page is the
 * combined page to jump to (derived from the first cited chunk). */
export interface SummaryItem {
  text: string;
  chunkIds: string[];
  page: number;
}

export interface SummaryContent {
  overview: string;
  items: SummaryItem[];
  /** Set on the project rollup when a portion underneath it changed after this
   * summary was written — the text is kept, the UI flags it. */
  stale?: boolean;
  /** page level only */
  documentId?: string;
  pageNumber?: number;
  combinedPage?: number;
  /** section level only */
  startPage?: number;
  endPage?: number;
}

export interface SummaryDto {
  id: string;
  projectId: string;
  portionId: string | null;
  level: SummaryLevel;
  summary: SummaryContent;
  sources: string[];
}

/**
 * What a summary run will cost, shown in the confirmation dialog before the
 * user spends anything. Call counts come from stored data; `outputTokens` is a
 * calibrated estimate, so treat the total as approximate.
 */
export interface SummaryEstimateDto {
  /** Portion estimates only. */
  portionName?: string;
  pages?: number;
  pagesToSummarize?: number;
  /** Pages whose summary already exists and will be reused for free. */
  reusedPageSummaries?: number;
  /** Project rollup only: how many discipline summaries feed it. */
  portionsUsed?: number;
  model: string;
  pageCalls: number;
  sectionCalls: number;
  portionCalls: number;
  totalCalls: number;
  inputTokens: number;
  outputTokens: number;
  costUsd: number;
}

export interface TokenTotals {
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  cacheWriteTokens: number;
  totalTokens: number;
  costUsd: number;
}

/** Stage that produced the tokens — matches the UsageKind enum. */
export type UsageKind = "chat" | "summary" | "classification" | "embedding";

export interface DashboardProjectRow {
  id: string;
  name: string;
  description: string | null;
  createdAt: string;
  documents: number;
  pages: number;
  status: "empty" | "processing" | "completed" | "failed";
  tokens: TokenTotals;
}

export interface DashboardDto {
  /** Length of the `activity` window, echoed back from the ?days= query. */
  activityDays: number;
  totals: {
    projects: number;
    documents: number;
    pages: number;
    chunks: number;
    summaries: number;
    chatMessages: number;
  };
  tokens: TokenTotals;
  tokensByKind: Partial<Record<UsageKind, TokenTotals>>;
  tokensByModel: Record<string, TokenTotals>;
  documentStatus: Record<string, number>;
  disciplines: { discipline: string; pages: number }[];
  projects: DashboardProjectRow[];
  activity: { date: string; totalTokens: number; costUsd: number }[];
  recentProjects: DashboardProjectRow[];
}

export interface SupportTicketDto {
  id: string;
  subject: string;
  message: string;
  name: string;
  email: string;
  createdAt: string;
}

/** Diagnosis for "processing finished but there is no summary". */
export interface SummaryStatusDto {
  summaries: Record<SummaryLevel, number>;
  portions: number;
  pagesWithChunks: number;
  documents: Record<string, number>;
  hint: string;
}
