/**
 * Which corpus the retrieval set asks.
 *
 * Both functions here exist because of one failure shape, and it is the worst
 * kind a benchmark has: a set pointing at a project nobody has touched does not
 * error. It returns nothing for every question and reports 0% recall — a number
 * that looks like a retrieval result and is not one. The checked-in set has been
 * in exactly that state, which is why the one benchmark that could say whether a
 * change to the CHUNKS hurt retrieval has been unrunnable through all of the
 * vision work that changed them.
 */

import test from "node:test";
import assert from "node:assert/strict";

import {
  appendCase,
  captureFollowUp,
  headline,
  applyProjectOverride,
  oneProjectOrThrow,
  unmarkedRefusal,
} from "./retrieval_eval.mjs";

const at = (projectId, question) => ({ projectId, question, expectedText: "IT-2" });

test("no --project leaves the set exactly as it is", () => {
  const cases = [at("a", "q1"), at("b", "q2")];
  assert.equal(applyProjectOverride(cases, undefined), cases, "same array, not a copy");
  assert.equal(applyProjectOverride(cases, ""), cases);
});

test("--project repoints every case and touches nothing else", () => {
  const moved = applyProjectOverride([at("old", "q1"), at("old", "q2")], "new");
  assert.deepEqual(
    moved.map((c) => c.projectId),
    ["new", "new"],
  );
  assert.equal(moved[0].question, "q1", "the question is what is being asked, not what moves");
  assert.equal(moved[0].expectedText, "IT-2", "the expectation travels with the question");
});

test("the override does not mutate the cases it was handed", () => {
  const cases = [at("old", "q1")];
  applyProjectOverride(cases, "new");
  assert.equal(cases[0].projectId, "old");
});

test("a set naming two projects is refused rather than averaged", () => {
  // It scores two corpora and prints one recall, and nothing in the output
  // says so. It arrives by repointing some cases and not the rest.
  assert.throws(() => oneProjectOrThrow([at("a", "q1"), at("b", "q2")]), /2 different projects/);
  assert.throws(() => oneProjectOrThrow([at("a", "q1"), at("b", "q2")]), /--project <uuid>/);
});

test("one project is returned so the caller can name the corpus it measured", () => {
  assert.equal(oneProjectOrThrow([at("a", "q1"), at("a", "q2")]), "a");
});

test("--project rescues a set that names two, which is the point of the order", () => {
  // The refusal exists for a set nobody has repointed. Applying the override
  // first is what lets the flag fix a stale set instead of being blocked by it.
  const cases = [at("a", "q1"), at("dead", "q2")];
  assert.throws(() => oneProjectOrThrow(cases));
  assert.equal(oneProjectOrThrow(applyProjectOverride(cases, "live")), "live");
});

test("importing this module does not start a benchmark run", () => {
  // The guard that makes every test above possible. Without it the import
  // fires main(), which needs a database, an embedding key and a live Qdrant.
  assert.equal(typeof applyProjectOverride, "function");
});

// --- Appending a captured case --------------------------------------------

test("a captured skeleton is added to a set that already fits the corpus", () => {
  const out = appendCase([at("p1", "q1")], at("p1", "q2"));
  assert.deepEqual(
    out.map((c) => c.question),
    ["q1", "q2"],
  );
});

test("appending to an empty or absent set just starts one", () => {
  assert.deepEqual(appendCase([], at("p1", "q1")).length, 1);
  assert.deepEqual(appendCase(undefined, at("p1", "q1")).length, 1);
});

test("the shipped set's _comment block survives the append", () => {
  // It explains the format, and it is what someone reads to fill in the
  // skeleton they just captured. Dropping it would delete the instructions at
  // the exact moment they are needed.
  const preamble = { _comment: ["how to fill this in"] };
  const out = appendCase([preamble, at("p1", "q1")], at("p1", "q2"));
  assert.equal(out[0], preamble);
  assert.equal(out.length, 3);
});

