import { describe, expect, it } from "vitest";
import type { RfiDto, RfiLocationDto } from "@cdip/shared";
import ExcelJS from "exceljs";
import {
  buildEvidenceRows,
  buildWorkbook,
  buildLogRow,
  buildWorkbookRows,
  EVIDENCE_COLUMNS,
  isoDate,
  LOG_COLUMNS,
  pageList,
  sheetList,
  viewerLink,
} from "./rfiExport.js";

const NOW = new Date("2026-06-01T00:00:00Z");

function location(over: Partial<RfiLocationDto> = {}): RfiLocationDto {
  return {
    id: "loc-1",
    documentId: "doc-1",
    filename: "S-Structural.pdf",
    pageNumber: 3,
    combinedPageNumber: 17,
    bbox: { x: 100.456, y: 200.123, width: 50.9, height: 25.4 },
    sheetNumber: "S-201",
    drawingRevised: false,
    supersededById: null,
    createdAt: "2026-05-01T00:00:00.000Z",
    ...over,
  };
}

function rfi(over: Partial<RfiDto> = {}): RfiDto {
  return {
    id: "rfi-1",
    projectId: "proj-1",
    number: 1,
    subject: "Pile cap PC4 not in schedule",
    question: "PC4 appears at grid 7/D but is not in the Pile Cap Schedule. Please confirm.",
    status: "open",
    priority: "high",
    discipline: "structural",
    dueAt: null,
    createdById: "author",
    createdByName: "A Contractor",
    assignedToId: "engineer",
    assignedToName: "An Engineer",
    answer: null,
    answeredById: null,
    answeredByName: null,
    answeredAt: null,
    closedAt: null,
    createdAt: "2026-05-01T09:30:00.000Z",
    updatedAt: "2026-05-01T09:30:00.000Z",
    locations: [location()],
    ...over,
  };
}

const nameOf = (id: string | null) => (id === null ? null : { author: "A Contractor", engineer: "An Engineer" }[id] ?? id);

describe("isoDate", () => {
  /**
   * The whole reason dates are strings in this export. Excel renders a Date
   * cell in the reader's locale, so 03/04 is two different days depending on
   * who opened the file — and an RFI due date is a contractual deadline.
   */
  it("writes an unambiguous ISO day", () => {
    expect(isoDate("2026-03-04T23:00:00.000Z")).toBe("2026-03-04");
  });

  it("is empty for a missing date rather than printing a fallback", () => {
    expect(isoDate(null)).toBe("");
  });

  it("is empty for an unparseable date rather than 'Invalid Date'", () => {
    expect(isoDate("not a date")).toBe("");
  });
});

describe("sheetList", () => {
  it("names the sheets, because that is what a reader searches for", () => {
    expect(sheetList([location({ sheetNumber: "S-201" }), location({ sheetNumber: "S-101" })])).toBe(
      "S-201, S-101",
    );
  });

  it("dedupes two pins on one sheet", () => {
    expect(sheetList([location({ sheetNumber: "S-201" }), location({ sheetNumber: "S-201" })])).toBe(
      "S-201",
    );
  });

  /**
   * A pin on a page whose title block never scraped is still a real pin. It
   * falls back to the page rather than vanishing from the column, because a
   * silently shorter list reads as "this RFI points at fewer places".
   */
  it("falls back to a page reference when a sheet number is unknown", () => {
    expect(sheetList([location({ sheetNumber: null, combinedPageNumber: 42 })])).toBe("(page 42)");
  });

  it("is empty for an RFI with no locations", () => {
    expect(sheetList([])).toBe("");
  });
});

describe("pageList", () => {
  it("sorts ascending and dedupes", () => {
    expect(
      pageList([
        location({ combinedPageNumber: 17 }),
        location({ combinedPageNumber: 4 }),
        location({ combinedPageNumber: 17 }),
      ]),
    ).toBe("4, 17");
  });

  it("falls back to the in-document page when there is no combined number", () => {
    expect(pageList([location({ combinedPageNumber: null, pageNumber: 3 })])).toBe("3");
  });
});

describe("viewerLink", () => {
  it("points at the project, the RFI and the page", () => {
    expect(viewerLink("https://app.example.com", "proj-1", 7, 17)).toBe(
      "https://app.example.com/projects/proj-1?rfi=7&page=17",
    );
  });

  it("tolerates a trailing slash on the base url", () => {
    expect(viewerLink("https://app.example.com/", "proj-1", 7, 17)).toContain(
      "app.example.com/projects",
    );
  });

  it("omits the page when the location has no combined number", () => {
    expect(viewerLink("https://app.example.com", "proj-1", 7, null)).not.toContain("page=");
  });

  /**
   * No configured base URL means no link at all. A link to `localhost` inside
   * a file that gets emailed is worse than an empty cell: it looks like it
   * should work, so the reader blames themselves when it does not.
   */
  it("writes nothing rather than a link nobody else can open", () => {
    expect(viewerLink("", "proj-1", 7, 17)).toBe("");
  });
});

