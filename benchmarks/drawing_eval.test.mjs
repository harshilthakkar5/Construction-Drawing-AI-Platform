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
  drift,
  runHistory,
  citationSupport,
  labelComponents,
  componentMisreads,
  systematicOffset,
  componentSwap,
  saturation,
  caseLocation,
  CHUNK_MAX_TOKENS,
  OUTCOMES,
  progressLine,
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


// Three intersections on one row line, one bay (130pt) apart.
const atPoint = (grid, x, expected, outcome, said) => ({
  ...verdictRow("grid-footing", expected, outcome),
  grid,
  point: [x, 600],
  said: outcome === "abstained" ? "" : said ?? expected,
});

test("a mark read correctly and placed one bay off is reported as drift", () => {
  // The measured case: five of seven footing misses in one run named the mark
  // that is the truth at the NEXT intersection, 130-156pt away. Scored
  // off-target, which says "named some other label of that kind" and reads as
  // a model that could not read the mark — when it read it correctly and put
  // it in the wrong place. The two failures have opposite fixes.
  const rows = [
    atPoint("3/B", 1000, "F9", "wrong", "F10"),
    atPoint("4/B", 1130, "F10", "correct"),
    atPoint("4.6/B", 1260, "F11", "correct"),
  ];
  const [found] = drift(rows);
  assert.equal(found.grid, "3/B", "the annotation belongs to the row that NAMED the label");
  assert.equal(found.named, "F10");
  assert.equal(found.truthAt, "4/B");
  assert.equal(Math.round(found.away), 130);
});

test("a label from the far side of the sheet is not drift", () => {
  // off-target already says "a label from elsewhere on the drawing", and that
  // is what this is. Calling it drift would turn one measurement into two
  // names for the same thing.
  const rows = [
    atPoint("3/B", 1000, "F9", "off-target", "F13"),
    atPoint("4/B", 1130, "F10", "correct"),
    atPoint("9/B", 3000, "F13", "correct"),
  ];
  assert.deepEqual(drift(rows), []);
});

test("the report annotates the drifted row and says how far", () => {
  const rows = [
    atPoint("3/B", 1000, "F9", "wrong", "F10"),
    atPoint("4/B", 1130, "F10", "correct"),
    atPoint("4.6/B", 1260, "F11", "off-target", "F10"),
  ];
  const said = verdict(rows);
  assert.match(said, /3\/B.*said F10.*F10 is the truth at 4\/B, 130pt away/);
  assert.match(said, /2 of 2 grid-footing misses name the truth at an ADJACENT intersection/);
  assert.match(said, /Locality is the lever/);
});

test("drift needs at least two misses before it claims a mechanism", () => {
  // One annotated row is a coincidence; the summary line is a claim about how
  // the model is failing, and one case cannot support it.
  const rows = [
    atPoint("3/B", 1000, "F9", "wrong", "F10"),
    atPoint("4/B", 1130, "F10", "correct"),
  ];
  assert.equal(drift(rows).length, 1);
  assert.doesNotMatch(verdict(rows), /name the truth at an ADJACENT/);
});

// --- Concentration cannot tell a prior from a misplacement on its own -------

// One row line, five intersections a bay apart. The model reads "S3" correctly
// where it lives and smears it onto its two neighbours: it over-names S3
// exactly as hard as a tag that never looked at the drawing.
const smear = (grid, x, expected, outcome, said) => ({
  ...verdictRow("grid-footing", expected, outcome),
  grid,
  point: [x, 600],
  said: outcome === "abstained" ? "" : said ?? expected,
});

test("over-naming whose misses are drift is called placement, not a guess", () => {
  const rows = [
    smear("1/B", 1000, "S1", "correct"),
    smear("2/B", 1130, "S2", "wrong", "S3"),
    smear("3/B", 1260, "S3", "correct"),
    smear("4/B", 1390, "S4", "off-target", "S3"),
    smear("5/B", 1520, "S1", "correct"),
  ];
  const c = answerConcentration(rows);
  assert.equal(c.label, "S3");
  assert.equal(c.namedMisses, 2);
  assert.equal(c.placedMisses, 2, "both misses name S3's own intersection one bay away");
  assert.equal(c.placement, true);
  assert.equal(c.prior, false, "a misplaced read is not a frequency prior");
  assert.match(verdict(rows), /placement, not a prior/);
  assert.match(verdict(rows), /read correctly and put in the wrong place/);
  assert.doesNotMatch(verdict(rows), /the shape of a guess/);
});

test("over-naming a label from nowhere near the misses is still a guess", () => {
  // Same shape, same concentration count — and the over-named label's own
  // intersection is on the far side of the sheet, so nothing was misplaced.
  const rows = [
    smear("1/B", 1000, "S1", "correct"),
    smear("2/B", 1130, "S2", "wrong", "S9"),
    smear("3/B", 1260, "S3", "off-target", "S9"),
    smear("4/B", 1390, "S4", "off-target", "S9"),
    smear("9/B", 4000, "S9", "correct"),
  ];
  const c = answerConcentration(rows);
  assert.equal(c.label, "S9");
  assert.equal(c.placedMisses, 0);
  assert.equal(c.placement, false);
  assert.equal(c.prior, true);
  assert.match(verdict(rows), /the shape of a guess/);
});

