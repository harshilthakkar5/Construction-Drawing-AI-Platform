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
  describeCoverage,
  report,
  vocabularySource,
  inventedLabel,
  labelVocabulary,
  mentions,
  namedLabels,
  oneProjectOrThrow,
  score,
  tally,
  answerConcentration,
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

const caseRow = (tag, expected, outcome) => ({ tag, expected, outcome, descriptionChunks: 0 });

test("the baseline for one tag is that tag's majority label", () => {
  const t = tally([
    caseRow("grid-footing", "F9", "correct"),
    caseRow("grid-footing", "F9", "abstained"),
    caseRow("grid-footing", "F7", "correct"),
    caseRow("grid-footing", "F8", "wrong"),
  ]);
  assert.equal(t.base.label, "F9");
  assert.equal(t.base.pct, 50);
});

test("the baseline across tags is each tag's own majority, not the pooled mode", () => {
  // Five columns and three footings: pooled, the mode is HSS8X8X3/8 at 3/8 =
  // 38%, which proposes answering a member size to "which footing mark". The
  // honest guesser knows the vocabulary the question asked for: 3 + 2 = 5/8.
  const rows = [
    caseRow("grid-column", "HSS8X8X3/8", "correct"),
    caseRow("grid-column", "HSS8X8X3/8", "correct"),
    caseRow("grid-column", "HSS8X8X3/8", "correct"),
    caseRow("grid-column", "HSS6X6X3/8", "abstained"),
    caseRow("grid-column", "HSS10X10X1/2", "abstained"),
    caseRow("grid-footing", "F9", "correct"),
    caseRow("grid-footing", "F9", "abstained"),
    caseRow("grid-footing", "F7", "abstained"),
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
    caseRow("grid-footing", "F9", "correct"), // the majority label: a guesser gets this
    caseRow("grid-footing", "F9", "abstained"),
    caseRow("grid-footing", "F13", "correct"), // a guesser cannot reach this one
    caseRow("grid-footing", "F7", "wrong"),
  ]);
  assert.equal(t.correct, 2);
  assert.equal(t.minorityHits, 1);
});