describe("buildLogRow", () => {
  it("fills every declared column", () => {
    const row = buildLogRow(rfi(), nameOf, NOW);
    for (const column of LOG_COLUMNS) {
      expect(row, `missing column ${column}`).toHaveProperty(column);
    }
    expect(Object.keys(row).sort()).toEqual([...LOG_COLUMNS].sort());
  });

  it("derives ball-in-court rather than storing it", () => {
    expect(buildLogRow(rfi({ status: "open" }), nameOf, NOW)["Ball In Court"]).toBe("An Engineer");
    expect(buildLogRow(rfi({ status: "answered" }), nameOf, NOW)["Ball In Court"]).toBe(
      "A Contractor",
    );
    expect(buildLogRow(rfi({ status: "closed" }), nameOf, NOW)["Ball In Court"]).toBe("");
  });

  it("flags an open RFI past its due date", () => {
    const late = rfi({ status: "open", dueAt: "2026-01-01T00:00:00.000Z" });
    expect(buildLogRow(late, nameOf, NOW).Overdue).toBe("YES");
  });

  it("does not flag an answered RFI, however late it was", () => {
    const late = rfi({ status: "answered", dueAt: "2026-01-01T00:00:00.000Z" });
    expect(buildLogRow(late, nameOf, NOW).Overdue).toBe("");
  });

  it("writes empty strings, not nulls, for absent people and dates", () => {
    const bare = rfi({
      createdById: null,
      createdByName: null,
      assignedToId: null,
      assignedToName: null,
      discipline: null,
    });
    const row = buildLogRow(bare, nameOf, NOW);
    expect(row["Raised By"]).toBe("");
    expect(row["Assigned To"]).toBe("");
    expect(row.Discipline).toBe("");
  });
});

describe("buildEvidenceRows", () => {
  it("fills every declared column", () => {
    const rows = buildEvidenceRows(rfi(), "proj-1", "https://app.example.com");
    expect(rows).toHaveLength(1);
    expect(Object.keys(rows[0]!).sort()).toEqual([...EVIDENCE_COLUMNS].sort());
  });

  it("carries the bbox so a reader can check the claim on the drawing", () => {
    const row = buildEvidenceRows(rfi(), "proj-1", "https://app.example.com")[0]!;
    expect(row["BBox x"]).toBe(100.46);
    expect(row["BBox y"]).toBe(200.12);
    expect(row["Page (combined)"]).toBe(17);
    expect(row.Sheet).toBe("S-201");
  });

  it("leaves bbox cells empty rather than zero when there is no box", () => {
    // Zero is a coordinate. An empty cell is the absence of one, and the two
    // must not look alike to someone checking the rectangle.
    const row = buildEvidenceRows(rfi({ locations: [location({ bbox: null })] }), "p", "")[0]!;
    expect(row["BBox x"]).toBeNull();
    expect(row["BBox w"]).toBeNull();
  });

  /**
   * An RFI nobody pinned has nothing to verify, and says so by producing no
   * row. A placeholder row would read, to someone scanning this sheet for
   * which claims are backed, as backing.
   */
  it("produces no evidence row for an RFI with no locations", () => {
    expect(buildEvidenceRows(rfi({ locations: [] }), "proj-1", "https://x.test")).toEqual([]);
  });

  it("warns on the row when the pinned sheet has been revised", () => {
    const revised = rfi({ locations: [location({ drawingRevised: true })] });
    expect(buildEvidenceRows(revised, "p", "")[0]!["Drawing Revised"]).toContain("re-pin");
  });

  it("marks Phase 1 rows as manually authored", () => {
    expect(buildEvidenceRows(rfi(), "p", "")[0]!.Source).toBe("manual");
  });

  it("emits one row per location", () => {
    const many = rfi({
      locations: [location({ id: "a" }), location({ id: "b", sheetNumber: "S-301" })],
    });
    expect(buildEvidenceRows(many, "p", "")).toHaveLength(2);
  });
});

