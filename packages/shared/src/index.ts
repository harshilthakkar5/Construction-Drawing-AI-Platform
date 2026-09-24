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
  /** Sheet number off the title block ("S-004"); null when unscraped. Already
   * folded into `label`, and exposed separately so the UI can show it apart
   * from the page number. */
  sheetNumber?: string | null;
  /** Discipline slug of the page the chunk sits on. */
  discipline?: string | null;
  /** "description" when the cited chunk is a vision model's account of the
   * drawing's geometry rather than text lifted off the sheet. The UI marks
   * those, because clicking through to a highlight whose words are nowhere on
   * the page would otherwise read as a broken citation. */
  kind?: string | null;
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
}

export interface SummarizeProjectJob {
  projectId: string;
}

/** Scan a project's drawings for RFI-worthy gaps. The API creates the
 * rfi_scans row first and passes its id, so the worker reports into a row the
 * UI is already polling rather than inventing one. */
export interface RfiScanJob {
  projectId: string;
  scanId: string;
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
    ["projectId", "portionId", "requestedById"],
    ["requestedById"],
  ),
  summarizeProject: jobFields<SummarizeProjectJob>()(["projectId"]),
  rfiScan: jobFields<RfiScanJob>()(["projectId", "scanId"]),
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
}

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
  costUsd: number;
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
