import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { dismissalFamily, sameDismissalItem } from "@cdip/shared";

/** The worker (dismissals.py) and the API decide what a remembered dismissal
 * covers from the same fixture, so the two cannot drift. */
const fixture = JSON.parse(
  readFileSync(new URL("../../../packages/shared/fixtures/dismissal-keys.json", import.meta.url), "utf8"),
) as { cases: [string, string, string | null][]; sameItem: [string, string, boolean][] };

describe("remembered dismissal keys", () => {
  it("derives a mark's family as the worker does", () => {
    for (const [checkType, key, family] of fixture.cases) expect(dismissalFamily(checkType, key), `${checkType} ${key}`).toBe(family);
  });

  it("treats one mark as one item however it is printed", () => {
    for (const [a, b, same] of fixture.sameItem) {
      expect(sameDismissalItem(a, b), `${a} / ${b}`).toBe(same);
      expect(sameDismissalItem(b, a), `${b} / ${a}`).toBe(same);
    }
    expect(sameDismissalItem(null, null)).toBe(false);
  });
});