test("appending a case for another project is refused before the file is written", () => {
  // The same rule oneProjectOrThrow enforces at run time, caught one step
  // earlier — here the set has not been modified yet, so nothing needs undoing.
  assert.throws(() => appendCase([at("old", "q1")], at("new", "q2")), /already holds cases for/);
  assert.throws(() => appendCase([at("old", "q1")], at("new", "q2")), /--set benchmarks\//);
});

test("a case with no question is preamble, not a conflicting case", () => {
  assert.doesNotThrow(() => appendCase([{ _comment: ["x"], projectId: "other" }], at("p1", "q")));
});

test("a set of skeletons is told to fill them in, never to capture more", () => {
  // The refusal this replaces said "Build them with --capture", printed to
  // someone who had just run exactly that. Capturing again appends another
  // empty case to a file whose problem is that its cases are empty.
  const said = unmarkedRefusal("benchmarks/crop_eval_set.json", [
    { question: "what is between column lines 7 and 8?" },
  ]);
  assert.match(said, /Capturing again will not fix it/);
  assert.doesNotMatch(said, /--capture/, "the command that produced this file is not the fix");
  assert.match(said, /benchmarks\/crop_eval_set\.json/, "names the file to open");
  assert.match(said, /expectedText/);
});

test("the refusal lists the questions waiting to be answered", () => {
  const said = unmarkedRefusal("s.json", [{ question: "what is at 4/B?" }, { question: "what is at 7/C?" }]);
  assert.match(said, /what is at 4\/B\?/);
  assert.match(said, /what is at 7\/C\?/);
});

test("the refusal counts in English", () => {
  // The saturation line shipped "1 of 40 case" because its test asserted the
  // sentence's claims and not its grammar. Assert the phrase.
  assert.match(unmarkedRefusal("s.json", [{ question: "q" }]), /all 1 case in s\.json is a skeleton/);
  assert.match(
    unmarkedRefusal("s.json", [{ question: "q" }, { question: "r" }]),
    /all 2 cases in s\.json are skeletons/,
  );
});

test("a long skeleton list is truncated rather than filling the screen", () => {
  const many = Array.from({ length: 14 }, (_, i) => ({ question: `q${i}` }));
  const said = unmarkedRefusal("s.json", many);
  assert.match(said, /\.\.\. and 4 more/);
  assert.doesNotMatch(said, /"q13"/);
});

test("the example expectation is not copied from this sheet's retrieval hits", () => {
  // Same rule as the vision prompt's synthetic examples: an expectation that
  // arrives from the tool rather than the drawing measures agreement with the
  // behaviour it was captured from.
  const said = unmarkedRefusal("s.json", [{ question: "q" }]);
  assert.match(said, /from the DRAWING rather than from what retrieval returned/);
});

test("the first capture into a new file says the run will REFUSE, not skip", () => {
  // The claim that mattered: a lone empty case is not skipped, it is refused.
  const said = captureFollowUp("s.json", "p1", [{ projectId: "p1", question: "q", expectedText: "" }]);
  assert.match(said, /REFUSES/);
  assert.doesNotMatch(said, /SKIPPED/);
  assert.match(said, /NOW FILL IT IN/);
});

test("a capture into a set that already scores says the new case is skipped", () => {
  const said = captureFollowUp("s.json", "p1", [
    { projectId: "p1", question: "old", expectedText: "IT-2 STEEL" },
    { projectId: "p1", question: "new", expectedText: "" },
  ]);
  assert.match(said, /SKIPPED/);
  assert.doesNotMatch(said, /REFUSES/);
  assert.match(said, /the 1 case already filled in/, "singular on one, and it counts the filled ones");
});

test("the follow-up counts only cases that can actually be scored", () => {
  const said = captureFollowUp("s.json", "p1", [
    { _comment: "how to fill this in" },
    { projectId: "p1", question: "a", expectedText: "X" },
    { projectId: "p1", question: "b", expectedPages: [3] },
    { projectId: "p1", question: "c", expectedText: "" },
  ]);
  assert.match(said, /the 2 cases already filled in/, "the preamble is not a case; the empty one is not scored");
});

test("the follow-up always ends with the command that runs the set", () => {
  for (const written of [
    [{ projectId: "p1", question: "q", expectedText: "" }],
    [{ projectId: "p1", question: "q", expectedText: "X" }],
  ]) {
    const said = captureFollowUp("benchmarks/crop_eval_set.json", "a3c28a63", written);
    assert.match(said, /--set benchmarks\/crop_eval_set\.json --project a3c28a63/);
  }
});

test("the headline counts in English", () => {
  // "1 cases", the same slip as drawing_eval's "1 of 40 case". The version
  // that reads correctly in the plural is exactly the one that reads wrong here.
  assert.match(headline({ cases: 1, "recall@18": 1, mrr: 0.5 }, 18), /^ {2}1 case · /);
  assert.match(headline({ cases: 2, "recall@18": 1, mrr: 0.5 }, 18), /^ {2}2 cases · /);
});

test("a small set is told what its rate's step size is", () => {
  // 100.0% on one case reads exactly like a result. It is 1 of 1.
  const said = headline({ cases: 1, "recall@18": 1, mrr: 0.5 }, 18);
  assert.match(said, /steps of 100\.0 points/);
  assert.match(said, /it is 1 of 1/);
  assert.match(said, /cannot separate two configurations/);
});

test("the step size is the one a single answer actually moves", () => {
  assert.match(headline({ cases: 4, "recall@18": 0.5, mrr: 0.3 }, 18), /steps of 25\.0 points/);
  assert.match(headline({ cases: 4, "recall@18": 0.5, mrr: 0.3 }, 18), /it is 2 of 4/);
});

test("a set big enough to carry a percentage is left alone", () => {
  const said = headline({ cases: 10, "recall@18": 0.7, mrr: 0.5 }, 18);
  assert.equal(said, "  10 cases · recall@18 70.0% · MRR 0.500");
  assert.doesNotMatch(said, /steps of/);
});

test("an empty set claims no resolution rather than dividing by zero", () => {
  const said = headline({ cases: 0, "recall@18": 0, mrr: 0 }, 18);
  assert.doesNotMatch(said, /Infinity|NaN|steps of/);
});