test("two runs can tie on correctness and differ entirely underneath", () => {
  // The case this number exists for. Both score 2/4; only one read anything.
  const guesser = [
    caseRow("grid-footing", "F9", "correct"),
    caseRow("grid-footing", "F9", "correct"),
    caseRow("grid-footing", "F13", "wrong"),
    caseRow("grid-footing", "F7", "wrong"),
  ];
  const reader = [
    caseRow("grid-footing", "F9", "wrong"),
    caseRow("grid-footing", "F9", "abstained"),
    caseRow("grid-footing", "F13", "correct"),
    caseRow("grid-footing", "F7", "correct"),
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

test("a description nothing retrieved is reported as nothing, not as coverage", () => {
  assert.match(describeCoverage([], []), /NONE/);
});

test("one chunk on the sheet says the description was never split", () => {
  const said = describeCoverage(["a"], ["a"]);
  assert.match(said, /1, of which 1 reached/);
  assert.match(said, /stored whole/);
});

test("the failure a per-case count cannot show: every case retrieved a description, and it was always the SAME one", () => {
  // The run that prompted this reported "description in prompt 40/40" — which
  // reads as full coverage — while naming exactly one chunk id. Five of the six
  // pieces of that sheet's description were in the corpus and unreachable, and
  // nothing in the report said so.
  const said = describeCoverage(["a", "b", "c", "d", "e", "f"], ["a"]);
  assert.match(said, /6, of which 1 reached/);
  assert.match(said, /other 5/);
  // The point of saying it: the obvious next move is the wrong one.
  assert.match(said, /VLM_MAX_TOKENS/);
});

test("a chunk reached by some other page's question does not count as coverage here", () => {
  assert.match(describeCoverage(["a", "b"], ["a", "zz"]), /2, of which 1 reached/);
});

test("repeats in the reached list are one chunk, not many", () => {
  assert.match(describeCoverage(["a", "b"], ["a", "a", "a"]), /2, of which 1 reached/);
});

test("full reach says so plainly rather than proposing a fix", () => {
  const said = describeCoverage(["a", "b"], ["b", "a"]);
  assert.match(said, /every piece is reachable/);
  assert.doesNotMatch(said, /VLM_MAX_TOKENS/);
});

const verdictRow = (tag, expected, outcome) => ({
  tag,
  expected,
  outcome,
  grid: "1/A",
  said: "",
  labelPattern: "F\\d{1,2}",
  descriptionChunks: 1,
  descriptionChunkIds: ["d1"],
  projectId: "p",
});

test("a run that declines reports coverage and how it did on what it answered", () => {
  // The baseline answers all four. This run answers two and gets both right —
  // 50% raw, which loses to a 50% baseline, and 100% on what it attempted.
  const t = tally([
    verdictRow("grid-footing", "F9", "correct"),
    verdictRow("grid-footing", "F7", "correct"),
    verdictRow("grid-footing", "F9", "abstained"),
    verdictRow("grid-footing", "F9", "abstained"),
  ]);
  assert.equal(t.answered, 2);
  assert.equal(t.coverage, 50);
  assert.equal(t.selective, 100);
  assert.equal(t.pct, 50);
});

test("with nothing declined, coverage is total and selective accuracy is the raw rate", () => {
  const t = tally([verdictRow("grid-footing", "F9", "correct"), verdictRow("grid-footing", "F7", "wrong")]);
  assert.equal(t.coverage, 100);
  assert.equal(t.selective, t.pct);
});

function verdict(rows) {
  const said = [];
  const real = console.log;
  console.log = (...args) => said.push(args.join(" "));
  try {
    report(rows, false, []);
  } finally {
    console.log = real;
  }
  return said.join("\n");
}

test("a below-baseline run whose hits are mostly the majority label is called what it is", () => {
  // Four correct, all on F9, the majority. Nothing here a guesser could not do.
  const rows = [
    ...Array.from({ length: 4 }, () => verdictRow("grid-footing", "F9", "correct")),
    ...Array.from({ length: 8 }, () => verdictRow("grid-footing", "F9", "wrong")),
  ];
  assert.match(verdict(rows), /ZERO comprehension/);
});

test("a below-baseline run is NOT called zero comprehension when its hits are minority labels", () => {
  // The report's own minority-hit column is a measurement of the very claim
  // "the correct answers are a frequency prior". Printing that verdict over
  // 4-of-5 minority hits is the report contradicting its own evidence — the
  // same failure as the pooled baseline, on the most decisive line it prints.
  const rows = [
    verdictRow("grid-footing", "F9", "correct"),
    verdictRow("grid-footing", "F7", "correct"),
    verdictRow("grid-footing", "F8", "correct"),
    verdictRow("grid-footing", "F10", "correct"),
    verdictRow("grid-footing", "F11", "correct"),
    ...Array.from({ length: 8 }, () => verdictRow("grid-footing", "F9", "wrong")),
    ...Array.from({ length: 7 }, () => verdictRow("grid-footing", "F9", "abstained")),
  ];
  const said = verdict(rows);
  assert.doesNotMatch(said, /ZERO comprehension/);
  assert.match(said, /Below baseline/);
  assert.match(said, /a frequency prior produces at a rate of zero/);
  assert.match(said, /declined 7\/20/);
});

test("the below-baseline verdict still says plainly that the baseline was not beaten", () => {
  const rows = [
    verdictRow("grid-footing", "F7", "correct"),
    ...Array.from({ length: 9 }, () => verdictRow("grid-footing", "F9", "abstained")),
  ];
  assert.match(verdict(rows), /Below baseline/);
});

test("a mark from elsewhere on the sheet is off-target, not invented", () => {
  // The measured false positive. The set asks about seven footing marks; the
  // sheet also carries F6, at an intersection no case covers. Scored against a
  // vocabulary built from the SET, naming F6 means "the model made this up" —
  // the one outcome that accuses it of fabrication.
  const cases = [
    { tag: "grid-footing", expected: "F9", distractor: "F8", sheetLabels: ["F6", "F8", "F9"] },
  ];
  const vocab = labelVocabulary(cases).get("grid-footing");
  assert.ok(vocab.includes("F6"));
  assert.equal(
    score("The footing there is F6.", { expected: "F9", distractor: "F8", labelPattern: "F\\d{1,2}" }, vocab),
    "off-target",
  );
});

test("a mark on no part of the drawing is still invented", () => {
  const cases = [
    { tag: "grid-footing", expected: "F9", distractor: "F8", sheetLabels: ["F6", "F8", "F9"] },
  ];
  const vocab = labelVocabulary(cases).get("grid-footing");
  assert.equal(
    score("The footing there is F44.", { expected: "F9", distractor: "F8", labelPattern: "F\\d{1,2}" }, vocab),
    "invented",
  );
});

test("a set with no sheetLabels still builds a vocabulary, and says which one it is", () => {
  const withSheet = [{ tag: "t", expected: "A", distractor: "B", sheetLabels: ["A", "B", "C"] }];
  const withoutSheet = [{ tag: "t", expected: "A", distractor: "B" }];
  assert.equal(vocabularySource(withSheet), "sheet");
  assert.equal(vocabularySource(withoutSheet), "set");
  // The fallback must still work — an old set runs, it just over-reports invented.
  assert.deepEqual(labelVocabulary(withoutSheet).get("t").sort(), ["A", "B"]);
});

test("the report says outright when the vocabulary is only the set's", () => {
  const rows = [verdictRow("grid-footing", "F9", "correct")];
  const said = verdict(rows);
  assert.match(said, /No case carries sheetLabels/);
  assert.match(said, /scores INVENTED rather than off-target/);
});

// A row that NAMED something, which the concentration measurement reads and
// the verdict rows above deliberately do not.
const namedRow = (tag, expected, outcome, said) => ({
  ...verdictRow(tag, expected, outcome),
  said: outcome === "abstained" ? "" : said ?? expected,
});

test("a tag that answers one label far more often than the sheet shows it is flagged", () => {
  // The measured case: the column tag scored 52%, exactly tying its baseline,
  // and said HSS8X8X3/8 to 15 of the 18 questions it answered. Minority hits
  // read 1/11 and every other number on its line looked like half a result.
  const rows = [
    ...Array.from({ length: 10 }, () => namedRow("grid-column", "HSS8X8X3/8", "correct")),
    ...Array.from({ length: 5 }, () => namedRow("grid-column", "HSS6X6X3/8", "off-target", "HSS8X8X3/8")),
    namedRow("grid-column", "HSS8X8X1/2", "correct"),
    namedRow("grid-column", "HSS8X8X3/8", "wrong", "HSS8X8X5/8"),
    namedRow("grid-column", "HSS10X10X1/2", "invented", "HSS8X10X12"),
    ...Array.from({ length: 3 }, () => namedRow("grid-column", "HSS6X6X5/8", "abstained")),
  ];
  const c = answerConcentration(rows);
  assert.equal(c.label, "HSS8X8X3/8");
  // Abstentions are not answers and must stay out of the denominator.
  assert.equal(c.answered, 18);
  assert.equal(c.named, 15);
  assert.equal(Math.round(c.namedPct), 83);
  assert.equal(Math.round(c.truthPct), 52);
});

test("a tag naming its common label at the rate the sheet shows it is not flagged", () => {
  // The same run's footing tag: F9 named 6 times in 18 against a 32% truth
  // rate, and it scored 74%. The contrast is the whole point of the number.
  const rows = [
    ...Array.from({ length: 5 }, () => namedRow("grid-footing", "F9", "correct")),
    namedRow("grid-footing", "F9", "wrong", "F7"),
    namedRow("grid-footing", "F8", "wrong", "F9"),
    ...Array.from({ length: 6 }, (_, i) => namedRow("grid-footing", `F1${i}`, "correct")),
    ...Array.from({ length: 5 }, (_, i) => namedRow("grid-footing", `F${i + 2}`, "correct")),
    namedRow("grid-footing", "F13", "abstained"),
  ];
  const c = answerConcentration(rows);
  assert.equal(c.label, "F9");
  assert.ok(c.gap < 15, `expected no over-naming, got ${c.gap}pt`);
});

test("concentration is measured per tag, never pooled across them", () => {
  // Pooling is the mistake the baseline already had to unlearn: a footing
  // question could never be answered with a column size, so mixing the two
  // dilutes the very concentration this exists to expose. Twelve calibrated
  // footing rows must not wash out six identical column answers.
  const rows = [
    ...Array.from({ length: 6 }, () => namedRow("grid-column", "HSS8X8X3/8", "correct")),
    ...Array.from({ length: 6 }, () => namedRow("grid-column", "HSS6X6X3/8", "off-target", "HSS8X8X3/8")),
    ...Array.from({ length: 12 }, (_, i) => namedRow("grid-footing", `F${i + 1}`, "correct")),
  ];
  const c = answerConcentration(rows);
  assert.equal(c.tag, "grid-column");
  assert.equal(c.named, 12);
  assert.equal(c.answered, 12);
  assert.equal(Math.round(c.truthPct), 50);
});

test("the report names an over-naming tag even when the set beat its baseline", () => {
  // The run this came from beat the baseline overall — 63% against 43% — while
  // one of its two tags tied its own baseline and answered one word to 83% of
  // what it attempted. A set-wide verdict is exactly where that hides.
  const rows = [
    ...Array.from({ length: 6 }, () => namedRow("grid-column", "HSS8X8X3/8", "correct")),
    ...Array.from({ length: 4 }, () => namedRow("grid-column", "HSS6X6X3/8", "off-target", "HSS8X8X3/8")),
    ...Array.from({ length: 10 }, (_, i) => namedRow("grid-footing", `F${i + 1}`, "correct")),
  ];
  const said = verdict(rows);
  assert.match(said, /Beat the baseline/);
  assert.match(said, /Watch grid-column/);
  assert.match(said, /"HSS8X8X3\/8" 10 times in 10/);
});

test("a tag that answered nothing reports no concentration rather than zero", () => {
  const rows = Array.from({ length: 4 }, () => namedRow("grid-footing", "F9", "abstained"));
  assert.equal(answerConcentration(rows), null);
  // And the report must not print a line about it.
  assert.doesNotMatch(verdict(rows), /over-naming|in step with the sheet/);
});

test("over-naming is not called a guess when the tag's hits are minority labels", () => {
  // The measured case. With GEMINI_THINKING_LEVEL=low the footing tag reached
  // for F8 far more often than the sheet shows it — past the threshold — and
  // still scored 12/19 with 10 of those hits on minority marks, which a
  // frequency prior produces at a rate of zero. Printing "the shape of a guess"
  // over that is the report contradicting its own evidence, the same failure
  // the set-wide verdict already had to be gated against.
  const rows = [
    // F9 is the tag's majority truth; four are read correctly, two are answered F8.
    ...Array.from({ length: 4 }, () => namedRow("grid-footing", "F9", "correct")),
    ...Array.from({ length: 2 }, () => namedRow("grid-footing", "F9", "off-target", "F8")),
    // F8 is the truth three times: read twice, declined once.
    ...Array.from({ length: 2 }, () => namedRow("grid-footing", "F8", "correct")),
    namedRow("grid-footing", "F8", "abstained"),
    // Ten minority marks: six read, four answered F8.
    ...Array.from({ length: 6 }, (_, i) => namedRow("grid-footing", `F1${i}`, "correct")),
    ...Array.from({ length: 4 }, (_, i) => namedRow("grid-footing", `F2${i}`, "off-target", "F8")),
  ];
  const c = answerConcentration(rows);
  assert.equal(c.label, "F8");
  assert.ok(c.gap >= 15, `expected over-naming, got ${c.gap}pt`);
  assert.equal(c.prior, false);
  const said = verdict(rows);
  assert.doesNotMatch(said, /shape of a guess/);
  assert.match(said, /neither that one nor the tag's majority/);
});

test("fixating on a NON-majority label does not earn the reading defence", () => {
  // The measured trap, and the one minority hits alone walked straight into.
  // The column tag answered one size to 17 of 21 questions; that size is the
  // truth at three intersections, so it was right there BY COINCIDENCE — and
  // because the size is not the tag's majority, all three scored as minority
  // hits, the one figure that is supposed to be un-fakeable. A frequency prior
  // over the wrong frequency, credited as comprehension, on a tag scoring 24%
  // where guessing scores 52%.
  const rows = [
    // The truth is the majority label 11 times; it answers the fixated one.
    ...Array.from({ length: 11 }, () => namedRow("grid-column", "HSS8X8X3/8", "off-target", "HSS6X6X3/8")),
    // The fixated label IS the truth three times, so those score correct.
    ...Array.from({ length: 3 }, () => namedRow("grid-column", "HSS6X6X3/8", "correct", "HSS6X6X3/8")),
    ...Array.from({ length: 3 }, () => namedRow("grid-column", "HSS6X6X1/2", "off-target", "HSS6X6X3/8")),
  ];
  const c = answerConcentration(rows);
  assert.equal(c.label, "HSS6X6X3/8");
  // Every hit is the label it fixated on: nothing here was read.
  assert.equal(c.correct, 3);
  assert.equal(c.minorityHits, 3, "the old gate counted all three as un-fakeable");
  assert.equal(c.independentHits, 0);
  assert.equal(c.prior, true);
  const said = verdict(rows);
  assert.match(said, /shape of a guess/);
  assert.match(said, /right by coincidence/);
});

test("a tag below its own baseline is never credited with reading the rest", () => {
  // 24% where guessing scores 52% is fixation whatever the hits look like, so
  // the defence does not apply however the correct answers are distributed.
  const rows = [
    ...Array.from({ length: 11 }, () => namedRow("grid-column", "HSS8X8X3/8", "off-target", "HSS6X6X3/8")),
    ...Array.from({ length: 3 }, () => namedRow("grid-column", "HSS6X6X3/8", "correct", "HSS6X6X3/8")),
    // Two hits that fixation could NOT produce — and it still loses to guessing.
    namedRow("grid-column", "HSS10X10X1/2", "correct"),
    namedRow("grid-column", "HSS6X6X1/2", "correct"),
  ];
  const c = answerConcentration(rows);
  assert.equal(c.independentHits, 2);
  assert.equal(c.beatsBase, false);
  assert.equal(c.prior, true);
  assert.match(verdict(rows), /where guessing this tag's most common label scores/);
});

test("the set-wide warning names the guessing tag, not the widest gap", () => {
  // A reading tag can lean harder on one label than a guessing tag does. Sorted
  // by gap alone the report would put the wrong tag on the line that decides
  // how the whole run is read.
  const rows = [
    // Guessing: every hit is the majority label.
    ...Array.from({ length: 5 }, () => namedRow("grid-column", "HSS8X8X3/8", "correct")),
    ...Array.from({ length: 2 }, () => namedRow("grid-column", "HSS8X8X3/8", "abstained")),
    ...Array.from({ length: 3 }, () => namedRow("grid-column", "HSS6X6X3/8", "off-target", "HSS8X8X3/8")),
    // Reading: a WIDER gap, but on labels a prior cannot reach.
    ...Array.from({ length: 8 }, (_, i) => namedRow("grid-footing", `F${i + 2}`, "correct")),
    ...Array.from({ length: 6 }, (_, i) => namedRow("grid-footing", `F2${i}`, "off-target", "F2")),
  ];
  const footing = answerConcentration(rows.filter((r) => r.tag === "grid-footing"));
  const column = answerConcentration(rows.filter((r) => r.tag === "grid-column"));
  assert.ok(footing.gap > column.gap, `footing ${footing.gap}pt should exceed column ${column.gap}pt`);
  assert.equal(footing.prior, false);
  assert.equal(column.prior, true);
  assert.equal(answerConcentration(rows).tag, "grid-column");
  assert.match(verdict(rows), /Watch grid-column/);
});
