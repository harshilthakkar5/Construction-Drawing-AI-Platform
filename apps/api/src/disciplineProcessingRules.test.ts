import { describe, expect, it } from "vitest";
import { chosenAfter } from "./disciplineProcessingRules.js";

describe("chosenAfter", () => {
  it("adds what was asked and keeps what was chosen before", () => {
    expect(chosenAfter(["structural"], ["mechanical"], [], false)).toEqual(["mechanical", "structural"]);
  });
  it("never removes a discipline", () => {
    expect(chosenAfter(["general", "structural"], [], ["electrical"], false)).toEqual(["general", "structural"]);
  });
  it("all = every discipline with pages, plus unsorted pages", () => {
    expect(chosenAfter(["structural"], [], ["electrical", "structural"], true)).toEqual([
      "electrical",
      "structural",
      "unsorted",
    ]);
  });
});
