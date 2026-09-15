/**
 * Tests for the drawing eval's scorer.
 *
 * The scorer is the part of a benchmark that can lie. A loose matcher reports a
 * wrong answer as right ("F9" found inside "F90"); a strict one reports a right
 * answer as a miss because the model wrote "HSS 8x8x3/8" for "HSS8X8X3/8".
 * Either way the run prints a number that is not about the system under test —
 * which is how this repo's retrieval benchmark spent two days reporting 40%.
 *
 * Run: node --test benchmarks/drawing_eval.test.mjs
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { mentions, score } from "./drawing_eval.mjs";

test("matches a mark written exactly", () => {
  assert.equal(mentions("The footing at that grid is F12.", "F12"), true);
});

test("does not match a mark inside a longer token", () => {
  // The guard that stops "F9" scoring against a sheet that says "F90".
  assert.equal(mentions("Footing F90 is elsewhere", "F9"), false);
  assert.equal(mentions("see F12A for the variant", "F12"), false);
});

test("matches a member size the model spaced out or lowercased", () => {
  assert.equal(mentions("a HSS 8x8x3/8 column", "HSS8X8X3/8"), true);
  assert.equal(mentions("an hss8x8x3/8 column", "HSS8X8X3/8"), true);
});

test("does not confuse two member sizes that share a prefix", () => {
  assert.equal(mentions("the column is HSS8X8X3/8", "HSS8X8X5/8"), false);
  assert.equal(mentions("the column is HSS8X8X1/2", "HSS8X8X1/2"), true);
});

test("a needle with no value never matches", () => {
  // drawing_truth.py emits a null distractor when the sheet has only one label
  // of that kind; a null must not match everything.
  assert.equal(mentions("anything at all", null), false);
  assert.equal(mentions("anything at all", ""), false);
});

const testCase = { expected: "F12", distractor: "F9" };

test("scores the truth alone as correct", () => {
  assert.equal(score("That grid carries footing F12.", testCase), "correct");
});

test("scores the distractor alone as wrong", () => {
  assert.equal(score("That grid carries footing F9.", testCase), "wrong");
});

test("scores naming both as hedged, not correct", () => {
  // A model that lists every nearby mark has not answered the question, and
  // counting it as correct would reward exactly that evasion.
  assert.equal(score("It is either F12 or F9.", testCase), "hedged");
});

test("scores a refusal as abstained, not wrong", () => {
  // The distinction the whole report rests on: declining to answer is safe
  // behaviour under FR-14, naming the wrong footing is not.
  assert.equal(
    score("The drawings do not show a footing at that intersection.", testCase),
    "abstained",
  );
});
