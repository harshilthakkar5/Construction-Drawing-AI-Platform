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

/**
 * Roles a project can be created for — the disciplines whose questions the
 * reader cares about. Same vocabulary as `Discipline` so a role lines up with
 * the categories detected from sheet numbers, minus "other".
 */
export const PROJECT_ROLES: { value: Discipline; label: string }[] = [
  { value: "architectural", label: "Architectural" },
  { value: "structural", label: "Structural" },
  { value: "civil", label: "Civil" },
  { value: "landscape", label: "Landscape" },
  { value: "interiors", label: "Interiors" },
  { value: "mechanical", label: "Mechanical" },
  { value: "hvac", label: "HVAC" },
  { value: "plumbing", label: "Plumbing" },
  { value: "electrical", label: "Electrical" },
  { value: "fire_protection", label: "Fire Protection" },
  { value: "fire_alarm", label: "Fire Alarm" },
  { value: "telecommunications", label: "Telecommunications" },
  { value: "information_technology", label: "Information Technology" },
  { value: "audio_visual", label: "Audio Visual" },
  { value: "general", label: "General" },
];

export const PROJECT_ROLE_VALUES = PROJECT_ROLES.map((r) => r.value);

export const roleLabel = (value: string) =>
  PROJECT_ROLES.find((r) => r.value === value)?.label ?? value;

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

// --- Spaces/MinIO bucket layout ---

/**
 * The bucket layout, as templates rather than functions, because the Python
 * worker needs the same paths and this is the ONE place they are written.
 * `workers/src/generated.py` is emitted from these by packages/shared/codegen.mjs;
 * the TypeScript `objectKeys` below is derived from them too, so neither side
 * is a copy of the other.
 */
export const OBJECT_KEY_TEMPLATES = {
  originalPdf: "projects/{projectId}/pdfs/{documentId}/original.pdf",
  pageImage: "projects/{projectId}/pdfs/{documentId}/pages/{page}.png",
  pageThumb: "projects/{projectId}/pdfs/{documentId}/thumbs/{page}.jpg",
  pageText: "projects/{projectId}/pdfs/{documentId}/text/{page}.txt",
  /** A targeted RFI review's evidence picture — the exact image the model
   * saw, kept so the report shows what a finding was drawn from. */
  reviewEvidence: "projects/{projectId}/rfi-reviews/{runId}/evidence/{evidenceId}.png",
} as const;

function fillTemplate(
  template: string,
  vars: Record<string, string | number>,
): string {
  return template.replace(/\{(\w+)\}/g, (_match, key: string) => {
    const value = vars[key];
    if (value === undefined) throw new Error(`object key template needs ${key}`);
    return String(value);
  });
}

export const objectKeys = {
  originalPdf: (projectId: string, documentId: string) =>
    fillTemplate(OBJECT_KEY_TEMPLATES.originalPdf, { projectId, documentId }),
  pageImage: (projectId: string, documentId: string, page: number) =>
    fillTemplate(OBJECT_KEY_TEMPLATES.pageImage, { projectId, documentId, page }),
  pageThumb: (projectId: string, documentId: string, page: number) =>
    fillTemplate(OBJECT_KEY_TEMPLATES.pageThumb, { projectId, documentId, page }),
  pageText: (projectId: string, documentId: string, page: number) =>
    fillTemplate(OBJECT_KEY_TEMPLATES.pageText, { projectId, documentId, page }),
  reviewEvidence: (projectId: string, runId: string, evidenceId: string) =>
    fillTemplate(OBJECT_KEY_TEMPLATES.reviewEvidence, { projectId, runId, evidenceId }),
} as const;

// --- API DTOs ---

export interface ProjectDto {
  id: string;
  name: string;
  description: string | null;
  /** Disciplines the project is read for; steers summary emphasis only. */
  roles: string[];
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
  /** False keeps the document out of every RFI review scope — set
   * automatically for a document that looks like an RFI (the answer key is
   * never input), and switchable on the Docs tab. */
  includeInRfiAnalysis: boolean;
  rfiExclusionReason: string | null;
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
  /** The size the last run asked for — the dialog preselects it. */
  summaryDetail: SummaryDetail | null;
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
  /** Sheet number off the title block ("S-004"); null when unscraped. Already
   * folded into `label`, and exposed separately so the UI can show it apart
   * from the page number. */
  sheetNumber?: string | null;
  /** Discipline slug of the page the chunk sits on. */
  discipline?: string | null;
  /** "description" when the cited chunk is a vision model's account of the
   * drawing's geometry rather than text lifted off the sheet. The UI marks
   * those, because clicking through to a highlight whose words are nowhere on
   * the page would otherwise read as a broken citation. "gridmarks" when it is
   * the geometric reading of which mark is printed at which grid crossing
   * (workers/src/gridmarks.py) — measured, not written by a model. */
  kind?: string | null;
}

/**
 * What the labels in a gridmarks chunk ARE, decided by the discipline of the
 * sheet they are printed on.
 *
 * The geometric reader (workers/src/gridmarks.py) runs at ingest, before the
 * title block is scraped, so it cannot know. It places every mark-shaped word
 * at its crossing, and on a structural sheet those are elements — C-6, SR-8,
 * PC1, HSS8X8X1/4. On the client's architectural A3.01 the same shape is a
 * unit or room tag (A4B, S1B, B2), and the chat listed them as "marks printed
 * at intersections" beside the structural sheet's columns, as if they were
 * the same kind of thing. Known and not structural → "tags". Unknown (not yet
 * scraped, or "other") stays "marks", the reader's own neutral word.
 *
 * The API tells the model and the chat's source chip tells the person; the
 * worker's summary prompt mirrors it as `gridmarks.grid_label_kind`, and both
 * sides test against packages/shared/fixtures/grid-label-kind.json.
 */
export type GridLabelKind = "marks" | "tags";