test("placement needs MOST of the over-named misses, not one", () => {
  // One drifted miss among four does not change what the tag is doing, and a
  // gate that flipped on a single case would excuse any fixation that happened
  // to sit next to its own home.
  const rows = [
    smear("1/B", 1000, "S1", "correct"),
    smear("2/B", 1130, "S2", "wrong", "S3"),
    smear("3/B", 1260, "S3", "correct"),
    smear("7/B", 2000, "S7", "off-target", "S3"),
    smear("8/B", 2130, "S8", "off-target", "S3"),
    smear("9/B", 2260, "S9", "off-target", "S3"),
  ];
  const c = answerConcentration(rows);
  assert.equal(c.namedMisses, 4);
  assert.equal(c.placedMisses, 1);
  assert.equal(c.placement, false);
});

// --- One run is one sample --------------------------------------------------

const ran = (ranAt, projectId, ids, pct, label = null, byTag = null) => ({
  ranAt,
  projectId,
  descriptionChunkIds: ids,
  label,
  byTag,
  pct,
});

test("separate ingests are listed oldest first with the range between them", () => {
  // The measured case: two ingests of the same sheet, on code whose
  // description path was byte-identical between them, scored 63% and 80%.
  // The range is REPORTED and explicitly not called an error bar — it mixes
  // real changes with run-to-run spread, and only repeating one configuration
  // separates those.
  const h = runHistory([
    ran("2026-09-17T06:00Z", "p1", ["a"], 43),
    ran("2026-09-17T06:37Z", "p2", ["b"], 63),
    ran("2026-09-17T08:51Z", "p3", ["c"], 80),
  ]);
  assert.equal(h.ingests.length, 3);
  assert.equal(h.range, 37);
  assert.deepEqual(h.ingests.map((i) => i.pct), [43, 63, 80], "oldest first");
  assert.deepEqual(h.rescored, []);
});

test("runs with NO descriptions are separate samples, never one corpus", () => {
  // The bug this feature shipped with. An empty chunk-id list is not a corpus
  // identity, so keying on it collapsed every pre-vision run into one group
  // and reported them as ONE corpus that had scored four different numbers —
  // an alarm about something that does not exist, printed by the feature added
  // to stop a number being over-read.
  const h = runHistory([
    ran("2026-09-17T01:00Z", "p1", [], 35),
    ran("2026-09-17T02:00Z", "p2", [], 38),
    ran("2026-09-17T03:00Z", "p3", [], 48),
  ]);
  assert.equal(h.ingests.length, 3, "three runs with no descriptions are three samples");
  assert.deepEqual(h.rescored, [], "and none of them is a re-score of another");
  assert.deepEqual(h.ingests.map((i) => i.described), [false, false, false]);
});

test("two description-less runs at the same instant are still not one corpus", () => {
  // The key falls back to the timestamp, so a collision is the one way two
  // runs with no descriptions can land in the same group. They still describe
  // no shared corpus, so the alarm — which claims temperature: 0 was violated
  // — must not fire on them.
  const h = runHistory([
    ran("2026-09-17T01:00Z", "p1", [], 35),
    ran("2026-09-17T01:00Z", "p2", [], 48),
  ]);
  assert.deepEqual(h.rescored, []);
});

test("the recent tail is what the report shows, not the whole history", () => {
  // The full list runs back to runs with no vision pass at all, which are a
  // different system rather than a different setting. Quoting a range across
  // all of them reads as an error bar and measures the project's history.
  const h = runHistory(
    [48, 25, 35, 45, 35, 28, 63, 20, 60, 57, 38, 43, 63, 80, 83].map((pct, i) =>
      ran(`2026-09-17T${String(i).padStart(2, "0")}:00Z`, `p${i}`, [`c${i}`], pct),
    ),
  );
  assert.equal(h.ingests.length, 15);
  assert.equal(h.recent.length, 5);
  assert.deepEqual(h.recent.map((i) => i.pct), [38, 43, 63, 80, 83], "the last five");
  assert.equal(h.range, 45, "83 - 38, not 83 - 20 across every run ever");
});

test("re-scoring the SAME descriptions is one ingest, not two", () => {
  // Re-running the harness against an unchanged corpus is not a second sample
  // of anything. Counting it as one would shrink the spread with runs that
  // measured nothing new.
  const h = runHistory([
    ran("2026-09-17T06:00Z", "p1", ["a", "b"], 63),
    ran("2026-09-17T07:00Z", "p1", ["b", "a"], 63),
  ]);
  assert.equal(h.runs, 2);
  assert.equal(h.ingests.length, 1, "chunk ids in a different order are the same corpus");
  assert.equal(h.range, 0);
  assert.deepEqual(h.rescored, [], "agreeing re-scores are not an alarm, they are the contract");
});

test("the range is over distinct ingests, not over runs", () => {
  // A corpus scored twice contributes ONE sample however many times the
  // harness was pointed at it. Counting each run would widen or narrow the
  // error bar with re-runs that measured nothing new — and the whole point of
  // the number is that it bounds what a comparison can claim.
  const h = runHistory([
    ran("2026-09-17T06:00Z", "p1", ["a"], 80),
    ran("2026-09-17T07:00Z", "p1", ["a"], 40),
    ran("2026-09-17T08:00Z", "p2", ["b"], 75),
  ]);
  assert.equal(h.ingests.length, 2);
  assert.equal(h.range, 35, "40 and 75, not 80 and 40");
});

