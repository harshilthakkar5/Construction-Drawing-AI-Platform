import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { gridLabelKind } from "@cdip/shared";

/**
 * The cases live in packages/shared/fixtures/grid-label-kind.json and are read
 * by BOTH this suite and workers/tests/test_gridmarks.py. The chat calls a
 * grid reading's labels tags or marks here; the summary worker decides the
 * same thing in Python. A drift would have the chat call A4B a tag while the
 * summary of the same sheet calls it a column mark.
 */
const fixturePath = fileURLToPath(
  new URL("../../../packages/shared/fixtures/grid-label-kind.json", import.meta.url),
);
const fixture = JSON.parse(readFileSync(fixturePath, "utf8")) as {
  cases: { discipline: string | null; expect: "marks" | "tags" }[];
};

describe("gridLabelKind (shared fixture)", () => {
  it("covers both answers", () => {
    const answers = new Set(fixture.cases.map((c) => c.expect));
    expect(answers).toEqual(new Set(["marks", "tags"]));
  });

  for (const c of fixture.cases) {
    it(`${JSON.stringify(c.discipline)} → ${c.expect}`, () => {
      expect(gridLabelKind(c.discipline)).toBe(c.expect);
    });
  }
});
