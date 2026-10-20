import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import ExcelJS from "exceljs";
import { describe, expect, it } from "vitest";
import {
  RFI_CHECK_LABELS,
  RFI_REVIEW_CHECKS,
  compareAcrossNeeds,
  libraryCatalogueIds,
  suggestSheets,
  type LibrarySheet,
  type RfiLibraryQuestionDto,
} from "@cdip/shared";
import { buildLibrary, library, questionIdsOf, resolveLibraryChoice, type CustomQuestionRow } from "./rfiLibrary.js";
import { readLibrary } from "./rfiLibraryImport.js";

const { file, version } = library();

describe("the shipped library (the client's workbook)", () => {
  it("keeps the client's red rows off", () => {
    expect(file.elements.filter((e) => e.active)).toHaveLength(62);
    expect(file.questions.filter((q) => q.active)).toHaveLength(346);
    const off = new Set(file.elements.filter((e) => !e.active).map((e) => e.id));
    expect(file.questions.filter((q) => off.has(q.elementId) && q.active)).toEqual([]);
  });

  it("has unique ids and no question outside an element", () => {
    const ids = [...file.elements, ...file.questions, ...file.universal].map((x) => x.id);
    expect(new Set(ids).size).toBe(ids.length);
    const elements = new Set(file.elements.map((e) => e.id));
    expect(file.questions.filter((q) => !elements.has(q.elementId))).toEqual([]);
  });

  it("links only real catalogue questions and code checks, and every original question is reachable", () => {
    const catalogue = new Set<string>(RFI_REVIEW_CHECKS.map((c) => c.id));
    const linked = [...file.elements, ...file.questions].filter((x) => x.active).flatMap((x) => x.catalogue);
    expect(linked.filter((id) => !catalogue.has(id))).toEqual([]);
    expect([...catalogue].filter((id) => !linked.includes(id as never))).toEqual([]);
    const checks = [...file.questions, ...file.universal].flatMap((x) => x.codeChecks);
    expect(checks.filter((c) => !(c in RFI_CHECK_LABELS))).toEqual([]);
  });
});

const find = (qs: RfiLibraryQuestionDto[], id: string): RfiLibraryQuestionDto | undefined => {
  for (const q of qs) {
    if (q.id === id) return q;
    const hit = find(q.children, id);
    if (hit) return hit;
  }
  return undefined;
};

describe("buildLibrary", () => {
  const customs: CustomQuestionRow[] = [
    { id: "c1", elementId: "CE-01-01", parentId: "CE-01-01-Q02", category: null, text: "Grid D to E only?" },
    { id: "c2", elementId: "CE-01-01", parentId: "c1", category: null, text: "On level 3?" },
    { id: "c3", elementId: "CE-01-01", parentId: null, category: "Location", text: "Own top-level question" },
    { id: "c4", elementId: "CE-01-01", parentId: "gone", category: null, text: "Parent deleted" },
    { id: "c5", elementId: "CE-01-04", parentId: null, category: null, text: "On a red element" },
  ];
  const dto = buildLibrary(file, version, customs);
  const grids = dto.packages[0]!.elements[0]!;

  it("shows active packages and elements only, in review order", () => {
    expect(dto.packages.map((p) => p.id)).toEqual(["01", "03", "04", "05", "06", "07", "08", "12"]);
    expect(dto.packages[0]!.elements.map((e) => e.name)).toEqual(["Grid lines", "Levels / elevations", "Project datum & survey control"]);
    expect(dto.packages.flatMap((p) => p.elements).some((e) => e.id === "CE-01-04")).toBe(false);
  });

  it("nests follow-ups to any depth and keeps an orphan rather than losing it", () => {
    expect(find(grids.questions, "CE-01-01-Q02")!.children.map((q) => q.id)).toEqual(["c1"]);
    expect(find(grids.questions, "c1")!.children.map((q) => q.id)).toEqual(["c2"]);
    const top = grids.questions.map((q) => q.id);
    expect(top).toContain("c3");
    expect(top).toContain("c4");
    expect(JSON.stringify(dto)).not.toContain("On a red element");
  });

  it("expands universal questions per element and carries the links", () => {
    const u = find(grids.questions, "CE-01-01-U-02")!;
    expect(u.source).toBe("universal");
    expect(u.text).toContain("Grid lines");
    expect(u.text).not.toContain("{element}");
    expect(find(grids.questions, "CE-01-01-Q01")!.codeChecks).toEqual(["grid_mismatch"]);
    expect(grids.questions.filter((q) => q.source === "library")).toHaveLength(10);
  });

  it("allows follow-ups only under a question of the same active element", () => {
    const ids = questionIdsOf(file, "CE-01-01", customs)!;
    expect(ids.has("CE-01-01-Q02") && ids.has("CE-01-01-U-01") && ids.has("c1")).toBe(true);
    expect(ids.has("CE-01-02-Q01")).toBe(false);
    expect(questionIdsOf(file, "CE-01-04", customs)).toBeNull();
    expect(questionIdsOf(file, "nope", customs)).toBeNull();
  });
});