test("the same descriptions scoring differently is an alarm about the harness", () => {
  // temperature: 0 — same chunks in, same answer out. A disagreement here is
  // the scorer or the chat path moving under the set, and nothing else in the
  // report can see it.
  const h = runHistory([
    ran("2026-09-17T06:00Z", "p1", ["a"], 63),
    ran("2026-09-17T07:00Z", "p1", ["a"], 55),
  ]);
  assert.equal(h.ingests.length, 1);
  assert.deepEqual(h.rescored, [{ projectId: "p1", scores: [55, 63] }]);
});

test("a single run claims no range at all", () => {
  const h = runHistory([ran("2026-09-17T06:00Z", "p1", ["a"], 80)]);
  assert.equal(h.range, 0);
  assert.equal(h.ingests.length, 1);
});

// --- FR-13: does the cited chunk account for the answer? --------------------

test("a label found in a cited chunk is supported", () => {
  const bodies = new Map([["c1", "COLUMN FOOTING SCHEDULE\nF10  5'-0\" SQ x 18\""]]);
  assert.equal(citationSupport("F10", ["c1"], bodies), "supported");
});

test("a label in NO cited chunk is unsupported, however right it is", () => {
  // The measured case: "Per the Column Footing Schedule on S-100.0, the footing
  // mark at the intersection of column line 4 and row line B is F10", citing
  // only the schedule. The claim is a POSITION, which is the one fact the text
  // layer does not hold — it is why the vision pass exists.
  const bodies = new Map([["schedule", "COLUMN FOOTING SCHEDULE\nF7  F8  F9"]]);
  assert.equal(citationSupport("F10", ["schedule"], bodies), "unsupported");
});

test("citing nothing at all is its own answer, not 'unsupported'", () => {
  // A claim with no citation has not broken the chain, it never joined it, and
  // the fix is different: the prompt's citation rule rather than retrieval.
  assert.equal(citationSupport("F10", [], new Map()), "uncited");
});

test("an answer that named no label makes no claim to support", () => {
  assert.equal(citationSupport("", ["c1"], new Map([["c1", "anything"]])), "no-label");
});

test("a hedged answer needs EVERY label it named to be cited", () => {
  // "either F12 or F9" makes two claims. One of them being traceable is not
  // the promise FR-13 makes.
  const bodies = new Map([["c1", "F12 at that grid"]]);
  assert.equal(citationSupport("F12, F9", ["c1"], bodies), "unsupported");
  assert.equal(citationSupport("F12", ["c1"], bodies), "supported");
});

test("a cited id the run never saw cannot support anything", () => {
  // The model can emit an id that was not in its prompt. It is a fabricated
  // citation, and it must never read as support.
  assert.equal(citationSupport("F10", ["never-retrieved"], new Map()), "unsupported");
});

test("support is checked against the CITED chunks, not everything retrieved", () => {
  // The exact shape of the measured failure: the description chunk holds the
  // grid pairing and the schedule does not, and the answer cited the schedule.
  // Checking against the whole retrieved set would call that supported and
  // report the one thing FR-13 exists to catch as fine.
  const bodies = new Map([
    ["schedule", "COLUMN FOOTING SCHEDULE\nF7  F8  F9"],
    ["description", "At 4/B: footing F10, column HSS8X8X3/8."],
  ]);
  assert.equal(citationSupport("F10", ["schedule"], bodies), "unsupported");
  assert.equal(citationSupport("F10", ["description"], bodies), "supported");
  assert.equal(citationSupport("F10", ["schedule", "description"], bodies), "supported");
});

// --- A member size is two facts printed as one string -----------------------

test("a member size splits into a section and a thickness", () => {
  assert.deepEqual(labelComponents("HSS8X8X3/8"), [
    { name: "section", value: "8X8" },
    { name: "thickness", value: "3/8" },
  ]);
  // Written the way a person types it.
  assert.deepEqual(labelComponents("hss 6x6 x 1/2"), [
    { name: "section", value: "6X6" },
    { name: "thickness", value: "1/2" },
  ]);
});

test("a footing mark does not decompose, and says so", () => {
  // "F9" is one token, read or not. A fabricated split would invent a finding.
  assert.equal(labelComponents("F9"), null);
  assert.equal(labelComponents("HSS8X8"), null);
  assert.equal(labelComponents(""), null);
  assert.equal(labelComponents(null), null);
});

const sized = (expected, said, outcome = "off-target") => ({
  tag: "grid-column",
  expected,
  said,
  outcome,
});

test("holding one part across the misses is a misread, not a guess", () => {
  // The measured run: 13 of 14 column misses carried the thickness exactly —
  // 3/8, 5/8 and 1/2 each landing where the drawing puts them — and got only
  // the section wrong, always 8X8 read as 6X6 and never the reverse. The report
  // called that "reaching for one label… while reading nothing". A model
  // reading nothing does not place three different thicknesses correctly
  // thirteen times.
  const rows = [
    ...Array.from({ length: 9 }, () => sized("HSS8X8X3/8", "HSS6X6X3/8")),
    ...Array.from({ length: 3 }, () => sized("HSS8X8X5/8", "HSS6X6X5/8")),
    ...Array.from({ length: 2 }, () => sized("HSS8X8X1/2", "HSS6X6X1/2")),
  ];
  const c = componentMisreads(rows);
  assert.equal(c.misses, 14);
  assert.equal(c.wrongPart, "section");
  assert.equal(c.heldPart, "thickness");
  assert.equal(c.held, 14);
  assert.equal(c.heldDistinct, 3, "three thicknesses, each placed correctly");
  assert.equal(c.halfRead, true);
  assert.deepEqual(c.swaps, [["8X8 read as 6X6", 14]], "one direction, never the reverse");
});

