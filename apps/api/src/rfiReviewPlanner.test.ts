import { describe, expect, it } from "vitest";
import { resolveSheet } from "./rfiReviewPlanner.js";

type Row = Parameters<typeof resolveSheet>[1][number];
const page = (id: string, sheetNumber: string): Row => ({ id, sheetNumber, sheetRegionText: null }) as Row;

describe("resolveSheet", () => {
  it("takes the sheet as printed before a look-alike that only normalises the same", () => {
    // Leland: a wall type "BW-S-001" was read as sheet S-001 and pulled into every S0.01 review.
    const pages = [page("a", "S0.01"), page("b", "S-001")];
    expect(resolveSheet("S0.01", pages).map((p) => p.id)).toEqual(["a"]);
    expect(resolveSheet("s001", pages).map((p) => p.id)).toEqual(["a", "b"]);
  });
});

describe("libraryQueries", () => {
  it("searches once per question category, in the element's own words, with no sheet numbers", async () => {
    const { libraryQueries } = await import("./rfiReviewRules.js");
    const q = (id: string, category: string | null, text: string) => ({ id, text, category, source: "library", codeChecks: [] });
    const out = libraryQueries({
      element: { id: "CE-05-01", name: "Elevator pits", variants: "Single, double", attributes: "", triggers: "" },
      questions: [q("a", "Elevation", "Is the top of pit slab elevation specified?"), q("b", "Elevation", "Is the pit depth given?"), q("c", null, "Is the sump shown?")],
    });
    expect(out.map((s) => s.label)).toEqual(["Elevator pits: Elevation", "Elevator pits: your questions"]);
    expect(out[0]!.query).toBe("Elevator pits Single, double Is the top of pit slab elevation specified? Is the pit depth given?");
    expect(libraryQueries(undefined)).toEqual([]);
  });

  it("searches with the element's attributes too: what its questions are about", async () => {
    const { libraryQueries } = await import("./rfiReviewRules.js");
    const [only] = libraryQueries({
      element: { id: "CE-04-02", name: "Pile caps", variants: "", attributes: "Mark, size, depth, top elevation", triggers: "" },
      questions: [{ id: "a", text: "Is every cap tagged?", category: "Marks", source: "library", codeChecks: [] }],
    });
    expect(only!.query).toBe("Pile caps Mark, size, depth, top elevation Is every cap tagged?");
  });
});
