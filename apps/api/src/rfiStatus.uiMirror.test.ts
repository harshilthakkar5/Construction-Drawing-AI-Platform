import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { RFI_STATUSES, type RfiStatus } from "@cdip/shared";
import { allowedTransitions } from "./rfiStatus.js";

/**
 * `RfiPanel.tsx` keeps its own copy of the transition table so it can show
 * only the buttons that will work. That copy is a DUPLICATE of the rules in
 * `rfiStatus.ts`, and this repository's recurring failure is exactly that: a
 * vocabulary declared twice, drifting silently, discovered by a user.
 *
 * A drift here is not a crash. The API still refuses illegally — it is the
 * only authority and this test does not change that. What drifts is what the
 * person SEES: a button that 409s when pressed, or, worse, a legal action
 * whose button has quietly disappeared from the UI with nothing to say so.
 *
 * The answer transition is excluded on purpose: answering carries content, so
 * it has its own route and its own form rather than a status button.
 */
const PANEL = new URL("../../web/src/components/RfiPanel.tsx", import.meta.url);

/** Parse the `NEXT` table out of the panel source. */
function panelTransitions(): Record<string, string[]> {
  const source = readFileSync(PANEL, "utf8");
  const block = /const NEXT: Record<RfiStatus, RfiStatus\[\]> = \{(.*?)\n\};/s.exec(source);
  if (!block) throw new Error("NEXT table not found in RfiPanel.tsx");

  const table: Record<string, string[]> = {};
  for (const line of block[1]!.split("\n")) {
    const row = /^\s*(\w+):\s*\[(.*)\],?\s*$/.exec(line);
    if (!row) continue;
    table[row[1]!] = row[2]!
      .split(",")
      .map((entry) => entry.trim().replace(/^["']|["']$/g, ""))
      .filter((entry) => entry.length > 0);
  }
  return table;
}

describe("the RFI panel's transition table", () => {
  it("names every status the API knows", () => {
    expect(Object.keys(panelTransitions()).sort()).toEqual([...RFI_STATUSES].sort());
  });

  it("never offers a transition the API would refuse", () => {
    const panel = panelTransitions();
    for (const [from, targets] of Object.entries(panel)) {
      for (const to of targets) {
        expect(
          allowedTransitions(from as RfiStatus) as readonly string[],
          `RfiPanel offers ${from} → ${to}, which rfiStatus.ts refuses — the button 409s`,
        ).toContain(to);
      }
    }
  });

  it("hides no legal transition except answering", () => {
    const panel = panelTransitions();
    for (const from of RFI_STATUSES) {
      for (const to of allowedTransitions(from)) {
        // Answering is a form, not a status button — it posts an answer body.
        if (to === "answered") continue;
        expect(
          panel[from],
          `rfiStatus.ts allows ${from} → ${to} but RfiPanel offers no button for it`,
        ).toContain(to);
      }
    }
  });
});
