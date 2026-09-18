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

import { appendCase, applyProjectOverride, oneProjectOrThrow } from "./retrieval_eval.mjs";

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
