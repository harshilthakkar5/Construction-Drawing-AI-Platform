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
import { labelVocabulary, mentions, namedLabels, score, tally } from "./drawing_eval.mjs";

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

// ---------------------------------------------------------------------------
// The fifth outcome: an answer that named a real label from the wrong part of
// the sheet. Scored as "abstained" until a run went from 11 correct / 4 wrong
// to 0 / 0 with everything in that bucket, and the report called it restraint.
// ---------------------------------------------------------------------------

const FOOTINGS = ["F7", "F8", "F9", "F10", "F11", "F12", "F13"];

test("scores a third label as off-target, not abstained", () => {
  assert.equal(score("That grid carries footing F11.", testCase, FOOTINGS), "off-target");
});

test("a genuine refusal is still abstained even with a vocabulary", () => {
  assert.equal(
    score("The drawings do not show a footing at that intersection.", testCase, FOOTINGS),
    "abstained",
  );
});

test("off-target never displaces the outcomes that name the truth", () => {
  // The vocabulary contains every label, including these two. Order matters:
  // an answer that names the truth is correct whatever else it mentions.
  assert.equal(score("Footing F12.", testCase, FOOTINGS), "correct");
  assert.equal(score("Footing F9.", testCase, FOOTINGS), "wrong");
  assert.equal(score("Either F12 or F9.", testCase, FOOTINGS), "hedged");
});

test("without a vocabulary the scorer keeps its old, blinder behaviour", () => {
  // Explicit so the default is a decision rather than an accident: callers that
  // have no label list get the pre-existing four outcomes.
  assert.equal(score("That grid carries footing F11.", testCase), "abstained");
});

test("namedLabels reports every label an answer touched", () => {
  assert.deepEqual(namedLabels("F7 and F13 are nearby", FOOTINGS), ["F7", "F13"]);
  assert.deepEqual(namedLabels("no marks here", FOOTINGS), []);
});

test("labelVocabulary keeps each tag's labels apart", () => {
  const vocab = labelVocabulary([
    { tag: "grid-footing", expected: "F9", distractor: "F7" },
    { tag: "grid-footing", expected: "F9", distractor: "F8" },
    { tag: "grid-column", expected: "HSS8X8X3/8", distractor: null },
  ]);
  assert.deepEqual(vocab.get("grid-footing"), ["F9", "F7", "F8"]);
  // A null distractor must not become a vocabulary entry: mentions() would
  // reject it anyway, but a null in the list is a bug waiting for a caller.
  assert.deepEqual(vocab.get("grid-column"), ["HSS8X8X3/8"]);
});

// ---------------------------------------------------------------------------
// The null model. Pooling the mode across tags proposes answering a member size
// to a question about footing marks, and scored a tied run as a win.
// ---------------------------------------------------------------------------

const row = (tag, expected, outcome) => ({ tag, expected, outcome, descriptionChunks: 0 });

test("the baseline for one tag is that tag's majority label", () => {
  const t = tally([
    row("grid-footing", "F9", "correct"),
    row("grid-footing", "F9", "abstained"),
    row("grid-footing", "F7", "correct"),
    row("grid-footing", "F8", "wrong"),
  ]);
  assert.equal(t.base.label, "F9");
  assert.equal(t.base.pct, 50);
});

test("the baseline across tags is each tag's own majority, not the pooled mode", () => {
  // Five columns and three footings: pooled, the mode is HSS8X8X3/8 at 3/8 =
  // 38%, which proposes answering a member size to "which footing mark". The
  // honest guesser knows the vocabulary the question asked for: 3 + 2 = 5/8.
  const rows = [
    row("grid-column", "HSS8X8X3/8", "correct"),
    row("grid-column", "HSS8X8X3/8", "correct"),
    row("grid-column", "HSS8X8X3/8", "correct"),
    row("grid-column", "HSS6X6X3/8", "abstained"),
    row("grid-column", "HSS10X10X1/2", "abstained"),
    row("grid-footing", "F9", "correct"),
    row("grid-footing", "F9", "abstained"),
    row("grid-footing", "F7", "abstained"),
  ];
  const t = tally(rows);
  assert.equal(t.base.hits, 5);
  assert.equal(t.base.pct, 62.5);
  // 4/8 = 50% correct against a 62.5% baseline: not beaten. Pooled, the same
  // run would have compared against 38% and printed a win.
  assert.equal(t.beatsBase, false);
});

test("minority-label hits count only correct answers off the majority label", () => {
  const t = tally([
    row("grid-footing", "F9", "correct"), // the majority label: a guesser gets this
    row("grid-footing", "F9", "abstained"),
    row("grid-footing", "F13", "correct"), // a guesser cannot reach this one
    row("grid-footing", "F7", "wrong"),
  ]);
  assert.equal(t.correct, 2);
  assert.equal(t.minorityHits, 1);
});

test("two runs can tie on correctness and differ entirely underneath", () => {
  // The case this number exists for. Both score 2/4; only one read anything.
  const guesser = [
    row("grid-footing", "F9", "correct"),
    row("grid-footing", "F9", "correct"),
    row("grid-footing", "F13", "wrong"),
    row("grid-footing", "F7", "wrong"),
  ];
  const reader = [
    row("grid-footing", "F9", "wrong"),
    row("grid-footing", "F9", "abstained"),
    row("grid-footing", "F13", "correct"),
    row("grid-footing", "F7", "correct"),
  ];
  assert.equal(tally(guesser).correct, tally(reader).correct);
  assert.equal(tally(guesser).minorityHits, 0);
  assert.equal(tally(reader).minorityHits, 2);
});
