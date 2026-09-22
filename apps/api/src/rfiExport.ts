import ExcelJS from "exceljs";
import type { RfiDto, RfiLocationDto } from "@cdip/shared";
import { ballInCourt, isOverdue } from "./rfiStatus.js";

/**
 * The Excel RFI log.
 *
 * Two sheets, and the second is the point. Sheet 1 is the log itself — the
 * thing a project manager emails to a design team. Sheet 2 is the EVIDENCE:
 * for every row, which document, which page, which bounding box, and a link
 * that reopens the viewer there.
 *
 * That second sheet is what keeps FR-13's chain intact once a claim leaves
 * this application. A spreadsheet of assertions with no way back to the
 * drawing is exactly the failure the whole citation architecture exists to
 * prevent — the reader cannot check it, so they either believe it or discard
 * it, and both are wrong. Every RFI in the export can be walked back to a
 * page and a rectangle on that page.
 *
 * Every decision lives here and not in the route, down to the workbook
 * assembly, so the bytes a person downloads can be built in a test and read
 * back. Nothing here does IO: the route streams what this returns.
 */

/** Sheet 1. Column order is the order a construction RFI log is normally read. */
export const LOG_COLUMNS = [
  "RFI No.",
  "Date Raised",
  "Status",
  "Priority",
  "Discipline",
  "Subject",
  "Question",
  "Sheet(s)",
  "Page(s)",
  "Raised By",
  "Assigned To",
  "Ball In Court",
  "Due Date",
  "Overdue",
  "Answer",
  "Answered By",
  "Answered Date",
  "Closed Date",
] as const;

/** Sheet 2. One row per pinned location, joined back to its RFI by number. */
export const EVIDENCE_COLUMNS = [
  "RFI No.",
  "Subject",
  "Source",
  "Document",
  "Sheet",
  "Page (in document)",
  "Page (combined)",
  "BBox x",
  "BBox y",
  "BBox w",
  "BBox h",
  "Drawing Revised",
  "Open In Viewer",
] as const;

export type LogRow = Record<(typeof LOG_COLUMNS)[number], string | number | null>;
export type EvidenceRow = Record<(typeof EVIDENCE_COLUMNS)[number], string | number | null>;

/** Resolve a user id to a name for the export, falling back to the id. */
export type NameLookup = (userId: string | null) => string | null;

/**
 * "S-101, S-201" rather than a count. A log is read by a person looking for
 * the sheet they are holding, and `2 locations` is not searchable.
 *
 * A location with no sheet number is listed by its page instead of being
 * silently dropped: a pin that predates classification, or one on a page whose
 * title block did not scrape, is still a real pin and the reader needs to know
 * it is there.
 */
export function sheetList(locations: RfiLocationDto[]): string {
  if (locations.length === 0) return "";
  const seen = new Set<string>();
  for (const loc of locations) {
    seen.add(loc.sheetNumber ?? `(page ${loc.combinedPageNumber ?? loc.pageNumber})`);
  }
  return [...seen].join(", ");
}

/** Combined page numbers, deduped, in ascending order. */
export function pageList(locations: RfiLocationDto[]): string {
  const pages = locations
    .map((loc) => loc.combinedPageNumber ?? loc.pageNumber)
    .filter((n): n is number => typeof n === "number");
  return [...new Set(pages)].sort((a, b) => a - b).join(", ");
}

/**
 * Dates go into the sheet as ISO date strings, not Date objects and not
 * locale-formatted text.
 *
 * Excel renders a Date in the READER's locale, so a log exported here and
 * opened in another country shows 03/04 as a different day than it was
 * written. An RFI due date is a contractual deadline; it cannot mean two
 * things depending on who double-clicked the file.
 */
export function isoDate(value: string | null): string {
  if (value === null) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toISOString().slice(0, 10);
}

/**
 * A link back into the app at the right project, RFI and page.
 *
 * `baseUrl` is passed in rather than read from env here so the pure half stays
 * pure, and so an export can be generated for whatever host the person is
 * actually using — a link to `localhost` in a file that gets emailed is worse
 * than no link, because it looks like it should work.
 */
export function viewerLink(
  baseUrl: string,
  projectId: string,
  rfiNumber: number,
  combinedPage: number | null,
): string {
  if (baseUrl === "") return "";
  const trimmed = baseUrl.replace(/\/+$/, "");
  const page = combinedPage === null ? "" : `&page=${combinedPage}`;
  return `${trimmed}/projects/${projectId}?rfi=${rfiNumber}${page}`;
}