describe("readLibrary (the importer)", () => {
  async function workbook(): Promise<string> {
    const book = new ExcelJS.Workbook();
    const el = book.addWorksheet("Element Library");
    el.addRow(["ID", "Review Seq", "Package", "Element", "Variants", "Compare Across", "Attributes", "Triggers"]);
    el.addRow(["CE-01-01", 1, "01 Setup", "Grid lines", "Primary", "Arch plans, Struct plans", "Labels", "Mismatch"]);
    const red = el.addRow(["CE-02-01", 2, "02 Subgrade", "Mud mat", "", "", "", ""]);
    red.getCell(1).fill = { type: "pattern", pattern: "solid", fgColor: { argb: "FFFF0000" } };
    const qb = book.addWorksheet("Question Bank");
    qb.addRow(["Q-ID", "Element ID", "Package", "Element", "Category", "Question", "Where"]);
    qb.addRow(["CE-01-01-Q01", "CE-01-01", "01 Setup", "Grid lines", "Coordination", "Do labels match?", "Arch plans"]);
    qb.addRow(["CE-02-01-Q01", "CE-02-01", "02 Subgrade", "Mud mat", "Geometry", "Thickness?", ""]);
    const u = book.addWorksheet("Universal Questions");
    u.addRow(["U-ID", "Category", "Template"]);
    u.addRow(["U-01", "Missing", "Is every {element} tagged?"]);
    u.addRow(["Category legend: Location"]);
    const path = join(mkdtempSync(join(tmpdir(), "lib-")), "lib.xlsx");
    await book.xlsx.writeFile(path);
    return path;
  }

  it("imports red rows as off, their questions with them, and keeps the links", async () => {
    const lib = await readLibrary(await workbook(), { "CE-01-01-Q01": { codeChecks: ["grid_mismatch"] } });
    expect(lib.packages).toEqual([{ id: "01", name: "Setup", active: true }, { id: "02", name: "Subgrade", active: false }]);
    expect(lib.elements.map((e) => [e.id, e.active])).toEqual([["CE-01-01", true], ["CE-02-01", false]]);
    expect(lib.elements[0]!.compareAcross).toEqual(["Arch plans", "Struct plans"]);
    expect(lib.questions.map((q) => [q.id, q.active])).toEqual([["CE-01-01-Q01", true], ["CE-02-01-Q01", false]]);
    expect(lib.questions[0]!.codeChecks).toEqual(["grid_mismatch"]);
    expect(lib.universal.map((x) => x.id)).toEqual(["U-01"]);
  });

  it("refuses a link to an id the new workbook dropped", async () => {
    await expect(readLibrary(await workbook(), { "CE-09-09": { catalogue: ["G01"] } })).rejects.toThrow(/CE-09-09/);
  });
});

