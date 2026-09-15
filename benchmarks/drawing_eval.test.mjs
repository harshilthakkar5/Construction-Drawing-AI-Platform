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
import {
  applyProjectOverride,
  inventedLabel,
  labelVocabulary,
  mentions,
  namedLabels,
  oneProjectOrThrow,
  score,
  tally,
} from "./drawing_eval.mjs";

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

// ---------------------------------------------------------------------------
// Which corpus was asked. A set generated against one project, run while the
// work happens in another, answers from the wrong ingest and says nothing --
// three runs in a row reported identical numbers for a project whose chunks
// nobody had touched.
// ---------------------------------------------------------------------------

const P1 = "c14a2d6b-b0e8-448d-8897-10f89279f42a";
const P2 = "03d9b557-e7ad-4ceb-908b-99361d7fb10b";

test("a set asking one project is accepted and names it", () => {
  assert.equal(oneProjectOrThrow([{ projectId: P1 }, { projectId: P1 }]), P1);
});

test("a set spanning two projects is refused, naming both", () => {
  assert.throws(
    () => oneProjectOrThrow([{ projectId: P1 }, { projectId: P2 }]),
    (err) => err.message.includes(P1) && err.message.includes(P2),
  );
});

test("--project repoints every case, including ones already agreeing", () => {
  const cases = [{ projectId: P1, tag: "grid-footing" }, { projectId: P2, tag: "grid-column" }];
  const moved = applyProjectOverride(cases, P2);
  assert.deepEqual(moved.map((c) => c.projectId), [P2, P2]);
  // The override is what makes a mixed set runnable, so it must satisfy the
  // check that would otherwise have rejected it.
  assert.equal(oneProjectOrThrow(moved), P2);
});

test("--project leaves the cases untouched when not given", () => {
  const cases = [{ projectId: P1 }];
  assert.equal(applyProjectOverride(cases, null), cases);
  assert.equal(applyProjectOverride(cases, "")[0].projectId, P1);
});

test("the override copies rather than mutating the set in place", () => {
  // The set is re-read to build the label vocabulary; mutating it here would
  // scope a --limit run's vocabulary differently from a full one.
  const cases = [{ projectId: P1 }];
  applyProjectOverride(cases, P2);
  assert.equal(cases[0].projectId, P1);
});

// ---------------------------------------------------------------------------
// A label the model made up. One description of the sheet answered nearly every
// column question with HSS9X9X3/8 -- a real AISC section, on no drawing here --
// and a closed-vocabulary scorer read all 21 as refusals.
// ---------------------------------------------------------------------------

const COLUMN_PATTERN =
  "HSS\\s*\\d+(?:\\.\\d+)?\\s*X\\s*\\d+(?:\\.\\d+)?(?:\\s*/\\s*\\d+)?" +
  "(?:\\s*X\\s*\\d+(?:\\.\\d+)?(?:\\s*/\\s*\\d+)?)?";
const SIZES = ["HSS8X8X3/8", "HSS8X8X5/8", "HSS10X10X1/2", "HSS6X6X3/8"];
const columnCase = {
  expected: "HSS8X8X3/8",
  distractor: "HSS6X6X3/8",
  labelPattern: COLUMN_PATTERN,
};

test("a size that is on no drawing scores invented, not abstained", () => {
  assert.equal(score("The column there is an HSS9X9X3/8.", columnCase, SIZES), "invented");
});

test("a size the sheet does carry is off-target, never invented", () => {
  assert.equal(score("The column there is an HSS10X10X1/2.", columnCase, SIZES), "off-target");
  assert.equal(inventedLabel("an HSS10X10X1/2 column", columnCase, SIZES), null);
});

test("invented never displaces an answer that named the truth", () => {
  // An answer can mention both; naming the right one still decides the outcome.
  assert.equal(score("HSS8X8X3/8, not HSS9X9X3/8.", columnCase, SIZES), "correct");
});

test("a real refusal stays abstained even with a pattern", () => {
  assert.equal(
    score("The drawing does not call out a column at that intersection.", columnCase, SIZES),
    "abstained",
  );
});

test("the model's spacing does not hide an invented size", () => {
  // It writes "HSS 9x9x3/8" for a sheet that would print "HSS9X9X3/8".
  assert.equal(inventedLabel("an HSS 9x9x3/8 column", columnCase, SIZES), "HSS 9x9x3/8");
});

test("a footing mark nowhere on the sheet is invented", () => {
  const footingCase = { expected: "F9", distractor: "F7", labelPattern: "F\\d{1,2}" };
  assert.equal(score("That grid carries footing F42.", footingCase, FOOTINGS), "invented");
  // F11 IS on this sheet, so it is a real mark in the wrong place.
  assert.equal(score("That grid carries footing F11.", footingCase, FOOTINGS), "off-target");
});

test("without a labelPattern the scorer cannot see an invented label", () => {
  // The old blindness, kept explicit: a set generated before labelPattern
  // existed scores these as refusals, and the report says so out loud.
  assert.equal(score("The column there is an HSS9X9X3/8.", { ...columnCase, labelPattern: undefined }, SIZES), "abstained");
});