test("one constant answer is NOT a half-read, however well one part matches", () => {
  // The condition the whole measure turns on. Answer HSS6X6X3/8 to everything
  // on a sheet whose columns are mostly X3/8 and the thickness "matches" every
  // time — from a model that never looked. Reading shows up as the held part
  // TRACKING the drawing, not as one value that happens to agree.
  const rows = Array.from({ length: 12 }, () => sized("HSS8X8X3/8", "HSS6X6X3/8"));
  const c = componentMisreads(rows);
  assert.equal(c.held, 12);
  assert.equal(c.heldDistinct, 1);
  assert.equal(c.halfRead, false, "one held value is an artifact of the truth distribution");
});

test("a part held by only half the misses is a lean, not a pattern", () => {
  const rows = [
    ...Array.from({ length: 4 }, () => sized("HSS8X8X3/8", "HSS6X6X3/8")),
    ...Array.from({ length: 2 }, () => sized("HSS8X8X5/8", "HSS6X6X5/8")),
    ...Array.from({ length: 6 }, () => sized("HSS8X8X3/8", "HSS8X8X1/2")),
  ];
  const c = componentMisreads(rows);
  assert.equal(c.halfRead, false, "6 of 12 is not two thirds");
});

test("correct answers and abstentions contribute nothing", () => {
  // This measures how the MISSES fail. Folding in the hits would let a good
  // tag's successes argue that its failures were nearly right.
  const rows = [
    sized("HSS8X8X3/8", "HSS8X8X3/8", "correct"),
    sized("HSS8X8X3/8", "", "abstained"),
  ];
  assert.equal(componentMisreads(rows), null);
});

test("a hedged answer naming two sizes is not decomposed", () => {
  // Two labels is no single reading, and picking one to split would be the
  // report inventing the finding it then reports.
  const rows = [sized("HSS8X8X3/8", "HSS6X6X3/8, HSS8X8X1/2", "hedged")];
  assert.equal(componentMisreads(rows), null);
});

test("the verdict says which PART is wrong instead of calling it a guess", () => {
  const rows = [
    ...Array.from({ length: 9 }, () => ({
      ...namedRow("grid-column", "HSS8X8X3/8", "off-target", "HSS6X6X3/8"),
    })),
    ...Array.from({ length: 3 }, () => ({
      ...namedRow("grid-column", "HSS8X8X5/8", "off-target", "HSS6X6X5/8"),
    })),
    ...Array.from({ length: 2 }, () => ({
      ...namedRow("grid-column", "HSS8X8X1/2", "off-target", "HSS6X6X1/2"),
    })),
  ];
  const c = answerConcentration(rows);
  assert.equal(c.prior, false, "a half-read is not a frequency prior");
  const said = verdict(rows);
  assert.match(said, /WHICH PART is wrong/);
  assert.match(said, /8X8 read as 6X6/);
  assert.doesNotMatch(said, /while reading nothing/);
});

// --- One mistake made N times is not N mistakes ----------------------------

// Five column lines 130pt apart on one row line, mirroring the real sheet's
// geometry closely enough to index.
const onRow = (col, x, expected, outcome, said) => ({
  ...verdictRow("grid-footing", expected, outcome),
  grid: `${col}/B`,
  point: [x, 600],
  said: outcome === "abstained" ? "" : said ?? expected,
});

test("drifted misses sharing one offset are reported as a single enumeration error", () => {
  // The measured run: SEVEN of the footing tag's ten drifted misses were the
  // identical offset — one column line over, same row — column 4 answering
  // with column 3's footing, 4.6 with 4's, 6 with 4.6's, 7 with 6's, 9 with
  // 8's. Every one the same direction, every one exactly one grid step. That
  // is the grid enumerated off by one, not ten independent slips.
  const rows = [
    onRow("1", 1000, "F1", "correct"),
    onRow("2", 1130, "F2", "wrong", "F1"),
    onRow("3", 1260, "F3", "wrong", "F2"),
    onRow("4", 1390, "F4", "wrong", "F3"),
    onRow("5", 1520, "F5", "wrong", "F4"),
  ];
  const off = systematicOffset(rows);
  assert.equal(off.count, 4);
  assert.equal(off.drifted, 4);
  assert.equal(off.columns, -1, "each answer belongs one column line BACK");
  assert.equal(off.rows, 0);
  assert.equal(off.systematic, true);
  assert.match(off.describe, /1 column line back/);
  assert.match(verdict(rows), /the SAME offset/);
  assert.match(verdict(rows), /ENUMERATED off by one/);
});

