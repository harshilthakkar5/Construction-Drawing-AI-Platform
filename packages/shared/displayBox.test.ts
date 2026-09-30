import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { displayBox } from "./src/index.js";

/**
 * Stored boxes are UNROTATED; the viewer draws DISPLAYED pages. Every case in
 * the fixture is PyMuPDF's own rotation_matrix applied to one box, so this
 * checks the TypeScript against the library the boxes came from — not against
 * arithmetic worked out by hand. workers/tests/test_display_box.py regenerates
 * each case from PyMuPDF and fails if the fixture has drifted from it.
 */
const fixture = JSON.parse(readFileSync(new URL("./fixtures/display-box.json", import.meta.url), "utf8")) as {
  cases: {
    rotation: number;
    pageWidth: number;
    pageHeight: number;
    bbox: { x: number; y: number; width: number; height: number };
    display: { x: number; y: number; width: number; height: number };
  }[];
};

describe("displayBox", () => {
  it.each(fixture.cases)("rotation $rotation on a $pageWidth x $pageHeight page", (c) => {
    const got = displayBox(c.bbox, c.rotation, c.pageWidth, c.pageHeight);
    for (const k of ["x", "y", "width", "height"] as const) expect(got[k]).toBeCloseTo(c.display[k], 2);
  });

  it("covers all four rotations", () => {
    expect(new Set(fixture.cases.map((c) => c.rotation))).toEqual(new Set([0, 90, 180, 270]));
  });

  it("draws an unknown rotation as unrotated rather than guessing", () => {
    const box = { x: 1, y: 2, width: 3, height: 4 };
    expect(displayBox(box, null, 100, 100)).toEqual(box);
    expect(displayBox(box, -270, 200, 100)).toEqual(displayBox(box, 90, 200, 100));
  });
});
