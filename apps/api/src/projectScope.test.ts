import { describe, expect, it } from "vitest";
import { outOfScopeTopics, questionOutOfScope, rfiSettingsOf, withinScope, type RfiLibraryDto } from "@cdip/shared";
import { buildLibrary, library } from "./rfiLibrary.js";
import { updateProjectSchema } from "./routes/projects.js";

/** Settings → Project scope. The client: "rebar detailing is not included in our scope of work". */
describe("project scope", () => {
  const real = () => buildLibrary(library().file, library().version, []);
  const questions = (lib: RfiLibraryDto) => lib.packages.flatMap((p) => p.elements.flatMap((e) => e.questions.map((q) => ({ e, q }))));

  it("holds rebar detailing out by default, and a saved choice wins", () => {
    expect(rfiSettingsOf(null).scope.outOfScope).toEqual(["rebar"]);
    expect(rfiSettingsOf({ review: { depth: "quick" } }).scope.outOfScope).toEqual(["rebar"]);
    expect(rfiSettingsOf({ scope: { outOfScope: [] } }).scope.outOfScope).toEqual([]);
  });

  it("is saved with the RFI settings, and an unknown topic is refused", () => {
    const base = rfiSettingsOf(null);
    expect(updateProjectSchema.parse({ rfiSettings: base }).rfiSettings!.scope).toEqual({ outOfScope: ["rebar"] });
    expect(updateProjectSchema.safeParse({ rfiSettings: { ...base, scope: { outOfScope: ["plumbing"] } } }).success).toBe(false);
    // Settings saved before the scope existed still validate.
    const { scope: _, ...before } = base;
    expect(updateProjectSchema.safeParse({ rfiSettings: before }).success).toBe(true);
  });

  it("takes the client's own Reinforcement questions and rebar elements out of the library, and nothing else", () => {
    const all = real();
    const { library: kept, removed } = withinScope(all, ["rebar"]);
    const before = questions(all);
    const after = questions(kept);
    expect(removed).toBe(before.length - after.length);
    expect(after.some(({ q }) => q.category === "Reinforcement")).toBe(false);
    expect(after.some(({ e }) => e.id === "CE-01-05" || e.id === "CE-12-03")).toBe(false);
    // Only those went: every other question is still offered, the beam's size and place among them.
    const gone = before.filter(({ e, q }) => !after.some((a) => a.e.id === e.id && a.q.id === q.id));
    expect(gone.every(({ e, q }) => q.category === "Reinforcement" || e.id === "CE-01-05" || e.id === "CE-12-03")).toBe(true);
    expect(removed).toBeGreaterThan(60);
    expect(after.some(({ e }) => e.name === "Beams & girders")).toBe(true);
    // Nothing out of scope: the library as it was.
    expect(withinScope(all, []).library).toBe(all);
  });

  it("reads a project's own question by its words, and never calls reinforced concrete rebar", () => {
    const own = (text: string) => ({ category: null, source: "custom", text });
    expect(questionOutOfScope(own("Are the beam stirrups and bottom bars given?"), "CE-08-03", ["rebar"])).toBe(true);
    expect(questionOutOfScope(own("Is the reinforced concrete wall thickness given?"), "CE-07-06", ["rebar"])).toBe(false);
    expect(questionOutOfScope(own("Are the beam stirrups given?"), "CE-08-03", [])).toBe(false);
    expect(outOfScopeTopics("Beam schedule has no reinforcement for B1", ["rebar"]).map((t) => t.id)).toEqual(["rebar"]);
    expect(outOfScopeTopics("Reinforced concrete wall RW-1 thickness", ["rebar"])).toEqual([]);
  });
});