test("drift in scattered directions is not called systematic", () => {
  // Misses that point different ways are what the drift line already says:
  // labels read right and placed at a neighbour. Calling that one enumeration
  // error would invent a mechanism the evidence does not show.
  const rows = [
    onRow("1", 1000, "F1", "correct"),
    onRow("2", 1130, "F2", "wrong", "F1"),
    onRow("3", 1260, "F3", "wrong", "F4"),
    onRow("4", 1390, "F4", "wrong", "F3"),
    onRow("5", 1520, "F5", "correct"),
  ];
  const off = systematicOffset(rows);
  assert.equal(off.systematic, false, "two one way and one the other is not a pattern");
  assert.doesNotMatch(verdict(rows), /the SAME offset/);
});

test("fewer than three drifted misses claims no mechanism at all", () => {
  // Two in a row on a small sheet is a coincidence, and this line is a claim
  // about how the model is failing.
  const rows = [
    onRow("1", 1000, "F1", "correct"),
    onRow("2", 1130, "F2", "wrong", "F1"),
    onRow("3", 1260, "F3", "wrong", "F2"),
  ];
  assert.equal(systematicOffset(rows), null);
});

test("the offset is counted in grid lines, not points", () => {
  // "One column line over" cannot be said with a distance: the bays on the
  // real sheet run 130 to 218pt. Unequal spacing must not split one offset
  // into several, so these uneven steps all report as the same -1.
  const rows = [
    onRow("1", 1000, "F1", "correct"),
    onRow("2", 1130, "F2", "wrong", "F1"),
    onRow("3", 1260, "F3", "wrong", "F2"),
    onRow("4", 1454, "F4", "wrong", "F3"),
    onRow("5", 1584, "F5", "wrong", "F4"),
  ];
  const off = systematicOffset(rows);
  assert.equal(off.columns, -1);
  assert.equal(off.count, 4, "130pt and 194pt steps are both ONE column line");
  assert.equal(off.systematic, true);
});

test("a neighbour past drift's own window is not counted, and that is drift's limit", () => {
  // Worth writing down rather than working around. `drift` measures the bay as
  // the SHORTEST gap in the set and looks 1.5 bays out, so on a sheet whose
  // bays run 130 to 218pt the widest pair — 1.5 x 130 = 195 — falls outside.
  // Every drift count on this sheet is therefore a LOWER bound, and so is the
  // offset built on it. Changing the threshold would rebase every drift figure
  // in CLAUDE.md against runs that never measured it.
  const rows = [
    onRow("1", 1000, "F1", "correct"),
    onRow("2", 1130, "F2", "wrong", "F1"),
    onRow("3", 1260, "F3", "wrong", "F2"),
    onRow("4", 1390, "F4", "wrong", "F3"),
    onRow("5", 1608, "F5", "wrong", "F4"), // 218pt from its neighbour
  ];
  const off = systematicOffset(rows);
  assert.equal(off.count, 3, "the 218pt pair is outside 1.5 bays and never reaches this");
  assert.equal(off.systematic, true, "the three that do reach it still agree");
});

// --- When the offset and the misread predict the same string ---------------

// The measured run, to the case. Seven column lines and three row lines, the
// row lines 137pt apart and the closest column pair 130 — which is the bay, so
// drift's window is 195pt and reaches a row step but not the widest bay.
const COL = { "2": 0, "4": 200, "4.6": 330, "6": 500, "7": 656, "8": 800, "9": 950 };
const ROW = { B: 0, C: 137, F: 274 };
const col = (grid, expected, said, outcome) => {
  const [c, r] = grid.split("/");
  return {
    ...verdictRow("grid-column", expected, outcome),
    grid,
    said,
    point: [COL[c], ROW[r]],
    labelPattern: "HSS[\\d.]+X[\\d.]+X\\d+/\\d+",
  };
};

// Row C carries HSS8X8 where row F carries HSS6X6 at the same thickness, so
// "took the value one row down" and "read 8X8 as 6X6" produce the identical
// answer at 2/C, 4.6/C and 7/C.
const confoundedSheet = () => [
  col("2/B", "HSS8X8X3/8", "HSS6X6X3/8", "off-target"),
  col("2/C", "HSS8X8X3/8", "HSS6X6X3/8", "wrong"),
  col("2/F", "HSS6X6X3/8", "HSS6X6X3/8", "correct"),
  col("4/B", "HSS8X8X3/8", "HSS8X8X3/8", "correct"),
  col("4/C", "HSS8X8X3/8", "HSS6X6X3/8", "off-target"),
  col("4/F", "HSS6X6X5/8", "HSS6X6X3/8", "wrong"),
  col("4.6/C", "HSS8X8X3/8", "HSS6X6X3/8", "wrong"),
  col("4.6/F", "HSS6X6X3/8", "HSS6X6X3/8", "correct"),
  col("6/B", "HSS8X8X1/2", "HSS8X8X1/2", "correct"),
  col("6/F", "HSS8X8X1/2", "HSS6X6X1/2", "wrong"),
  col("7/C", "HSS8X8X1/2", "HSS6X6X1/2", "wrong"),
  col("7/F", "HSS6X6X1/2", "HSS6X6X1/2", "correct"),
  col("8/B", "HSS8X8X3/8", "HSS6X6X3/8", "off-target"),
  col("8/C", "HSS8X8X3/8", "HSS6X6X3/8", "off-target"),
  col("8/F", "HSS8X8X3/8", "HSS8X8X3/8", "correct"),
  col("9/C", "HSS8X8X3/8", "HSS6X6X3/8", "off-target"),
  col("9/F", "HSS8X8X1/2", "HSS8X8X1/2", "correct"),
];