describe("buildWorkbookRows", () => {
  const options = {
    projectId: "proj-1",
    baseUrl: "https://app.example.com",
    nameOf,
    now: NOW,
  };

  it("orders the log by RFI number", () => {
    const rows = buildWorkbookRows(
      [rfi({ id: "c", number: 3 }), rfi({ id: "a", number: 1 }), rfi({ id: "b", number: 2 })],
      options,
    );
    expect(rows.log.map((r) => r["RFI No."])).toEqual([1, 2, 3]);
  });

  it("does not mutate the array it was given", () => {
    const input = [rfi({ number: 3 }), rfi({ number: 1 })];
    buildWorkbookRows(input, options);
    expect(input.map((r) => r.number)).toEqual([3, 1]);
  });

  /**
   * A gap in the numbers is information, not corruption: RFI numbers are never
   * reused, so 1, 2, 4 means 3 was voided. The export must not renumber to
   * close the gap.
   */
  it("keeps gaps left by voided RFIs", () => {
    const rows = buildWorkbookRows([rfi({ number: 1 }), rfi({ number: 4 })], options);
    expect(rows.log.map((r) => r["RFI No."])).toEqual([1, 4]);
  });

  it("keeps the evidence sheet joinable to the log by number", () => {
    const rows = buildWorkbookRows([rfi({ number: 9 })], options);
    expect(rows.evidence[0]!["RFI No."]).toBe(9);
    expect(rows.log[0]!["RFI No."]).toBe(9);
  });

  it("handles an empty project", () => {
    expect(buildWorkbookRows([], options)).toEqual({ log: [], evidence: [] });
  });
});

/**
 * The workbook itself, written and read back.
 *
 * The row builders above were unit-tested while the file assembly was not,
 * which is the arrangement that produces a fully green suite over a download
 * that opens to an empty sheet — or does not open at all. These assertions go
 * through ExcelJS's own reader, so they fail if the bytes are not a workbook.
 */
describe("buildWorkbook", () => {
  const options = {
    projectId: "proj-1",
    baseUrl: "https://app.example.com",
    nameOf,
    now: NOW,
  };

  async function roundTrip(rfis: RfiDto[]): Promise<ExcelJS.Workbook> {
    const { log, evidence } = buildWorkbookRows(rfis, options);
    const buffer = await buildWorkbook(log, evidence).xlsx.writeBuffer();
    const read = new ExcelJS.Workbook();
    await read.xlsx.load(buffer as ArrayBuffer);
    return read;
  }

  it("writes both sheets, named so a reader knows which is which", async () => {
    const book = await roundTrip([rfi()]);
    expect(book.worksheets.map((s) => s.name)).toEqual(["RFI Log", "Evidence"]);
  });

  it("puts the declared headers in row 1", async () => {
    const book = await roundTrip([rfi()]);
    const header = book.getWorksheet("RFI Log")!.getRow(1).values as unknown[];
    // ExcelJS row values are 1-indexed, so slot 0 is empty padding.
    expect(header.slice(1)).toEqual([...LOG_COLUMNS]);
  });

  it("writes one log row per RFI, in number order", async () => {
    const book = await roundTrip([rfi({ id: "b", number: 2 }), rfi({ id: "a", number: 1 })]);
    const sheet = book.getWorksheet("RFI Log")!;
    expect(sheet.rowCount).toBe(3); // header + 2
    expect(sheet.getRow(2).getCell(1).value).toBe(1);
    expect(sheet.getRow(3).getCell(1).value).toBe(2);
  });

  it("carries the bbox through to the evidence sheet as numbers", async () => {
    const book = await roundTrip([rfi()]);
    const sheet = book.getWorksheet("Evidence")!;
    const headers = sheet.getRow(1).values as unknown[];
    const x = headers.indexOf("BBox x");
    expect(sheet.getRow(2).getCell(x).value).toBe(100.46);
  });

  /**
   * The reason dates are strings: a Date cell renders in the reader's locale,
   * and an RFI due date cannot mean two days depending on who opened the file.
   */
  it("writes dates as unambiguous ISO text, not Date cells", async () => {
    const book = await roundTrip([rfi()]);
    const sheet = book.getWorksheet("RFI Log")!;
    const headers = sheet.getRow(1).values as unknown[];
    const raised = sheet.getRow(2).getCell(headers.indexOf("Date Raised")).value;
    expect(raised).toBe("2026-05-01");
    expect(raised).not.toBeInstanceOf(Date);
  });

  it("produces a valid workbook for a project with no RFIs at all", async () => {
    const book = await roundTrip([]);
    expect(book.worksheets).toHaveLength(2);
    // Header only — an empty log is a legitimate export, not an error.
    expect(book.getWorksheet("RFI Log")!.rowCount).toBe(1);
  });

  it("leaves the evidence sheet empty when nothing is pinned", async () => {
    const book = await roundTrip([rfi({ locations: [] })]);
    expect(book.getWorksheet("RFI Log")!.rowCount).toBe(2);
    expect(book.getWorksheet("Evidence")!.rowCount).toBe(1);
  });
});
