import { describe, expect, it } from "vitest";
import { buildSheetMap, levelLabel, type ManifestEntryDto } from "@cdip/shared";

let next = 0;
function sheet(sheetNumber: string, discipline: ManifestEntryDto["discipline"], extra: Partial<ManifestEntryDto> = {}): ManifestEntryDto {
  next += 1;
  return {
    documentId: "d",
    pageNumber: next,
    combinedPageNumber: next,
    filename: "set.pdf",
    hasImage: true,
    pageWidth: null,
    pageHeight: null,
    rotation: null,
    discipline,
    sheetNumber,
    sheetKind: "plan",
    sheetTitle: null,
    levels: [],
    sheetRefs: [],
    ...extra,
  };
}

describe("buildSheetMap", () => {
  const a2 = sheet("A1.02", "architectural", { levels: ["LEVEL 2"], sheetTitle: "SECOND FLOOR PLAN" });
  const s2 = sheet("S2.02", "structural", { levels: ["LEVEL 2"], sheetRefs: ["S5.01", "S-501", "A1.02", "S2.02"] });
  const s2e = sheet("S2.12", "structural", { levels: ["LEVEL 2"], sheetKind: "enlarged_plan", sheetRefs: ["S9.99"] });
  const m3 = sheet("M1.03", "mechanical", { levels: ["LEVEL 3"] });
  const roof = sheet("A1.09", "architectural", { levels: ["ROOF"] });
  const b2 = sheet("S2.00", "structural", { levels: ["LEVEL B2"] });
  const details = sheet("S5.01", "structural", { sheetKind: "detail" });
  const map = buildSheetMap([a2, s2, s2e, m3, roof, b2, details]);
  const at = (e: ManifestEntryDto) => map.sheets[e.combinedPageNumber]!;
  const numbers = (links: { sheetNumber: string | null }[]) => links.map((l) => l.sheetNumber);

  it("lists levels in building order with their sheets, and sheets with no level apart", () => {
    expect(map.levels.map((l) => l.level)).toEqual(["LEVEL B2", "LEVEL 2", "LEVEL 3", "ROOF"]);
    expect(numbers(map.levels[1]!.sheets)).toEqual(["A1.02", "S2.02", "S2.12"]);
    expect(numbers(map.noLevel)).toEqual(["S5.01"]);
  });

  it("links plans of other disciplines on the same level", () => {
    expect(numbers(at(a2).sameLevel)).toEqual(["S2.02", "S2.12"]);
    expect(numbers(at(s2).sameLevel)).toEqual(["A1.02"]);
    expect(at(m3).sameLevel).toEqual([]);
  });

  it("links an enlarged plan and the overall plan of its discipline and level", () => {
    expect(numbers(at(s2e).overall)).toEqual(["S2.02"]);
    expect(numbers(at(s2).enlarged)).toEqual(["S2.12"]);
    expect(at(a2).enlarged).toEqual([]);
  });

  it("resolves references whatever the separators, both ways, and names the missing ones", () => {
    // "S5.01" and "S-501" are one sheet (S501), so it is listed once; its own number is skipped.
    expect(numbers(at(s2).references)).toEqual(["S5.01", "A1.02"]);
    expect(at(s2).missingReferences).toEqual([]);
    expect(numbers(at(details).referencedBy)).toEqual(["S2.02"]);
    expect(numbers(at(a2).referencedBy)).toEqual(["S2.02"]);
    expect(at(s2e).missingReferences).toEqual(["S9.99"]);
  });

  it("writes a level for people", () => {
    expect(levelLabel("LEVEL B2")).toBe("Level B2");
    expect(levelLabel("GROUND FLOOR")).toBe("Ground Floor");
  });
});