test("an offset a component misread also explains is not an enumeration error", () => {
  // Without the gate this fixture reports "3 of those 6 are the SAME offset —
  // 1 row line down", two lines above the component line saying nine of the
  // same misses are 8X8 read as 6X6. Both cannot be independent evidence: they
  // are the same three answers, counted twice, explained twice.
  const off = systematicOffset(confoundedSheet());
  assert.equal(off.drifted, 6);
  assert.equal(off.ambiguous, 6, "every drifted miss is one substitution from its own truth");
  assert.equal(off.discriminating, 0, "none of them needs displacement to be explained");
  assert.equal(off.systematic, false);
  assert.equal(off.confound.key, "8X8 read as 6X6");
  assert.equal(off.confound.elsewhere, 4, "four misses no neighbour can account for");
});

test("the report names the substitution it refused the offset claim for", () => {
  const said = verdict(confoundedSheet());
  assert.match(said, /name the truth at an ADJACENT intersection/, "drift itself still reports");
  assert.doesNotMatch(said, /ENUMERATED off by one/);
  assert.match(said, /No offset claim/);
  assert.match(said, /8X8 read as 6X6/);
  assert.match(said, /cannot tell them apart/);
  assert.match(said, /4 misses where no neighbour holds what was said/);
});

test("with no miss outside the drift, the report says neither story is falsifiable", () => {
  // The same sheet minus the four misses displacement cannot reach. Now the
  // misread has no independent evidence either, and the honest line is that
  // this sheet's own label distribution cannot separate them — which is still
  // not a licence to claim the offset.
  const rows = confoundedSheet().filter((r) => !["2/B", "8/B", "8/C", "9/C"].includes(r.grid));
  const off = systematicOffset(rows);
  assert.equal(off.systematic, false);
  assert.equal(off.confound.elsewhere, 0);
  const said = verdict(rows);
  assert.match(said, /unfalsifiable/);
  assert.doesNotMatch(said, /ENUMERATED off by one/);
});

test("an offset no substitution can produce still reports as an enumeration error", () => {
  // The gate is not a blanket refusal for compound labels. Here each answer
  // differs from its own truth in BOTH parts, so no single misread makes it,
  // and the only account left is that the values walked one row line down.
  const both = (grid, expected, said, outcome) => col(grid, expected, said, outcome);
  const rows = [
    both("2/B", "HSS8X8X3/8", "HSS6X6X1/2", "wrong"),
    both("4/B", "HSS10X10X3/8", "HSS5X5X5/8", "wrong"),
    both("6/B", "HSS8X8X1/2", "HSS4X4X3/8", "wrong"),
    both("2/C", "HSS6X6X1/2", "HSS6X6X1/2", "correct"),
    both("4/C", "HSS5X5X5/8", "HSS5X5X5/8", "correct"),
    both("6/C", "HSS4X4X3/8", "HSS4X4X3/8", "correct"),
  ];
  const off = systematicOffset(rows);
  assert.equal(off.ambiguous, 0);
  assert.equal(off.discriminating, 3);
  assert.equal(off.systematic, true);
  assert.equal(off.rows, 1);
  assert.match(verdict(rows), /ENUMERATED off by one/);
});

test("an atomic label has no substitution, so a footing offset is untouched", () => {
  // The run this whole measure was built for. F3 for F4 is not half of a
  // reading, so nothing can confound it and the claim stands as it did.
  const rows = [
    onRow("1", 1000, "F1", "correct"),
    onRow("2", 1130, "F2", "wrong", "F1"),
    onRow("3", 1260, "F3", "wrong", "F2"),
    onRow("4", 1390, "F4", "wrong", "F3"),
    onRow("5", 1520, "F5", "wrong", "F4"),
  ];
  const off = systematicOffset(rows);
  assert.equal(off.ambiguous, 0);
  assert.equal(off.discriminating, 4);
  assert.equal(off.systematic, true);
  assert.equal(componentSwap("F4", "F3"), null);
});

test("componentSwap names one edit, and refuses anything that is not one", () => {
  assert.deepEqual(componentSwap("HSS8X8X3/8", "HSS6X6X3/8"), {
    part: "section",
    key: "8X8 read as 6X6",
  });
  assert.deepEqual(componentSwap("HSS6X6X5/8", "HSS6X6X3/8"), {
    part: "thickness",
    key: "5/8 read as 3/8",
  });
  assert.equal(componentSwap("HSS8X8X3/8", "HSS6X6X1/2"), null, "two edits are not one misread");
  assert.equal(componentSwap("HSS8X8X3/8", "HSS8X8X3/8"), null, "no edit at all");
  assert.equal(componentSwap("F12", "F10"), null, "an atomic label has no parts");
  assert.equal(
    componentSwap("HSS8X8X3/8", "HSS6X6X3/8, HSS8X8X3/8"),
    null,
    "a hedge names several labels and has no single reading to decompose",
  );
});

// --- When the set has been beaten ------------------------------------------