export function buildLogRow(rfi: RfiDto, nameOf: NameLookup, now: Date): LogRow {
  const court = ballInCourt(rfi);
  return {
    "RFI No.": rfi.number,
    "Date Raised": isoDate(rfi.createdAt),
    Status: rfi.status,
    Priority: rfi.priority,
    Discipline: rfi.discipline ?? "",
    Subject: rfi.subject,
    Question: rfi.question,
    "Sheet(s)": sheetList(rfi.locations),
    "Page(s)": pageList(rfi.locations),
    "Raised By": rfi.createdByName ?? nameOf(rfi.createdById) ?? "",
    "Assigned To": rfi.assignedToName ?? nameOf(rfi.assignedToId) ?? "",
    "Ball In Court": nameOf(court) ?? "",
    "Due Date": isoDate(rfi.dueAt),
    Overdue: isOverdue({ status: rfi.status, dueAt: rfi.dueAt ? new Date(rfi.dueAt) : null }, now)
      ? "YES"
      : "",
    Answer: rfi.answer ?? "",
    "Answered By": rfi.answeredByName ?? nameOf(rfi.answeredById) ?? "",
    "Answered Date": isoDate(rfi.answeredAt),
    "Closed Date": isoDate(rfi.closedAt),
  };
}

/**
 * Evidence rows for one RFI — one per pinned location.
 *
 * An RFI with NO location produces no evidence row, and that absence is the
 * honest output: there is nothing to verify. It is deliberately not filled
 * with a placeholder row, because a reader scanning this sheet is checking
 * which claims are backed, and a row that exists but points nowhere reads as
 * backing.
 *
 * `Source` is "manual" throughout Phase 1 — every RFI here was written by a
 * person. It exists now so that when generated RFIs arrive the column is
 * already in the format people have been reading, and a reader can tell at a
 * glance which questions a machine proposed.
 */
export function buildEvidenceRows(
  rfi: RfiDto,
  projectId: string,
  baseUrl: string,
): EvidenceRow[] {
  return rfi.locations.map((loc) => {
    const bbox = loc.bbox;
    return {
      "RFI No.": rfi.number,
      Subject: rfi.subject,
      Source: "manual",
      Document: loc.filename ?? "",
      Sheet: loc.sheetNumber ?? "",
      "Page (in document)": loc.pageNumber,
      "Page (combined)": loc.combinedPageNumber,
      "BBox x": bbox ? round2(bbox.x) : null,
      "BBox y": bbox ? round2(bbox.y) : null,
      "BBox w": bbox ? round2(bbox.width) : null,
      "BBox h": bbox ? round2(bbox.height) : null,
      // Flagged rather than hidden: the pin is still where it was, but the
      // sheet under it has a newer revision, so the reader must not treat the
      // rectangle as current without looking.
      "Drawing Revised": loc.drawingRevised ? "YES — re-pin needed" : "",
      "Open In Viewer": viewerLink(baseUrl, projectId, rfi.number, loc.combinedPageNumber),
    };
  });
}

function round2(value: number): number {
  return Math.round(value * 100) / 100;
}

/**
 * The log, in the order it is read: by RFI number, ascending. Numbers are
 * allocated in creation order and never reused, so this is also chronological,
 * and a gap in the sequence is a voided RFI rather than a missing row.
 */
export function buildWorkbookRows(
  rfis: RfiDto[],
  options: { projectId: string; baseUrl: string; nameOf: NameLookup; now: Date },
): { log: LogRow[]; evidence: EvidenceRow[] } {
  const ordered = [...rfis].sort((a, b) => a.number - b.number);
  return {
    log: ordered.map((rfi) => buildLogRow(rfi, options.nameOf, options.now)),
    evidence: ordered.flatMap((rfi) =>
      buildEvidenceRows(rfi, options.projectId, options.baseUrl),
    ),
  };
}

/**
 * Assemble the workbook. Lives here rather than in the route so the bytes
 * people actually download can be built in a test and read back — the row
 * shapes above were unit-tested while the file assembly was not, which is the
 * arrangement that produces a green suite over a broken download.
 */
export function buildWorkbook(log: LogRow[], evidence: EvidenceRow[]): ExcelJS.Workbook {
  const workbook = new ExcelJS.Workbook();
  workbook.created = new Date();
  writeSheet(workbook.addWorksheet("RFI Log", FROZEN_HEADER), LOG_COLUMNS, log);
  writeSheet(workbook.addWorksheet("Evidence", FROZEN_HEADER), EVIDENCE_COLUMNS, evidence);
  return workbook;
}

/** The header row stays visible while scrolling a long log. */
const FROZEN_HEADER = { views: [{ state: "frozen" as const, ySplit: 1 }] };

function writeSheet(
  sheet: ExcelJS.Worksheet,
  columns: readonly string[],
  rows: (LogRow | EvidenceRow)[],
): void {
  sheet.columns = columns.map((header) => ({
    header,
    key: header,
    width: PROSE_COLUMNS.includes(header) ? 60 : Math.max(12, header.length + 4),
  }));
  sheet.getRow(1).font = { bold: true };
  sheet.autoFilter = { from: { row: 1, column: 1 }, to: { row: 1, column: columns.length } };
  for (const row of rows) sheet.addRow(row);
  for (const name of PROSE_COLUMNS) {
    const index = columns.indexOf(name);
    if (index >= 0) sheet.getColumn(index + 1).alignment = { wrapText: true, vertical: "top" };
  }
}

/** Columns holding sentences rather than values: wrapped, and given room. */
const PROSE_COLUMNS: readonly string[] = ["Question", "Answer"];