describe("sheet suggestions for an element", () => {
  const sheet = (sheetNumber: string, discipline: LibrarySheet["discipline"], sheetKind: LibrarySheet["sheetKind"], n = 1): LibrarySheet => ({
    combinedPageNumber: n, sheetNumber, discipline, sheetKind,
  });
  const set = [
    sheet("A0.01", "architectural", "cover", 1),
    sheet("A1.01", "architectural", "plan", 2),
    sheet("S0.01", "structural", "notes", 3),
    sheet("S2.01", "structural", "plan", 4),
    sheet("S5.01", "structural", "schedule", 5),
    sheet("M1.01", "mechanical", null, 6),
  ];

  it("reads the client's wording: a kind alone belongs to the discipline before it, anything else is outside", () => {
    const { needs, outside } = compareAcrossNeeds(["Struct plan", "pile cap schedule", "details", "Geotech", "Specs Div 03"]);
    expect(needs.map((n) => [n.entry, n.disciplines, n.kinds])).toEqual([
      ["Struct plan", ["structural"], ["plan", "enlarged_plan"]],
      ["pile cap schedule", ["structural"], ["schedule"]],
      ["details", ["structural"], ["detail"]],
    ]);
    expect(outside).toEqual(["Geotech", "Specs Div 03"]);
  });

  it("suggests one sheet per entry, the matching kind first, a page never read as a fallback", () => {
    expect(suggestSheets(["Arch plans", "Struct plans", "Civil site plan", "MEP plans"], set)).toEqual({
      sheets: ["A1.01", "S2.01", "M1.01"],
      missing: ["Civil site plan"],
      outside: [],
    });
    expect(suggestSheets(["Struct plan", "pile cap schedule", "Geotech"], set)).toEqual({
      sheets: ["S2.01", "S5.01"],
      missing: [],
      outside: ["Geotech"],
    });
  });

  it("prefers a floor plan over a cover or notes sheet, a plan over an enlarged one, and one floor throughout", () => {
    const floors: LibrarySheet[] = [
      { ...sheet("A0.10", "architectural", "plan", 1), level: null },
      { ...sheet("A2.83.01", "architectural", "enlarged_plan", 2), level: "LEVEL 1" },
      { ...sheet("A1.02", "architectural", "plan", 3), level: "LEVEL 2" },
      { ...sheet("S0.00", "structural", "plan", 4), level: null },
      { ...sheet("S2.21", "structural", "plan", 5), level: "LEVEL 1" },
      { ...sheet("S2.23", "structural", "plan", 6), level: "LEVEL 2" },
    ];
    expect(suggestSheets(["Arch plans", "Struct plans"], floors).sheets).toEqual(["A1.02", "S2.23"]);
  });

  it("reads a bare discipline as its plan, falling back to other kinds", () => {
    expect(suggestSheets(["Struct", "Arch"], set).sheets).toEqual(["S2.01", "A1.01"]);
    expect(suggestSheets(["Struct"], [sheet("S0.01", "structural", "notes", 1)]).sheets).toEqual(["S0.01"]);
  });

  it("never suggests a sheet twice, and one per entry unless capped", () => {
    const r = suggestSheets(["Struct", "Struct plan", "Arch", "Arch plans", "MEP", "Struct"], set);
    expect(new Set(r.sheets).size).toBe(r.sheets.length);
    expect(r.sheets.length).toBeLessThanOrEqual(6);
    expect(suggestSheets(["Struct", "Arch", "MEP"], set, 2).sheets).toHaveLength(2);
  });

  it("asks a chosen question through its own link, or its element's when it has none", () => {
    expect(libraryCatalogueIds({ catalogue: ["C01"] }, [{ catalogue: ["C03"] }])).toEqual(["C03"]);
    expect(libraryCatalogueIds({ catalogue: ["C01"] }, [{ catalogue: ["C03"] }, { catalogue: [] }]).sort()).toEqual(["C01", "C03"]);
    expect(libraryCatalogueIds({ catalogue: [] }, [{ catalogue: [] }])).toEqual([]);
  });

  it("every active element of the shipped library maps to at least one drawing discipline", () => {
    const none = file.elements.filter((e) => e.active && compareAcrossNeeds(e.compareAcross).needs.length === 0);
    expect(none.map((e) => e.id)).toEqual([]);
  });
});

describe("resolveLibraryChoice (what a By element review is asked)", () => {
  const customs: CustomQuestionRow[] = [
    { id: "c1", elementId: "CE-03-01", parentId: "CE-03-01-Q01", category: null, text: "Which mark is the pier at A1/BK?" },
  ];

  it("resolves the element's own words, a follow-up with its parent's, and the linked code checks", () => {
    const s = resolveLibraryChoice(file, customs, "CE-03-01", ["CE-03-01-Q01", "c1", "CE-03-01-U-01"]);
    expect(s.element.name).toBe("Drilled piers / caissons / drilled shafts");
    expect(s.questions.map((q) => q.id)).toEqual(["CE-03-01-Q01", "c1", "CE-03-01-U-01"]);
    expect(s.questions[1]!.text).toBe(`${s.questions[0]!.text} → Which mark is the pier at A1/BK?`);
    expect(s.questions[1]!.source).toBe("custom");
    expect(s.questions[2]!.codeChecks).toEqual(["unscheduled_mark", "unmarked_element"]);
  });

  it("refuses another element's question, an element that is off, and an unknown id", () => {
    expect(() => resolveLibraryChoice(file, customs, "CE-03-01", ["CE-01-01-Q01"])).toThrow(/not questions of/);
    expect(() => resolveLibraryChoice(file, customs, "CE-01-04", ["CE-01-04-Q01"])).toThrow(/not in the library/);
    expect(() => resolveLibraryChoice(file, customs, "CE-03-01", ["nope"])).toThrow(/nope/);
  });
});