test("a set with no wrong answer and one case left says it is spent", () => {
  // The first crop run: 39 of 40, nothing wrong, off-target, invented or
  // hedged. One case of headroom on a forty-case set is 2.5 points against a
  // measured 43-point spread, so the set can no longer tell two runs apart.
  const rows = [
    ...Array.from({ length: 39 }, () => verdictRow("grid-footing", "F9", "correct")),
    verdictRow("grid-footing", "F7", "abstained"),
  ];
  const spent = saturation(rows);
  assert.equal(spent.left, 1);
  assert.equal(spent.points, 2.5);
  const said = verdict(rows);
  assert.match(said, /THIS SET IS SPENT/);
  // "1 of 40 case" shipped in the first run of this line. The plural belongs to
  // the DENOMINATOR — "1 of 40 cases" — and the version that reads correctly at
  // left === 1 is exactly the version that reads wrong there.
  assert.match(said, /1 of 40 cases/);
  assert.match(said, /not a regression/);
  assert.match(said, /needs a harder SET, not a better pipeline/);
});

test("one wrong answer means the set can still discriminate", () => {
  // The distinction is the MISS buckets, not the score. A run can be at 97%
  // and still be measurable, because a wrong answer is something a change can
  // fix and a reader can check.
  const rows = [
    ...Array.from({ length: 38 }, () => verdictRow("grid-footing", "F9", "correct")),
    verdictRow("grid-footing", "F7", "wrong"),
    verdictRow("grid-footing", "F7", "abstained"),
  ];
  assert.equal(saturation(rows), null);
  assert.doesNotMatch(verdict(rows), /THIS SET IS SPENT/);
});

test("a clean run with real headroom is not spent either", () => {
  // Nothing wrong, but a quarter of the set declined. There is plenty for a
  // change to move and the abstentions are exactly what it should move.
  const rows = [
    ...Array.from({ length: 30 }, () => verdictRow("grid-footing", "F9", "correct")),
    ...Array.from({ length: 10 }, () => verdictRow("grid-footing", "F7", "abstained")),
  ];
  assert.equal(saturation(rows), null);
});

test("an off-target or invented answer keeps the set alive", () => {
  for (const outcome of ["off-target", "invented", "hedged"]) {
    const rows = [
      ...Array.from({ length: 39 }, () => verdictRow("grid-footing", "F9", "correct")),
      verdictRow("grid-footing", "F7", outcome),
    ];
    assert.equal(saturation(rows), null, outcome);
  }
});

// --- The only number that is an error bar ----------------------------------

test("two ingests sharing a label measure run-to-run variance", () => {
  // The measurement this feature was built to demand. Two ingests at a
  // byte-identical, fully logged configuration scored 25% and 68% — wider than
  // every effect the vision work had claimed, all of them n=1 per arm.
  const tags = (col, foot) => ({
    "grid-column": { pct: col },
    "grid-footing": { pct: foot },
  });
  const h = runHistory([
    ran("2026-09-17T10:00Z", "pA", ["a"], 25, "minimal+4000", tags(24, 26)),
    ran("2026-09-17T11:00Z", "pB", ["b"], 68, "minimal+4000", tags(43, 95)),
  ]);
  assert.equal(h.repeats.length, 1);
  const [r] = h.repeats;
  assert.equal(r.n, 2);
  assert.equal(r.spread, 43);
  assert.equal(r.worstTag.tag, "grid-footing", "the widest tag, not the first");
  assert.equal(r.worstTag.spread, 69, "a set-wide number averages the variance away");
  const said = verdict([]);
  assert.equal(said.includes("ERROR BAR"), false, "no history, no claim");
});

test("one ingest per label is no error bar", () => {
  // A configuration run once measures nothing about its own variance, however
  // many other configurations sit beside it.
  const h = runHistory([
    ran("2026-09-17T10:00Z", "pA", ["a"], 25, "minimal+4000"),
    ran("2026-09-17T11:00Z", "pB", ["b"], 68, "minimal+20000"),
  ]);
  assert.deepEqual(h.repeats, []);
});

test("unlabelled ingests never form an error bar", () => {
  // Every run before --label existed is unlabelled, and grouping them would
  // invent the very measurement this exists to supply.
  const h = runHistory([
    ran("2026-09-17T10:00Z", "pA", ["a"], 25),
    ran("2026-09-17T11:00Z", "pB", ["b"], 68),
  ]);
  assert.deepEqual(h.repeats, []);
  assert.equal(h.ingests.length, 2);
});

test("the report leads with the error bar and says what it forbids", () => {
  const tags = (col) => ({ "grid-column": { pct: col } });
  const rows = [verdictRow("grid-column", "HSS8X8X3/8", "correct")];
  const h = runHistory([
    ran("2026-09-17T10:00Z", "pA", ["a"], 25, "minimal+4000", tags(24)),
    ran("2026-09-17T11:00Z", "pB", ["b"], 68, "minimal+4000", tags(43)),
  ]);
  const said = [];
  const real = console.log;
  console.log = (...a) => said.push(a.join(" "));
  try { report(rows, false, [], h); } finally { console.log = real; }
  const out = said.join("\n");
  assert.match(out, /ERROR BAR: "minimal\+4000" has been ingested 2 times/);
  assert.match(out, /43-point spread with NOTHING changed/);
  assert.match(out, /is not evidence of anything/);
});