export function gridLabelKind(discipline: string | null | undefined): GridLabelKind {
  const d = discipline?.trim().toLowerCase();
  if (!d || d === "structural" || d === "other") return "marks";
  return "tags";
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

/** One past conversation in a project's chat history picker. */
export interface ChatSessionDto {
  id: string;
  createdAt: string;
  /** Last activity — what the history window filters on. */
  lastMessageAt: string;
  messageCount: number;
  /** The session's first question, as its title in the list. */
  preview: string;
}

/** A persisted turn (FR-23), as the history picker replays it. */
export interface ChatMessageDto {
  id: string;
  role: "user" | "assistant";
  content: Record<string, unknown>;
  sources?: ChatSourceDto[] | null;
  createdAt: string;
}

/** Time windows the history picker offers. "custom" takes from/to instead. */
export const CHAT_HISTORY_WINDOWS = ["1h", "24h", "7d", "30d", "3m", "all", "custom"] as const;
export type ChatHistoryWindow = (typeof CHAT_HISTORY_WINDOWS)[number];

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
  rfiScan: "rfi-scan",
  rfiReview: "rfi-review",
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
 * scrape-region queue under the job name "preview"; the worker's return value
 * IS the result, which the API reads back off the job — PDF bytes are never
 * touched inside an HTTP request. */
export interface RegionPreviewJob {
  projectId: string;
  box: RegionBox;
  sampleSize: number;
}

/** FR-10/12 on demand: summarize ONE discipline, because a user asked. */
export interface SummarizePortionJob {
  projectId: string;
  portionId: string;
  requestedById?: string;
  /** SUMMARY_DETAILS key; omitted = the worker's SUMMARY_DETAIL default. */
  detail?: SummaryDetail;
}

export interface SummarizeProjectJob {
  projectId: string;
  detail?: SummaryDetail;
}

/**
 * How big a summary is: at most `points` highlights and an overview of the
 * given length. Applies to what the user READS — the section, discipline and
 * project rollups. Page summaries stay at the standard size because every
 * later run reuses them.
 *
 * MIRRORED as DETAIL_LEVELS in workers/src/summarize.py, which writes the
 * summaries; test_summarize reads this block and fails on a drift, because
 * the dialog would otherwise price and promise a size the worker never writes.
 */
export const SUMMARY_DETAILS = {
  brief: { label: "Brief", points: 5, overview: "1-2 sentence" },
  standard: { label: "Standard", points: 8, overview: "1-3 sentence" },
  detailed: { label: "Detailed", points: 15, overview: "3-5 sentence" },
  full: { label: "Full", points: 25, overview: "4-6 sentence" },
} as const;
export type SummaryDetail = keyof typeof SUMMARY_DETAILS;
export const SUMMARY_DETAIL_KEYS = Object.keys(SUMMARY_DETAILS) as SummaryDetail[];

/** Scan a project's drawings for RFI-worthy gaps. The API creates the
 * rfi_scans row first and passes its id, so the worker reports into a row the
 * UI is already polling rather than inventing one. */
export interface RfiScanJob {
  projectId: string;
  scanId: string;
}

/** Run one targeted RFI review. Everything else — target, checks, the
 * approved scope, thinking — lives on the rfi_review_runs row, so a retry
 * reads the same immutable plan the user approved. */
export interface RfiReviewJob {
  runId: string;
}

/**
 * The wire shape of each job, as data — because the Python worker parses these
 * payloads and TypeScript interfaces do not survive to runtime.
 * `workers/src/generated.py` is emitted from this.
 *
 * `jobFields<T>()` makes drift a COMPILE error rather than a production one: it
 * rejects a name that is not a key of the interface (catching a rename or a
 * deletion), and its return type collapses to `never` if any key of the
 * interface is missing from the list (catching a field added on the TypeScript
 * side that the worker would then silently never read).
 */
/**
 * `unknown` (harmless) when F covers every key of T, otherwise a shape a plain
 * array literal cannot satisfy — so the error names the field that was left
 * out. It has to constrain the ARGUMENT: as a return type the resulting
 * `never` is assigned to nothing and TypeScript stays quiet.
 */
type Complete<F extends readonly string[], T> = [
  Exclude<keyof T & string, F[number]>,
] extends [never]
  ? unknown
  : { __missingFromJobFields: Exclude<keyof T & string, F[number]> };

function jobFields<T>() {
  return <F extends readonly (keyof T & string)[]>(
    fields: F & Complete<F, T>,
    optional: readonly F[number][] = [],
  ): { fields: readonly string[]; optional: readonly string[] } => ({
    fields,
    optional,
  });
}

export const JOB_FIELDS = {
  processDocument: jobFields<ProcessDocumentJob>()([
    "projectId",
    "documentId",
    "spacesKey",
  ]),
  scrapeRegion: jobFields<ScrapeRegionJob>()(
    ["projectId", "regionVersion", "documentId"],
    ["documentId"],
  ),
  summarizePortion: jobFields<SummarizePortionJob>()(
    ["projectId", "portionId", "requestedById", "detail"],
    ["requestedById", "detail"],
  ),
  summarizeProject: jobFields<SummarizeProjectJob>()(["projectId", "detail"], ["detail"]),
  rfiScan: jobFields<RfiScanJob>()(["projectId", "scanId"]),
  rfiReview: jobFields<RfiReviewJob>()(["runId"]),
} as const;

/** Field types the generator needs to emit a correct Python cast. */
export const JOB_FIELD_TYPES: Record<string, "str" | "int"> = {
  regionVersion: "int",
};

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
  /** Rollups: the size this summary was written at (SUMMARY_DETAILS). Absent
   * on summaries written before sizes existed, which were all "standard". */
  detail?: SummaryDetail;
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
  /** The model that will actually run — follows SUMMARY_PROVIDER. */
  model: string;
  /** The size this estimate was priced at. */
  detail: SummaryDetail;
  /** SUMMARY_THINKING on the worker's side of the env, or null for the global
   * default. Reasoning tokens bill as output and are NOT in the estimate. */
  thinking: string | null;
  /** True when the page tier goes through a batch, billing it at half price. */
  batched: boolean;
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

/** Stage that produced the tokens — matches the UsageKind enum in
 * apps/api/prisma/schema.prisma. This copy had drifted: "rerank" has been a
 * value of that enum for as long as rerank.ts has recorded one, and its
 * absence here meant reranker spend reached the dashboard with no label to
 * render it under. */
export type UsageKind =
  | "chat"
  | "summary"
  | "classification"
  | "embedding"
  | "rerank"
  | "vlm"
  | "rfi";

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

// --- RFIs ---

/**
 * An RFI's lifecycle. Mirrored from the `RfiStatus` enum in schema.prisma, and
 * `rfi.test.ts` reads that file to fail on a drift.
 *
 * The mirror is checked rather than trusted because this repo has already paid
 * for the unchecked version: `rerank` lived in the `UsageKind` enum and was
 * missing from its union here, so reranker spend reached the dashboard with no
 * label to render it under, silently, for as long as the feature existed.
 *
 * `voided` and not the industry's "void": the word is a TypeScript keyword, and
 * an RFI withdrawn after issue still keeps its number so the gap in the log
 * stays explainable.
 */
export type RfiStatus = "draft" | "open" | "answered" | "closed" | "voided";

export type RfiPriority = "low" | "normal" | "high" | "critical";

export const RFI_STATUSES: RfiStatus[] = ["draft", "open", "answered", "closed", "voided"];
export const RFI_PRIORITIES: RfiPriority[] = ["low", "normal", "high", "critical"];

/**
 * Audit vocabulary for `rfi_events.kind`, which is a TEXT column rather than a
 * Postgres enum. That is the opposite of the choice made for RfiStatus, and
 * deliberately: a status is a closed set that the API, this union and the UI
 * all branch on, while event kinds GROW with every phase that touches an RFI
 * and nothing branches on them. An enum would mean a migration per phase for a
 * column that is only ever read back as a list.
 */
export const RFI_EVENT_KINDS = [
  "created",
  "updated",
  "status_changed",
  "answered",
  "reopened",
  "location_added",
  "location_removed",
  "exported",
] as const;
export type RfiEventKind = (typeof RFI_EVENT_KINDS)[number];

/**
 * Where an RFI was asked, pinned to (documentId, pageNumber, bbox) and never
 * to a chunkId — chunk uuids are re-minted on every ingest.
 *
 * `sheetNumber` and `combinedPageNumber` are snapshots taken when the pin was
 * made, not joins: they are what the Excel export prints, and what survives
 * the pinned document being deleted.
 */
export interface RfiLocationDto {
  id: string;
  documentId: string | null;
  filename: string | null;
  pageNumber: number;
  combinedPageNumber: number | null;
  bbox: BBox | null;
  sheetNumber: string | null;
  /** FR-4: the pinned sheet has a newer revision. A person confirms the re-pin;
   * the geometry may have moved, and that move may be what the RFI is about. */
  drawingRevised: boolean;
  supersededById: string | null;
  createdAt: string;
}

export interface RfiEventDto {
  id: string;
  kind: string;
  detail: unknown;
  actorId: string | null;
  actorName: string | null;
  createdAt: string;
}

export interface RfiDto {
  id: string;
  projectId: string;
  /** Per-project, monotonic, never reused — it is quoted in correspondence. */
  number: number;
  subject: string;
  question: string;
  status: RfiStatus;
  priority: RfiPriority;
  discipline: string | null;
  dueAt: string | null;
  createdById: string | null;
  createdByName: string | null;
  assignedToId: string | null;
  assignedToName: string | null;
  /** "manual" or "generated" — see RfiCandidateDto. */
  source: string;
  /** For a generated RFI, the check that found it (RFI_CHECK_LABELS). */
  checkType: string | null;
  /** Human-authored, always. No model writes here. */
  answer: string | null;
  answeredById: string | null;
  answeredByName: string | null;
  answeredAt: string | null;
  closedAt: string | null;
  createdAt: string;
  updatedAt: string;
  locations: RfiLocationDto[];
  /** Only on the detail read; the list endpoint leaves it out. */
  events?: RfiEventDto[];
}

// --- Generated RFIs ---

/** Mirrors the `RfiScanStatus` enum; rfi.test.ts fails on a drift. */
export type RfiScanStatus = "queued" | "running" | "completed" | "failed";
export const RFI_SCAN_STATUSES: RfiScanStatus[] = ["queued", "running", "completed", "failed"];

/** Mirrors `RfiCandidateStatus`. A candidate has no number until accepted. */
export type RfiCandidateStatus = "pending" | "accepted" | "dismissed";
export const RFI_CANDIDATE_STATUSES: RfiCandidateStatus[] = ["pending", "accepted", "dismissed"];

/** Mirrors `RfiConfidence`. Set by which CHECK fired and its guards, never by
 * a model rating itself. */
export type RfiConfidence = "high" | "medium" | "low";
export const RFI_CONFIDENCES: RfiConfidence[] = ["high", "medium", "low"];

/**
 * What each check looks for, in the words the review list shows. The keys are
 * written by workers/src/rfi_checks.py (CHECK_TYPES there), and the worker's
 * test reads this file to fail when the two disagree — a check the UI cannot
 * name would render as its raw key.
 */
export const RFI_CHECK_LABELS = {
  dangling_reference: "Sheet referenced but not in the set",
  unscheduled_mark: "Mark with no row in its schedule",
  open_item_note: "Note left open on the drawing (TBD / verify)",
  grid_mismatch: "Grid line named differently between drawings",
  // Targeted review only (workers/src/rfi_columns.py): it needs two named
  // sheets laid over each other, which a whole-project scan does not have.
  column_mismatch: "Column drawn differently on two drawings",
} as const;
export type RfiCheckType = keyof typeof RFI_CHECK_LABELS;

/** Where a finding sits on the drawings — page + bbox, like rfi_locations. */
export interface RfiEvidenceDto {
  documentId: string;
  pageNumber: number;
  combinedPageNumber: number | null;
  sheetNumber: string | null;
  bbox: BBox | null;
  /** Informational only: chunk ids are re-minted on every ingest. */
  chunkId: string | null;
  /** The drawing's own words at that spot — what a reviewer checks. */
  quote: string;
  /** "finding" is where the gap is; "context" is what it was checked against
   * (the schedule a mark is missing from). */
  role?: "finding" | "context";
  /** Targeted review only: which evidence item this was (text, gridmarks,
   * description, a whole-page image or a close-up), how far it can be trusted,
   * which named sheet it belongs to, and what the review said it shows. */
  kind?: "text" | "gridmarks" | "description" | "page" | "crop";
  sourceTrust?: "project_text" | "geometry" | "visual" | "model_description";
  side?: string | null;
  observation?: string | null;
}

export interface RfiCandidateDto {
  id: string;
  checkType: string;
  confidence: RfiConfidence;
  subject: string;
  question: string;
  /** "model" when the AI worded it, "template" when the check's own
   * sentence was used (no key, a refused reply, or a reply naming something
   * the evidence does not contain). */
  questionSource: string;
  evidence: RfiEvidenceDto[];
  status: RfiCandidateStatus;
  rfiId: string | null;
  createdAt: string;
  /** Who found it: the deterministic project scan, or a targeted review. */
  origin: RfiCandidateOrigin;
  /** The targeted review run that found it; null for a scan finding. */
  reviewRunId: string | null;
  /** One sentence: why a targeted review flagged this. Null for scan findings,
   * whose checks explain themselves in the evidence. */
  reasoning: string | null;
  /** Suggested by the targeted review; the accepted RFI still starts at the
   * RFI's own default unless a person changes it. */
  priority: RfiPriority | null;
}

export type RfiCandidateOrigin = "deterministic_scan" | "targeted_review";

export interface RfiScanDto {
  id: string;
  status: RfiScanStatus;
  findings: number;
  modelWorded: number;
  byCheck: Record<string, number> | null;
  /** Checks that did not run, and why — shown, because "found nothing" and
   * "could not look" otherwise read the same. */
  notes: string[];
  /** What the wording step cost — null when it never ran (no new findings,
   * AI wording off, a scan from before this was recorded). */
  usage: RfiScanUsageDto | null;
  /** A "Rescan from scratch" rather than an ordinary scan. */
  fresh: boolean;
  error: string | null;
  startedAt: string | null;
  finishedAt: string | null;
  createdAt: string;
}

/** One scan's wording calls, written by the worker (workers/src/rfi_scan.py
 * WordingUsage.as_json) and priced by the API. */
export interface RfiScanUsageDto {
  provider: string | null;
  model: string | null;
  /** RFI_THINKING as configured; null means the global defaults decided. */
  thinkingSetting: string | null;
  /** What was really sent after any refusal ladder, e.g. "thinking_level=low". */
  thinkingSent: string[];
  /** The model refused thinkingSetting and ran at the nearest one it takes. */
  thinkingAdjusted: boolean;
  calls: number;
  failedCalls: number;
  inputTokens: number;
  /** Includes the thinking — both vendors bill reasoning as output. */
  outputTokens: number;
  /** The reasoning share of outputTokens; null where the provider does not
   * report it separately (Anthropic). */
  thinkingTokens: number | null;
  cacheReadTokens: number;
  cacheWriteTokens: number;
  /** Estimated from the model's published rate (apps/api/src/usage.ts). */
  costUsd: number | null;
}

/** Everything the project has spent on RFI wording, from usage_events. */
export interface RfiUsageTotalsDto {
  calls: number;
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  cacheWriteTokens: number;
  costUsd: number;
  byModel: { model: string; calls: number; inputTokens: number; outputTokens: number; costUsd: number }[];
}

// --- Targeted RFI review (docs/rfi-targeted-review.md) ---

/**
 * The review catalogue: the 16 ORIGINAL questions (fixtures/rfi-original-
 * questions.json holds the verbatim golden copy; tests on both sides of the
 * process boundary fail when this drifts from it). These are REVIEW
 * OBJECTIVES, not sixteen instructions to manufacture RFIs: a review inventories
 * what each asks for and proposes a candidate only for a supported conflict or
 * an unresolved construction-critical gap.
 *
 * As DATA — the API routes and prices it, the worker writes it into prompts,
 * the UI names it. The Python copy is GENERATED into workers/src/generated.py
 * (the worker image does not ship packages/), and RFI_REVIEW_CATALOGUE_VERSION
 * is stored on every run so a result can be read against the catalogue that
 * produced it.
 *
 *   requiredObservations  the fields discovery must report per instance
 *   query                 what the planner hands hybrid retrieval (the model
 *                         never writes a retrieval query)
 *   applicability         `always` checks run on every auto plan; the others
 *                         when a keyword of theirs appears in the target
 *   comparisonRules       what counts as the SAME thing across two sources
 *   candidateRules        when a difference is an RFI and when it is not
 *   deterministic         an exact check (no model) deciding the same question:
 *                         G01 the scan's grid comparison, C01 the column overlay
 *   geometryAids          exact measurements handed to the model as evidence
 *                         (never as findings): C03's column-to-grid offsets
 */
export const RFI_REVIEW_CATALOGUE_VERSION = "2026-09-30.1";

export const RFI_REVIEW_CHECKS = [
  {
    id: "G01",
    sourceSection: "General",
    sourceQuestionNumber: 1,
    originalQuestion:
      "what are the Grid to Grid Dimensions, compare the grids with architecture if there is any mismatch isolate and highlight",
    label: "Grid names and grid-to-grid dimensions",
    requiredObservations: ["grid label", "grid pair", "dimension between the pair", "units", "level/view", "which end of the line"],
    query: "grid line grid dimension spacing",
    applicability: { always: true, keywords: [] },
    comparisonRules:
      "Compare architectural and structural grids for the SAME area and level. Resolve renamed or jogged grids, orientation and explicit offsets before calling a mismatch.",
    candidateRules:
      "A candidate needs the same grid pair dimensioned differently, or one grid line named differently, on two localized sources.",
    deterministic: "grid_mismatch",
    geometryAids: [],
  },
  {
    id: "G02",
    sourceSection: "General",
    sourceQuestionNumber: 2,
    originalQuestion:
      "Find all the Level and there elevation from the drawings set, compare the Levels with architecture if there is any mismatch isolate and highlight",
    label: "Levels and their elevations",
    requiredObservations: ["level name", "elevation", "units", "vertical datum", "source view"],
    query: "level elevation datum T.O.S. top of slab finish floor",
    applicability: { always: true, keywords: [] },
    comparisonRules:
      "Normalize level aliases (LEVEL 5 / L5 / 5TH FLOOR) and datums before comparing. Structural top of slab and architectural finish floor differ by design; compare like with like.",
    candidateRules: "A candidate needs one level given two different elevations on the same datum by two localized sources.",
    deterministic: null,
    geometryAids: [],
  },
  {
    id: "F01",
    sourceSection: "Foundations",
    sourceQuestionNumber: 1,
    originalQuestion:
      "What are all the isolated footing types in the floor plans and foundation schedules, location sizes, and elevation.",
    label: "Isolated footings",
    requiredObservations: ["footing mark/type", "instance location from grids", "size", "elevation", "schedule row"],
    query: "isolated footing spread footing schedule F1 size elevation",
    applicability: { always: false, keywords: ["FOOTING", "FOUNDATION", "FTG", "SPREAD"] },
    comparisonRules:
      "Match plan instances to schedule rows by mark. A size given by type in the schedule is not missing from the instance.",
    candidateRules:
      "A candidate needs a mark with no schedule row, a schedule size contradicted elsewhere, or an instance with no locating information after plans, schedules and details were searched.",
    deterministic: null,
    geometryAids: [],
  },
  {
    id: "F02",
    sourceSection: "Foundations",
    sourceQuestionNumber: 2,
    originalQuestion:
      "What are all the Wall footing in the floor plans and foundation schedule, also find there size location and elevation from grids.",
    label: "Wall footings",
    requiredObservations: ["wall footing mark/type", "segment extent", "size", "location from grids", "elevation", "reference face or centerline"],
    query: "wall footing continuous footing strip footing schedule size elevation",
    applicability: { always: false, keywords: ["WALL FOOTING", "CONTINUOUS FOOTING", "STRIP FOOTING", "WF", "FOUNDATION"] },
    comparisonRules: "Match segments and extents; compare only the same reference (face vs centerline).",
    candidateRules: "A candidate needs a segment whose size, location or elevation conflicts between sources, or cannot be located after the relevant sections were searched.",
    deterministic: null,
    geometryAids: [],
  },
  {
    id: "F03",
    sourceSection: "Foundations",
    sourceQuestionNumber: 3,
    originalQuestion: "what are all the Mat foundation in floor plans and foundation schedules.",
    label: "Mat foundations",
    requiredObservations: ["mat mark", "presence on plan", "schedule row", "stated extents, thickness and elevation where given"],
    query: "mat foundation mat slab raft schedule thickness",
    applicability: { always: false, keywords: ["MAT", "RAFT", "FOUNDATION"] },
    comparisonRules: "Reconcile presence and identity between plans and schedules. Extents and thickness are supporting attributes, not mandatory fields.",
    candidateRules: "A candidate needs a mat on one source that the other contradicts or omits.",
    deterministic: null,
    geometryAids: [],
  },
  {
    id: "F04",
    sourceSection: "Foundations",
    sourceQuestionNumber: 4,
    originalQuestion: "Are there any piles in the floor plans, if so what are the size and location.",
    label: "Piles",
    requiredObservations: ["pile presence", "pile mark/type", "size", "location from grids", "pile cap it belongs to"],
    query: "pile pile cap pile schedule diameter capacity location",
    applicability: { always: false, keywords: ["PILE", "PC", "CAISSON", "PIER", "FOUNDATION"] },
    comparisonRules: "Distinguish individual piles from pile caps. Match pile types to their legend or schedule.",
    candidateRules: "A candidate needs a pile or pile cap whose size or location conflicts between sources, or cannot be located.",
    deterministic: null,
    geometryAids: [],
  },
  {
    id: "FL01",
    sourceSection: "Floor",
    sourceQuestionNumber: 1,
    originalQuestion: "What are the floor edges location from Grids",
    label: "Floor edge locations",
    requiredObservations: ["edge segment", "reference grid", "offset", "face/edge convention"],
    query: "slab edge floor edge dimension from grid edge of slab",
    applicability: { always: false, keywords: ["SLAB", "FLOOR", "EDGE", "PLAN", "LEVEL"] },
    comparisonRules: "Compare the same edge on corresponding architectural and structural plans; account for intentional setbacks.",
    candidateRules: "A candidate needs one edge located differently by two sources, or an edge with no locating dimension after enlarged plans and details were searched.",
    deterministic: null,
    geometryAids: [],
  },
  {
    id: "FL02",
    sourceSection: "Floor",
    sourceQuestionNumber: 2,
    originalQuestion: "What are the Floor top Elevation and Thickness",
    label: "Floor top elevation and thickness",
    requiredObservations: ["slab zone", "top elevation", "thickness", "units", "datum"],
    query: "slab thickness top of slab elevation T.O.S. floor elevation",
    applicability: { always: false, keywords: ["SLAB", "FLOOR", "T.O.S", "TOS", "LEVEL", "PLAN"] },
    comparisonRules: "Distinguish structural top of slab from finish floor, and local thickening from the typical thickness.",
    candidateRules: "A candidate needs one zone given two different elevations or thicknesses by two sources on the same datum.",
    deterministic: null,
    geometryAids: [],
  },
  {
    id: "FL03",
    sourceSection: "Floor",
    sourceQuestionNumber: 3,
    originalQuestion: "are there any stepping in floor, if yes what are the location and elevation different at stepping",
    label: "Floor steps",
    requiredObservations: ["step location/extent", "elevation each side", "difference (derived, with its operands)"],
    query: "slab step depression drop elevation change recess",
    applicability: { always: false, keywords: ["STEP", "DEPRESS", "RECESS", "DROP", "SLAB", "FLOOR"] },
    comparisonRules: "A documented step is an inventory item, not an RFI. Compare the step on plan with its section.",
    candidateRules: "A candidate needs a step whose elevations disagree between plan and section, or a step with no elevations anywhere searched.",
    deterministic: null,
    geometryAids: [],
  },
  {
    id: "FL04",
    sourceSection: "Floor",
    sourceQuestionNumber: 4,
    originalQuestion: "are there any slop on the floor, if yes what is the sloping direction span and sloping percentage",
    label: "Floor slopes",
    requiredObservations: ["sloped zone", "direction", "horizontal run", "rise or slope as printed", "percentage (derived only when rise and run are both supported)"],
    query: "slope slope to drain per foot ramp spot elevation",
    applicability: { always: false, keywords: ["SLOPE", "RAMP", "DRAIN", "PER FT", "%"] },
    comparisonRules: "Convert ratio to percent only with a known convention: 100 x rise / horizontal run, marked derived.",
    candidateRules: "A candidate needs a slope whose direction, run or rate conflicts between sources, or a sloped zone with no rate anywhere searched.",
    deterministic: null,
    geometryAids: [],
  },
  {
    id: "C01",
    sourceSection: "Columns & Shear walls",
    sourceQuestionNumber: 1,
    originalQuestion:
      "Find all the columns in the floor plans and columns schedule, find sizes, location from Grids and base and top elevation of all the columns.",
    label: "Columns: size, location, base and top",
    requiredObservations: ["column mark", "instance location from grids", "size", "base elevation", "top elevation", "schedule row"],
    query: "column schedule column size mark location grid base top elevation",
    applicability: { always: false, keywords: ["COLUMN", "COL", "C-", "PLAN", "FRAMING"] },
    comparisonRules: "Match instances by mark AND level. Size changes up the building and transfers are not conflicts.",
    candidateRules: "A candidate needs a column shown at a different place or size on two sources for the same level, or with no base/top elevation after the schedule and sections were searched.",
    deterministic: "column_mismatch",
    geometryAids: [],
  },
  {
    id: "C02",
    sourceSection: "Columns & Shear walls",
    sourceQuestionNumber: 2,
    originalQuestion:
      "Find all the shear Wall or Core wall in the floor plan, find sizes, location from Grids and base and top elevation of all the Core Walls.",
    label: "Shear and core walls",
    requiredObservations: ["wall mark", "thickness", "location from grids (face or centerline)", "base elevation", "top elevation"],
    query: "core wall shear wall location grid thickness wall schedule",
    applicability: { always: false, keywords: ["CORE", "SHEAR WALL", "SHEARWALL", "SW-", "WALL"] },
    comparisonRules: "Localize wall faces or centerlines consistently and compare corresponding views of the same level.",
    candidateRules: "A candidate needs a wall at a different place or thickness on two sources, or with no locating dimension after sections were searched.",
    deterministic: null,
    geometryAids: [],
  },
  {
    id: "C03",
    sourceSection: "Columns & Shear walls",
    sourceQuestionNumber: 3,
    originalQuestion:
      "if any column is centered to grid, most probably it is not dimensioned from grid, but if any column is off from the grids and not dimensioned from grid isolate and highlight.",
    label: "Off-grid columns without a locating dimension",
    requiredObservations: ["column mark", "centred on the grid crossing or not", "governing locating dimension if off grid", "where that dimension is"],
    query: "column offset from grid dimension column location enlarged plan",
    applicability: { always: false, keywords: ["COLUMN", "COL", "C-", "PLAN", "FRAMING"] },
    comparisonRules:
      "A centred column needs no offset dimension. An off-grid column is located only by a printed dimension, note, enlarged detail or schedule — never by a measurement of the drawing.",
    candidateRules:
      "A candidate needs an off-grid column with no locating dimension after dimension chains, notes, enlarged details and schedules were searched. Unreadable is insufficient evidence, not a candidate.",
    deterministic: null,
    geometryAids: ["column_grid_offsets"],
  },
  {
    id: "B01",
    sourceSection: "Beams",
    sourceQuestionNumber: 1,
    originalQuestion: "what is the start and end of the beams with reference to grids",
    label: "Beam start and end",
    requiredObservations: ["beam mark", "start location from grids", "end location from grids", "endpoint convention (support, face or centerline)"],
    query: "beam framing plan beam span support grid",
    applicability: { always: false, keywords: ["BEAM", "FRAMING", "GIRDER", "JOIST", "B-"] },
    comparisonRules: "Compare only equivalent endpoint references (support vs face vs centerline).",
    candidateRules: "A candidate needs a beam whose ends are located differently by two sources, or cannot be located after framing details were searched.",
    deterministic: null,
    geometryAids: [],
  },
  {
    id: "B02",
    sourceSection: "Beams",
    sourceQuestionNumber: 2,
    originalQuestion: "what is the size of the beam.",
    label: "Beam size",
    requiredObservations: ["beam mark/type", "size or section", "units"],
    query: "beam schedule beam size section W shape depth",
    applicability: { always: false, keywords: ["BEAM", "FRAMING", "GIRDER", "JOIST", "B-"] },
    comparisonRules: "Reconcile framing tags, schedules and sections, including legitimate variation along one member.",
    candidateRules: "A candidate needs one beam given two sizes by two sources, or a tagged beam with no size anywhere searched.",
    deterministic: null,
    geometryAids: [],
  },
  {
    id: "B03",
    sourceSection: "Beams",
    sourceQuestionNumber: 3,
    originalQuestion: "what is the elevation of the beams.",
    label: "Beam elevation",
    requiredObservations: ["beam mark", "elevation", "datum", "reference surface (top, bottom or centerline)"],
    query: "beam elevation top of beam T.O.B. bottom of beam soffit",
    applicability: { always: false, keywords: ["BEAM", "FRAMING", "GIRDER", "T.O.B", "TOB"] },
    comparisonRules: "Distinguish top, bottom and centerline; account for documented slopes and steps.",
    candidateRules: "A candidate needs one beam at two elevations on the same reference by two sources, or no elevation anywhere searched.",
    deterministic: null,
    geometryAids: [],
  },
] as const;
export type RfiReviewCheckId = (typeof RFI_REVIEW_CHECKS)[number]["id"];
export const RFI_REVIEW_CHECK_IDS = RFI_REVIEW_CHECKS.map((c) => c.id) as RfiReviewCheckId[];

/**
 * How much one review may look at and spend. CAPS, not quotas: fewer
 * relevant sources beat a quota filled with weak ones. Read by the API planner
 * (scope and estimate) and the worker (crops, batches, token ceiling) from the
 * same generated values, so an estimate cannot price a scope the worker then
 * does not build.
 *
 *   hitsPerQuery     retrieval hits per check query
 *   referencePages   sheets a target REFERENCES that are pulled in before ranking
 *   pairWindows      the SAME area cut from two sheets that line up (two images each)
 *   maxBatches       discovery calls at most; evidence beyond them is OMITTED, visibly
 *   maxTotalTokens   input + output across every call of the run; reaching it stops
 *                    the run as `partial`
 */
export const RFI_REVIEW_DEPTHS = {
  quick: {
    label: "Quick", chunks: 24, visualPages: 4, cropsPerPage: 2, pairWindows: 2,
    hitsPerQuery: 12, referencePages: 2, maxBatches: 1, maxTotalTokens: 200_000,
  },
  standard: {
    label: "Standard", chunks: 48, visualPages: 8, cropsPerPage: 3, pairWindows: 4,
    hitsPerQuery: 24, referencePages: 4, maxBatches: 2, maxTotalTokens: 500_000,
  },
  deep: {
    label: "Deep", chunks: 96, visualPages: 14, cropsPerPage: 4, pairWindows: 6,
    hitsPerQuery: 36, referencePages: 8, maxBatches: 4, maxTotalTokens: 1_200_000,
  },
} as const;
export type RfiReviewDepth = keyof typeof RFI_REVIEW_DEPTHS;
export const RFI_REVIEW_DEPTH_KEYS = Object.keys(RFI_REVIEW_DEPTHS) as RfiReviewDepth[];

/** Bounds on the per-call input limit a person may set. */
export const RFI_REVIEW_INPUT_LIMITS = { min: 20_000, max: 400_000, default: 120_000 } as const;
/** Bounds on a numeric thinking ceiling, where the model takes one. */
export const RFI_REVIEW_THINKING_LIMITS = { min: 1_024, max: 32_000 } as const;

/** Thinking effort a person may pick — the RFI_THINKING vocabulary. */
export const RFI_REVIEW_THINKING = ["low", "medium", "high"] as const;
export type RfiReviewThinking = (typeof RFI_REVIEW_THINKING)[number];

export type RfiReviewProvider = "claude" | "gemini";

/**
 * What a model lets a review control about its reasoning. A VERSION SNIFF, the
 * same one workers/src/llm.py makes (`_claude_takes_effort`,
 * `_takes_thinking_level`); both read fixtures/thinking-capability.json so the
 * dialog cannot offer a control the transport would then ignore.
 *
 *   effort  Low/Medium/High means something on this model (every model here)
 *   budget  a numeric thinking ceiling is honoured: Claude before 4.6 and
 *           Gemini before 3. Newer models take an effort or a level instead,
 *           and a number sent to them would be silently re-mapped.
 */
export function reviewThinkingCapability(provider: RfiReviewProvider, model: string): { effort: boolean; budget: boolean; note: string } {
  if (provider === "gemini") {
    const major = Number(/gemini-(\d+)/.exec(model)?.[1] ?? 0);
    return major >= 3
      ? { effort: true, budget: false, note: "Gemini 3 and later take a thinking LEVEL, not a token budget." }
      : { effort: true, budget: true, note: "This Gemini model takes a thinking token budget." };
  }
  if (/^claude-\d/.test(model)) return { effort: true, budget: true, note: "This Claude model takes a thinking token budget." };
  const found = /^claude-[a-z]+-(\d+)(?:-(\d+))?/.exec(model);
  if (!found) return { effort: true, budget: false, note: "Claude 4.6 and later take an effort, not a token budget." };
  const major = Number(found[1]);
  const minor = found[2] && found[2].length <= 2 ? Number(found[2]) : 0;
  const effortModel = major > 4 || (major === 4 && minor >= 6);
  return effortModel
    ? { effort: true, budget: false, note: "Claude 4.6 and later take an effort, not a token budget." }
    : { effort: true, budget: true, note: "This Claude model takes a thinking token budget." };
}

export type RfiReviewTarget =
  | { type: "sheet"; value: string }
  | { type: "element"; value: string; level?: string; area?: string }
  | { type: "compare"; values: string[] };

export type RfiReviewCheckMode = "auto" | "custom" | "all_original";

export type RfiReviewStatus =
  | "planned"
  | "queued"
  | "running"
  | "ready"
  | "partial"
  | "failed"
  | "cancelled"
  | "stale";
export const RFI_REVIEW_STATUSES: RfiReviewStatus[] = [
  "planned",
  "queued",
  "running",
  "ready",
  "partial",
  "failed",
  "cancelled",
  "stale",
];

export type RfiReviewStage = "planning" | "discovery" | "reasoning" | "verification" | "saving";

/** Whether a check applies to the planned scope, decided before anything is spent. */
export type RfiCheckApplicability = "applicable" | "unknown" | "not_applicable";

/**
 * What became of each of the 16 questions in one run. `not_selected` is NEVER
 * a pass, and `complete_no_issue` is a claim about the scope reviewed, not
 * about the project. A run with only some checks completed says so check by
 * check rather than rolling them into one status.
 */
export type RfiCheckOutcome =
  | "complete_no_issue"
  | "candidate_found"
  | "insufficient_evidence"
  | "not_applicable"
  | "failed"
  | "not_selected";
export const RFI_CHECK_OUTCOMES: RfiCheckOutcome[] = [
  "complete_no_issue",
  "candidate_found",
  "insufficient_evidence",
  "not_applicable",
  "failed",
  "not_selected",
];

export interface RfiCheckPlanDto {
  selected: boolean;
  applicability: RfiCheckApplicability;
  reason: string;
}

export interface RfiCheckResultDto {
  outcome: RfiCheckOutcome;
  reason: string;
  observations: number;
  candidates: number;
  gaps: string[];
}

/** One element a review inventoried, field by field. */
export interface RfiInventoryItemDto {
  checkId: string;
  entity: string;
  location: string | null;
  fields: { name: string; value: string | null; state: "supported" | "unknown" | "not_applicable"; evidenceIds: string[]; derived: boolean }[];
}

/** One page in a review's scope, as the plan screen shows it. */
export interface RfiReviewPageDto {
  pageId: string;
  documentId: string;
  pageNumber: number;
  combinedPageNumber: number | null;
  sheetNumber: string | null;
  discipline: string | null;
  /** "target" / "side:N" / "element" pages are what was asked about and are
   * never dropped by a cap; "reference" pages are sheets the target points
   * at; "related" pages were found by retrieval. */
  role: string;
  /** Why the page is in the scope, in words. */
  reason: string;
  /** Rendered and sent as images (whole-page overview + crops). */
  visual: boolean;
  chunks: number;
}

export interface RfiReviewEstimateDto {
  modelCalls: number;
  inputTokens: number;
  outputTokens: number;
  imageParts: number;
  /** A RANGE: token counts before a run are approximations. All three are
   * null when the model has no known price — unknown stays unknown. */
  costUsd: number | null;
  costLowUsd: number | null;
  costHighUsd: number | null;
  model: string;
  provider: RfiReviewProvider;
  pricingVersion: string;
  /** Model calls and cost already SPENT by planning (query embeddings,
   * reranking) — null when the pricing is unknown, never 0 by default. */
  planningCostUsd: number | null;
  planningTokens: number;
  assumptions: string[];
}

export interface RfiReviewLimitsDto {
  maxInputTokens: number;
  maxThinkingTokens: number | null;
  thinkingEffort: RfiReviewThinking;
  maxTotalTokens: number;
  maxBatches: number;
}

export interface RfiReviewCoverageDto {
  /** Pages that matched but were left out by a cap, with the reason. */
  omittedPages: { sheetNumber: string | null; reason: string }[];
  /** Sheet numbers the target points at that are not in the project. */
  unresolvedReferences: string[];
  /** What the worker searched for and found while verifying. */
  searchLog: { query: string; found: number; stage: string }[];
  /** Evidence the run could not look at (batches beyond the cap, a failed batch). */
  omissions: string[];
}

export interface RfiReviewModelOptionDto {
  provider: RfiReviewProvider;
  model: string;
  available: boolean;
  capability: { effort: boolean; budget: boolean; note: string };
  priced: boolean;
}

export interface RfiReviewRunDto {
  id: string;
  status: RfiReviewStatus;
  stage: RfiReviewStage | null;
  progress: number;
  target: RfiReviewTarget;
  checkMode: RfiReviewCheckMode;
  checkIds: RfiReviewCheckId[];
  /** Why each check was chosen (auto) — shown on the plan screen. */
  checkReasons: Record<string, string>;
  /** All 16, selected or not, with applicability and reason. */
  checkPlan: Record<string, RfiCheckPlanDto>;
  /** All 16, once the run finished — `not_selected` for the others. */
  checkResults: Record<string, RfiCheckResultDto> | null;
  inventory: RfiInventoryItemDto[];
  coverage: RfiReviewCoverageDto;
  catalogueVersion: string;
  depth: RfiReviewDepth;
  provider: RfiReviewProvider;
  model: string;
  limits: RfiReviewLimitsDto;
  thinkingRequested: RfiReviewThinking;
  thinkingSent: string[];
  pages: RfiReviewPageDto[];
  /** Target values matched more than once — all are included and the plan
   * screen asks the user to remove the wrong ones. */
  ambiguous: string[];
  chunkCount: number;
  scopeHash: string;
  estimate: RfiReviewEstimateDto | null;
  /** Per-stage usage once it ran (same shape as a scan's, per stage). */
  usage: { stages: Record<string, RfiScanUsageDto>; total: RfiScanUsageDto | null } | null;
  candidates: number;
  notes: string[];
  error: string | null;
  createdAt: string;
  startedAt: string | null;
  completedAt: string | null;
}

/** Two runs' cost side by side, and whether they are comparable at all. */
export interface RfiReviewComparisonDto {
  base: { id: string; model: string; costUsd: number | null; tokens: number | null };
  other: { id: string; model: string; costUsd: number | null; tokens: number | null };
  differenceUsd: number | null;
  differencePercent: number | null;
  sameTarget: boolean;
  sameChecks: boolean;
  sameRevisions: boolean;
  sameCoverage: boolean;
  caveats: string[];
}
