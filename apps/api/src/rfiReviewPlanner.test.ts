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