test("a dimension matches however the model spaces it", () => {
  // The drafter writes 26' - 2 1/2"; a model writes 26'-2 1/2". Escaping the
  // sheet's own spaces made the two miss each other, which scores a correct
  // answer as an ABSTENTION — the outcome that reads as restraint.
  const truth = "26' - 2 1/2\"";
  for (const written of ["26' - 2 1/2\"", "26'-2 1/2\"", "26'-21/2\"", "26 ' - 2 1/2 \""]) {
    assert.ok(mentions(`the bay is ${written} wide`, truth), written);
  }
});

test("dropping the needle's spaces does not widen what it matches", () => {
  assert.equal(mentions("126' - 2 1/2\"", "26' - 2 1/2\""), false, "flank guard still holds");
  assert.equal(mentions("F90", "F9"), false);
  assert.equal(mentions("HSS8X8X3/8", "HSS8X8X3/16"), false);
});

test("a needle of nothing but whitespace matches nothing", () => {
  // An unfilled expectation must never score. Without the guard the body is
  // empty and the pattern becomes two lookarounds, which match wherever two
  // non-alphanumerics meet — so "F9, F10." matches a blank expectation at the
  // comma and every answer containing punctuation scores CORRECT. The first
  // haystack I asserted this against ("anything at all") passed either way,
  // which made the test look green while testing nothing.
  assert.equal(mentions("the marks are F9, F10.", "   "), false);
  assert.equal(mentions("the marks are F9, F10.", ""), false);
  assert.equal(mentions("the marks are F9, F10.", "\t\n"), false);
});

test("a spacing case is located by the gap it asks about", () => {
  // It is not AT an intersection, so "4/B" is the wrong shape. Reading
  // gridColumn off it printed "undefined/undefined" against every miss.
  assert.equal(caseLocation({ axis: "column line", between: ["7", "8"] }), "7-8");
  assert.equal(caseLocation({ gridColumn: "4", gridRow: "B" }), "4/B");
});

test("a derivation naming no location says so rather than inventing one", () => {
  assert.equal(caseLocation({}), "?");
  assert.equal(caseLocation(), "?");
  assert.equal(caseLocation({ gridColumn: "4" }), "?", "half a coordinate is not one");
});

test("a between that names one line is not a gap", () => {
  // ["7"] joins to "7", which reads like a grid line rather than a gap and
  // collides with nothing — so a malformed derivation would pass silently.
  assert.equal(caseLocation({ between: ["7"] }), "?");
  assert.equal(caseLocation({ between: ["7", "8", "9"] }), "?");
});

test("the location is distinct per gap, because drift compares rows by it", () => {
  assert.notEqual(caseLocation({ between: ["7", "8"] }), caseLocation({ between: ["8", "9"] }));
});

test("one chunk under the cap is the correct outcome, not a warning", () => {
  // A 604-token description is under the chunker's 800, so one chunk is what
  // the split would produce anyway. Saying "check it" there fires on every
  // healthy run, and a warning that is usually wrong stops being read.
  const said = describeCoverage(["a"], ["a"], 604);
  assert.match(said, /never going to be split/);
  assert.doesNotMatch(said, /check it/);
});

test("one chunk over the cap says the split did not run", () => {
  const said = describeCoverage(["a"], ["a"], 3200);
  assert.match(said, /did not run on this ingest/);
  assert.match(said, /3200 tokens/);
  assert.match(said, /800-token cap/);
});

test("the boundary token count counts as within the cap", () => {
  assert.match(describeCoverage(["a"], ["a"], CHUNK_MAX_TOKENS), /never going to be split/);
  assert.match(describeCoverage(["a"], ["a"], CHUNK_MAX_TOKENS + 1), /did not run/);
});

test("without a token count the old wording stands", () => {
  // A run file or a schema that cannot supply it must not get a confident
  // verdict either way.
  const said = describeCoverage(["a"], ["a"]);
  assert.match(said, /check it against the chunker's cap/);
  assert.doesNotMatch(said, /did not run/);
});

test("the one-chunk verdict never displaces the reach count", () => {
  for (const size of [604, 3200, null]) {
    assert.match(describeCoverage(["a"], ["a"], size), /Description chunks on the sheet: 1, of which 1 reached/);
  }
});

test("several chunks are judged by reach, not by size", () => {
  assert.match(describeCoverage(["a", "b"], ["a", "b"], 3200), /every piece is reachable/);
  assert.match(describeCoverage(["a", "b"], ["a"], 100), /retrieval never surfaces them/);
});

test("a short outcome fully covers a longer one on the progress line", () => {
  // \r rewinds without erasing: "correct" over "off-target" printed
  // "correctget", which reads as an outcome this scorer does not have.
  const long = progressLine(39, 40, "off-target");
  const short = progressLine(40, 40, "correct");
  assert.equal(short.length, long.length, "every line is the same width");
  assert.match(short, /correct\s+\r$/);
});

test("the progress line pads to the outcome vocabulary, not a magic number", () => {
  const width = Math.max(...OUTCOMES.map((o) => o.length));
  for (const outcome of OUTCOMES) {
    const line = progressLine(1, 40, outcome);
    assert.equal(line.length, `  [1/40] `.length + width + 1, outcome);
  }
});

test("the progress line still says where the run is", () => {
  assert.match(progressLine(7, 40, "correct"), /\[7\/40\]/);
});
