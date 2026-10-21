import { describe, expect, it } from "vitest";
import { RFI_SETTINGS_DEFAULTS } from "@cdip/shared";
import { buildLibrary, library, questionIdsOf, resolveLibraryChoice } from "./rfiLibrary.js";
import { setCost, setPlanRequest } from "./rfiSets.js";

const { file, version } = library();
const mine = [{ id: "11111111-1111-4111-8111-111111111111", packageId: "03", name: "Helical piles" }];
const lib = buildLibrary(file, version, [], mine);
const review = RFI_SETTINGS_DEFAULTS.review;

describe("a project's own element", () => {
  it("sits in its package after the library's, with the universal questions", () => {
    const pkg = lib.packages.find((p) => p.id === "03")!;
    const el = pkg.elements.at(-1)!;
    expect([el.id, el.name, el.custom]).toEqual([mine[0]!.id, "Helical piles", true]);
    expect(el.questions.length).toBe(file.universal.length);
    expect(el.questions[0]!.text).toContain("Helical piles");
  });

  it("takes questions and resolves for a review like a library element", () => {
    const customs = [{ id: "c1", elementId: mine[0]!.id, parentId: null, category: null, text: "Helix diameter?" }];
    expect(questionIdsOf(file, mine[0]!.id, customs, mine)?.has("c1")).toBe(true);
    expect(questionIdsOf(file, mine[0]!.id, customs)).toBeNull();
    const scope = resolveLibraryChoice(file, customs, mine[0]!.id, ["c1"], mine);
    expect(scope.element.name).toBe("Helical piles");
    expect(scope.questions.map((q) => q.text)).toEqual(["Helix diameter?"]);
  });

  it("is dropped when its package is not the library's", () => {
    const stray = buildLibrary(file, version, [], [{ id: "x", packageId: "99", name: "Stray" }]);
    expect(stray.packages.flatMap((p) => p.elements).some((e) => e.id === "x")).toBe(false);
  });
});

describe("setPlanRequest (one element of a named RFI)", () => {
  const grids = lib.packages[0]!.elements[0]!;

  it("asks the chosen questions over one sheet, or the sheets compared", () => {
    const one = setPlanRequest(["S2.30-B"], { elementId: grids.id, questionIds: ["CE-01-01-Q01"] }, lib, review, false);
    expect(one.target).toEqual({ type: "sheet", value: "S2.30-B" });
    expect(one.checkIds).toEqual(["G01"]);
    expect(one.library).toEqual({ elementId: grids.id, questionIds: ["CE-01-01-Q01"] });
    const two = setPlanRequest(["S2.30-B", "A2.30"], { elementId: grids.id, questionIds: ["CE-01-01-Q01"] }, lib, review, false);
    expect(two.target).toEqual({ type: "compare", values: ["S2.30-B", "A2.30"] });
  });

  it("sends a thinking ceiling only where the model honours one", () => {
    const capped = { ...review, maxThinkingTokens: 4000 };
    const item = { elementId: grids.id, questionIds: ["CE-01-01-Q01"] };
    expect(setPlanRequest(["S1"], item, lib, capped, false)).not.toHaveProperty("maxThinkingTokens");
    expect(setPlanRequest(["S1"], item, lib, capped, true).maxThinkingTokens).toBe(4000);
  });

  it("refuses an element or question that is not there", () => {
    expect(() => setPlanRequest(["S1"], { elementId: "nope", questionIds: ["q"] }, lib, review, false)).toThrow(/not in the library/);
    expect(() => setPlanRequest(["S1"], { elementId: grids.id, questionIds: ["CE-02-01-Q01"] }, lib, review, false)).toThrow(/no question/);
  });
});

describe("setCost", () => {
  it("sums the ranges, and is unknown as soon as one review is", () => {
    expect(setCost([{ costLowUsd: 0.1, costHighUsd: 0.3 }, { costLowUsd: 0.2, costHighUsd: 0.5 }])).toEqual({ costLowUsd: 0.1 + 0.2, costHighUsd: 0.8 });
    expect(setCost([{ costLowUsd: 0.1, costHighUsd: 0.3 }, null])).toEqual({ costLowUsd: null, costHighUsd: null });
    expect(setCost([])).toEqual({ costLowUsd: null, costHighUsd: null });
  });
});
