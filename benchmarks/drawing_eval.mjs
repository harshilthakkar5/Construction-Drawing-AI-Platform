#!/usr/bin/env node
/**
 * Drawing-comprehension evaluation harness (Phase 0).
 *
 * `retrieval_eval.mjs` asks whether the right chunk comes back. This asks
 * whether the ANSWER is right, on the one class of question where the two come
 * apart: geometry.
 *
 * On a text-heavy set retrieval reports 100% recall, and that is true. It is
 * also the limit of what recall can say. A sheet's text layer holds every
 * footing mark and every member size, so the chunk is always found — but it
 * holds them in two separate runs, one of sizes and one of marks, because the
 * pairing between them is drawn as a leader line rather than written as words.
 * Retrieval cannot miss a fact that is present; it also cannot supply one that
 * is not. "Which column sits on the footing at grid 7/F" is not in the text at
 * any k, and that gap is invisible to a recall number.
 *
 * So this harness exists to give the VLM work a target it can fail. Run it
 * before building anything: if the answers are already right, the pipeline does
 * not need vision and the cheapest change is no change.
 *
 * The cases come from `drawing_truth.py`, which derives each answer from the
 * PDF's own coordinates and refuses any it cannot derive unambiguously. They
 * are not written by hand and not captured from the app — a case captured from
 * the behaviour under test measures agreement with itself, and a case written
 * by hand carries whatever the writer misread. Both mistakes have already been
 * made in this repo's benchmarks; this is the correction.
 *
 * Usage
 *   node benchmarks/drawing_eval.mjs
 *   node benchmarks/drawing_eval.mjs --set benchmarks/drawing_eval_set.json
 *   node benchmarks/drawing_eval.mjs --limit 10      # a cheap smoke run
 *   node benchmarks/drawing_eval.mjs --json
 *   node benchmarks/drawing_eval.mjs --project <uuid> --label "minimal+4000"
 *
 * `--label` names the CONFIGURATION an ingest was made under, copied off
 * `vlm._report_settings` in the worker log. Two ingests sharing a label are the
 * same experiment repeated, and the spread between them is the only error bar
 * this benchmark can produce — measured at 43 points set-wide, 69 on one tag.
 *
 * Every case costs one chat completion, so the full set is a real spend. Start
 * with --limit.
 *
 * It needs the API's environment (DATABASE_URL, QDRANT_URL, the embedding key
 * and the chat provider's key) because it runs the API's own retrieval and
 * answer code rather than a copy.
 */
import { mkdirSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath, pathToFileURL } from "node:url";
import { basename, dirname, resolve } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, "..");

// Mirrors routes/chat.ts. Retrieval depth is part of what is being measured:
// scored against a different k this is a different experiment.
const RETRIEVAL_LIMIT = 18;

async function loadApi() {
  // A benchmark's calls are not a user's — without this every question lands in
  // the project's dashboard spend.
  process.env.USAGE_TRACKING = "off";

  const { register } = await import("tsx/esm/api");
  register();
  process.chdir(resolve(root, "apps/api"));
  const load = (p) => import(pathToFileURL(resolve(root, p)).href);
  try {
    const retrieval = await load("apps/api/src/retrieval.ts");
    const answer = await load("apps/api/src/answer.ts");
    const citations = await load("apps/api/src/citations.ts");
    const { prisma } = await load("apps/api/src/db.ts");
    return { retrieval, answer, citations, prisma };
  } catch (err) {
    throw new Error(
      "could not load the API's code. This harness runs the real thing, so it needs the " +
        "API's environment — copy .env to apps/api/.env, or export DATABASE_URL, QDRANT_URL, " +
        `the embedding provider's key and the chat provider's key.\n\n  Underlying error: ${err.message}`,
    );
  }
}

function parseArgs(argv) {
  const args = {
    set: resolve(here, "drawing_eval_set.json"),
    json: false,
    limit: 0,
    out: null,
    project: null,
  };
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === "--json") args.json = true;
    else if (arg === "--set") args.set = resolve(process.cwd(), argv[++i]);
    else if (arg === "--limit") args.limit = Number(argv[++i]);
    else if (arg === "--out") args.out = resolve(process.cwd(), argv[++i]);
    else if (arg === "--project") args.project = argv[++i];
    // What CONFIGURATION this ingest was made under. The harness cannot know
    // it: VLM_* is read by the worker at ingest and the chunks carry none of
    // it, so this is the one thing only the person running it can supply.
    // Copy it off `vlm._report_settings` in the worker log. Two ingests given
    // the same label are claimed to be the SAME experiment, and that claim is
    // what turns a list of scores into an error bar — so a wrong label is
    // worse than none, because it manufactures a measurement.
    else if (arg === "--label") args.label = argv[++i];
    else if (arg === "--help") args.help = true;
  }
  return args;
}

/**
 * Whether `needle` appears in `haystack` as a standalone token.
 *
 * Built as a pattern rather than a substring test because the model writes a
 * member size the way a person would and the set stores it the way the PDF
 * does: "HSS8X8X3/8" may come back as "HSS 8x8x3/8". Spaces are therefore
 * optional between every character, case is ignored, and the flanks must not be
 * alphanumeric — without that guard "F9" matches inside "F90" and a wrong
 * answer scores as right.
 */
export function mentions(haystack, needle) {
  if (!needle) return false;
  const body = [...needle]
    .map((ch) => (/[a-z0-9]/i.test(ch) ? ch : `\\${ch}`))
    .join("\\s*");
  return new RegExp(`(?<![a-z0-9])${body}(?![a-z0-9])`, "i").test(haystack);
}

/**
 * Every label of a tag's kind that appears anywhere in the set — the sheet's own
 * vocabulary of footing marks, or of member sizes, as derived by
 * drawing_truth.py. `score` needs it to tell a refusal apart from an answer that
 * named a real label from the wrong part of the drawing.
 */
export function labelVocabulary(cases) {
  const byTag = new Map();
  for (const c of cases) {
    const labels = byTag.get(c.tag) ?? new Set();
    // Every mark of this kind ON THE SHEET when the generator recorded it.
    // Without that, the vocabulary is only the labels the SET happens to ask
    // about — and a real mark at an intersection nobody asked about then
    // scores INVENTED, which is the outcome that says the model made it up.
    // Two of the three "invented" answers in the first run to produce them
    // were F6 and HSS5X5X3/8, both of which the description places at 9/C;
    // the set simply never asks about a case whose answer is either one.
    for (const label of c.sheetLabels ?? []) labels.add(label);
    if (c.expected) labels.add(c.expected);
    if (c.distractor) labels.add(c.distractor);
    byTag.set(c.tag, labels);
  }
  return new Map([...byTag].map(([tag, labels]) => [tag, [...labels]]));
}

/** Whether the vocabulary came from the SHEET or only from the case set. */
export function vocabularySource(cases) {
  return cases.some((c) => (c.sheetLabels ?? []).length) ? "sheet" : "set";
}

/** Which of `vocabulary`'s labels this answer names, in the set's spelling. */
export function namedLabels(answer, vocabulary) {
  return (vocabulary ?? []).filter((label) => mentions(answer, label));
}

const squash = (text) => text.replace(/\s+/g, "").toUpperCase();

/**
 * A label the model INVENTED: shaped like a real mark of this kind, but written
 * nowhere on the sheet.
 *
 * The vocabulary is every label the PDF actually carries, so a closed-vocabulary
 * scorer cannot see this at all — and the gap is not theoretical. One
 * description of this sheet answered nearly every column question with
 * HSS9X9X3/8. That is a real AISC square section, which is exactly why it reads
 * as plausible — and it appears nowhere on THIS sheet, whose columns are HSS8X8,
 * HSS6X6 and HSS10X10. It matched neither the truth, nor the distractor, nor
 * anything else on the drawing,
 * so all 21 cases scored ABSTAINED: a size nobody specified, reported as a
 * refusal to guess. That is the most dangerous answer this benchmark can
 * receive and it was being counted as the safest.
 *
 * `labelPattern` is emitted per case by drawing_truth.py, so the shape of a
 * footing mark is defined once and travels with the cases rather than being
 * written again here. A set generated before it existed simply has none, and
 * the report says so rather than quietly returning to the blind behaviour.
 */
export function inventedLabel(answer, testCase, vocabulary) {
  if (!testCase.labelPattern) return null;
  const re = new RegExp(`(?<![a-z0-9])(?:${testCase.labelPattern})(?![a-z0-9])`, "gi");
  const known = new Set((vocabulary ?? []).map(squash));
  for (const [token] of answer.matchAll(re)) {
    if (!known.has(squash(token))) return token;
  }
  return null;
}

/**
 * Five outcomes, not two — and the fifth is why this file has tests.
 *
 * A model that declines to answer is not a model that answers wrongly, and on
 * construction drawings the difference is the whole point: "the drawings do not
 * show this" sends someone to look at the sheet, while a confident wrong
 * footing mark gets poured. Collapsing them into one "incorrect" bucket would
 * hide the only failure that is actually dangerous, and would punish exactly
 * the behaviour FR-14 asks for.
 *
 * Knowing only the truth and its nearest neighbour is not enough to draw that
 * line. An answer naming a THIRD label — a real mark, from the wrong part of
 * the sheet — mentions neither, and the first version of this function filed
 * every one of those under "abstained", which is to say under SAFE. That is not
 * a cosmetic miscount. `wrong` requires naming the nearest neighbour
 * specifically, so the further an answer landed from the right intersection the
 * safer it scored: a run could get more dangerous and report the opposite, and
 * one did. Raising VLM_MAX_TOKENS took the column tag from 11 correct / 4 wrong
 * to 0 / 0 with all 21 cases in "abstained" — both buckets emptying at once,
 * which no amount of genuine restraint produces.
 *
 * So the tag's whole vocabulary is passed in and naming any of it counts as an
 * answer. This reads text, not intent: a refusal that lists candidates ("the
 * sheet shows F7 and F9 near there, I cannot tell which") scores off-target
 * rather than abstained. That is the trade, and it is why every run now writes
 * its answers to disk — a surprising bucket is meant to be read, not trusted.
 */
export function score(answer, testCase, vocabulary = []) {
  const hasExpected = mentions(answer, testCase.expected);
  const hasDistractor = mentions(answer, testCase.distractor);
  if (hasExpected && !hasDistractor) return "correct";
  if (hasExpected && hasDistractor) return "hedged";
  if (hasDistractor) return "wrong";
  // Neither the truth nor its neighbour. Any other mark of this kind means the
  // model did answer, and answered somewhere else entirely.
  if (namedLabels(answer, vocabulary).length) return "off-target";
  // Shaped like a mark of this kind, but on no part of this drawing.
  if (inventedLabel(answer, testCase, vocabulary)) return "invented";
  return "abstained";
}

/** The corpus must be able to answer at all before a run means anything: a set
 *  pointed at a deleted or re-uploaded project would report confident zeros. */
/**
 * Point every case at one project, for the workflow this harness kept losing
 * track of: the same PDF ingested into a FRESH project per configuration.
 * Without the override the set keeps naming whichever project it was generated
 * against, and the run returns a stale answer rather than an error.
 */
export function applyProjectOverride(cases, projectId) {
  if (!projectId) return cases;
  return cases.map((c) => ({ ...c, projectId }));
}

/**
 * The one project every case must ask.
 *
 * A set spanning two projects scores two corpora and reports one number, and
 * nothing in the output says so — some questions are answered from one ingest
 * and the rest from another. It arrives by regenerating a set against a new
 * project while some cases still carry the old id.
 */
export function oneProjectOrThrow(cases) {
  const ids = [...new Set(cases.map((c) => c.projectId))];
  if (ids.length > 1) {
    throw new Error(
      `the set names ${ids.length} different projects (${ids.join(", ")}). Every case must ask ` +
        "the same corpus, or the score mixes two of them — regenerate the set, or pass " +
        "--project <uuid> to point every case at one.",
    );
  }
  return ids[0];
}

/**
 * How much of the sheet's description retrieval can actually reach.
 *
 * `descriptionChunkIds` says which description chunks REACHED an answer. It
 * cannot say how many exist, and the difference is the whole diagnosis. A run
 * where all 40 cases retrieved a description looks like full coverage and
 * reported exactly ONE distinct chunk id — which means one of two entirely
 * different things:
 *
 *   one chunk exists   the description was stored whole, so `split_description`
 *                      did not run on this ingest (or the description was short
 *                      enough not to need it)
 *   several exist      the split worked and retrieval surfaces the SAME piece
 *                      for every question, so the rest of the sheet is written
 *                      into the corpus and never read
 *
 * They have opposite fixes, and only the second makes raising VLM_MAX_TOKENS
 * actively counter-productive: a longer description then means more of the
 * sheet sitting in pieces nothing retrieves. So count what is on the page.
 */
export function describeCoverage(onSheet, reached) {
  const total = onSheet.length;
  if (total === 0) {
    return "Description chunks on the sheet: NONE — the vision pass stored nothing for this ingest.";
  }
  const present = new Set(onSheet);
  const used = new Set([...new Set(reached)].filter((id) => present.has(id)));
  const head = `Description chunks on the sheet: ${total}, of which ${used.size} reached an answer`;
  if (total === 1) {
    return `${head}. One chunk means the description was stored whole — check it against the chunker's cap.`;
  }
  if (used.size === total) return `${head} — every piece is reachable.`;
  return (
    `${head}. The other ${total - used.size} are in the corpus and retrieval never surfaces them, ` +
    "so whatever part of the sheet they describe cannot be answered from. Raising VLM_MAX_TOKENS " +
    "writes MORE description into pieces nothing retrieves — fix the reach first."
  );
}

/** Every description chunk living on the sheets this set asks about. */
async function descriptionChunksOnSheets(prisma, cases) {
  const sheets = new Map(
    cases.map((c) => [`${c.projectId}:${c.derivation.sheet}`, {
      projectId: c.projectId,
      sheet: c.derivation.sheet,
    }]),
  );
  const ids = [];
  for (const { projectId, sheet } of sheets.values()) {
    const found = await prisma.chunk.findMany({
      where: {
        kind: "description",
        page: { sheetNumber: sheet, document: { projectId, supersededAt: null } },
      },
      select: { id: true },
    });
    ids.push(...found.map((c) => c.id));
  }
  return ids;
}

async function preflight(prisma, cases) {
  const ids = [oneProjectOrThrow(cases)];
  for (const id of ids) {
    if (!id || id === "TEST") {
      throw new Error(
        `the set has projectId "${id}" — regenerate it with drawing_truth.py --project <uuid> ` +
          "so the questions are asked against a real project",
      );
    }
    const pages = await prisma.page.count({
      where: { document: { projectId: id, supersededAt: null } },
    });
    if (pages === 0) {
      throw new Error(
        `project ${id} has no live pages. The set is pointed at a project that was deleted or ` +
          "re-uploaded under a new id — regenerate it rather than reading its zeros as a result.",
      );
    }
  }

  // Stronger than "the project exists": every question names a sheet, and a
  // sheet that is not in this project cannot be answered from it. Without this
  // a set generated from a PDF that was never uploaded — or uploaded to a
  // different project — would run to completion and report that the model
  // hallucinates, when what it actually did was correctly say it had no such
  // drawing.
  for (const { projectId, sheet } of new Map(
    cases.map((c) => [`${c.projectId}:${c.derivation.sheet}`, {
      projectId: c.projectId,
      sheet: c.derivation.sheet,
    }]),
  ).values()) {
    const found = await prisma.page.count({
      where: { sheetNumber: sheet, document: { projectId, supersededAt: null } },
    });
    if (found === 0) {
      throw new Error(
        `project ${projectId} has no page with sheetNumber "${sheet}". The set asks about a ` +
          "drawing this project does not contain, so every answer would be a correct refusal " +
          "scored as a failure. Upload the sheet, or regenerate the set from a PDF that is in " +
          "this project.",
      );
    }
    // The same sheet on several live pages means the PDF was uploaded more than
    // once rather than reindexed. Retrieval then draws on two ingests at once
    // and whichever description wins the fusion decides the answer, so the
    // score is a blend of them with no way to say which. Delete the extra
    // documents, or reindex in place — a re-upload is a new document, only
    // replacesDocumentId makes it a revision.
    if (found > 1) {
      throw new Error(
        `project ${projectId} has sheetNumber "${sheet}" on ${found} live pages — the same ` +
          "drawing is in it more than once. Retrieval would mix both ingests into one score. " +
          "Delete the duplicate documents (DELETE /projects/:id/documents/:documentId) and " +
          "re-run, or point --project at a project holding one copy.",
      );
    }
  }
}

/**
 * The majority label of one tag, and how often it is the truth.
 *
 * A sheet reuses a handful of marks, so "always answer HSS8X8X3/8" scores 52%
 * on the column tag while reading nothing at all. Without that number printed
 * beside it, a bare correctness percentage invites exactly the wrong reading.
 */
function majorityBaseline(subset) {
  if (!subset.length) return { label: null, hits: 0, pct: 0 };
  const freq = new Map();
  for (const r of subset) freq.set(r.expected, (freq.get(r.expected) ?? 0) + 1);
  const [label, hits] = [...freq].sort((a, b) => b[1] - a[1])[0];
  return { label, hits, pct: (hits / subset.length) * 100 };
}

/**
 * The null model for a set spanning several tags — which is NOT the majority
 * label pooled across all of them.
 *
 * Pooling takes the mode of every expected answer at once, so on this set it
 * proposes "answer HSS8X8X3/8 to everything", including the questions that ask
 * which FOOTING MARK is at an intersection. No guesser is that stupid: the
 * question names the vocabulary it wants, and a guesser reading only that much
 * scores each tag's own majority, summed.
 *
 * The difference is not academic. Pooled, this set's baseline is 28%; tag-aware
 * it is 43%. So a run that scored 43% — tying the real null model on BOTH tags,
 * to the case — was told it had beaten the baseline. The most encouraging line
 * on the screen was produced by this function's own arithmetic rather than by
 * the system under test, which is the one thing a benchmark may never do.
 */
function baseline(rows) {
  const tags = [...new Set(rows.map((r) => r.tag))];
  if (tags.length <= 1) {
    const one = majorityBaseline(rows);
    return { ...one, describe: one.label ? `always "${one.label}"` : "n/a" };
  }
  let hits = 0;
  for (const tag of tags) hits += majorityBaseline(rows.filter((r) => r.tag === tag)).hits;
  return {
    label: null,
    hits,
    pct: rows.length ? (hits / rows.length) * 100 : 0,
    describe: "each tag's own majority",
  };
}

/**
 * Correct answers that named something OTHER than their tag's majority label.
 *
 * The only figure in this report a frequency prior cannot produce, and the one
 * that separates two runs scoring identically. A guesser's hits are ALL on the
 * majority label, by construction; every minority hit is a label it could not
 * have reached. Two runs tied at 17/40 here, and one of them was reading.
 */
function minorityHits(rows) {
  let hits = 0;
  for (const tag of new Set(rows.map((r) => r.tag))) {
    const subset = rows.filter((r) => r.tag === tag);
    const { label } = majorityBaseline(subset);
    hits += subset.filter((r) => r.outcome === "correct" && r.expected !== label).length;
  }
  return hits;
}

/**
 * Which label a tag's ANSWERS actually reached for, and how often — against how
 * often that label is the truth.
 *
 * Minority hits measure the same instinct from the other end, but only among
 * CORRECT answers, so a tag can look clean there while its misses are all one
 * word. This run is exactly that shape: the column tag scored 52%, tying its
 * baseline to the case, and said "HSS8X8X3/8" on 15 of the 18 cases it answered
 * — 83% of its answers on a label that is the truth 52% of the time. The
 * footing tag on the same sheet named its majority mark at 33% against a 32%
 * truth rate, and scored 74%. Same description, same run: one tag reading the
 * drawing and one tag guessing the common answer, and the only number that
 * separated them had to be worked out by hand from the miss list.
 *
 * Over-naming is not the same claim as being wrong. A tag can over-name and
 * still beat its baseline, and that is worth seeing: it means the hits are
 * riding on the sheet's own frequencies rather than on what is drawn at the
 * intersection asked about.
 */
const OVER_NAMING_POINTS = 15;

function saidLabels(row) {
  return String(row.said ?? "").split(",").map((x) => x.trim()).filter(Boolean);
}

function tagConcentration(subset, tag, drifted = []) {
  const answered = subset.filter((r) => r.outcome !== "abstained");
  if (!answered.length) return null;
  const freq = new Map();
  // `said` is every label of this kind the answer named, so a hedged answer
  // counts toward both. That is the right reading: this measures what the model
  // REACHED FOR, not what it settled on.
  for (const r of answered) {
    for (const label of String(r.said ?? "").split(",").map((x) => x.trim()).filter(Boolean)) {
      freq.set(label, (freq.get(label) ?? 0) + 1);
    }
  }
  if (!freq.size) return null;
  const [label, named] = [...freq].sort((a, b) => b[1] - a[1] || String(a[0]).localeCompare(String(b[0])))[0];
  const namedPct = (named / answered.length) * 100;
  const truthPct = (subset.filter((r) => r.expected === label).length / subset.length) * 100;
  // Carried alongside, because over-naming on its own does not establish
  // guessing and this report has already printed one verdict its own evidence
  // contradicted. A tag can lean on a label AND read the sheet: the footing tag
  // reached for F8 18pt more often than the drawing offers it while 10 of its
  // 12 hits were minority marks, which a prior produces at a rate of zero.
  const correct = subset.filter((r) => r.outcome === "correct").length;
  // Minority hits alone cannot carry this defence, and one run proved it. A tag
  // that fixates on a label which is NOT its majority scores "minority hits" by
  // COINCIDENCE: it answered HSS6X6X3/8 to 17 of 21 questions, that size is the
  // truth at three of those intersections, and all three landed in the minority
  // column — a frequency prior over the WRONG frequency, credited as the one
  // thing a prior cannot fake. So the hits that count are the ones naming
  // neither the tag's majority label nor the label it is over-naming: those are
  // the answers no amount of fixation produces.
  const majority = majorityBaseline(subset).label;
  const independent = subset.filter(
    (r) => r.outcome === "correct" && r.expected !== majority && r.expected !== label,
  ).length;
  // And a tag that loses to its own baseline is not "reading the rest" in any
  // sense worth printing. 24% where guessing scores 52% is fixation whatever
  // the hits look like.
  const beatsBase = (correct / subset.length) * 100 > majorityBaseline(subset).pct;
  // And neither gate can tell a frequency prior from a label read correctly and
  // PLACED one bay off, which is the failure that replaces fixation once a
  // description gets good. The two produce the same concentration count: a tag
  // that reads row F's size and smears it up into rows B and C over-names that
  // size exactly as hard as a tag that never looked. `drift` already separates
  // them per miss — the named label's own intersection is a bay away — so this
  // asks how many of the over-named label's MISSES are that, and the verdict
  // stops calling it a guess when most of them are.
  //
  // Not a defence of the score. A tag misplacing what it reads is still wrong
  // at those intersections; it wants LOCALITY rather than a better prior, and
  // saying "it is reaching for one label" without saying which of the two is
  // happening points the next change at the wrong thing.
  const namedMisses = subset.filter(
    (r) => !["correct", "abstained"].includes(r.outcome) && saidLabels(r).includes(label),
  );
  const placed = namedMisses.filter((r) =>
    drifted.some((d) => d.tag === r.tag && d.grid === r.grid && d.named === label),
  ).length;
  const placement = namedMisses.length > 0 && placed * 2 >= namedMisses.length;
  // And a compound label has a third possibility neither gate can see: the
  // model reading one HALF of it right, every time. A member size is a section
  // and a thickness printed as one string, and a tag that carries the thickness
  // through thirteen of fourteen misses is not guessing a label — it is
  // misreading one glyph pair. Same principle as `placement`: the verdict has
  // to be the one the evidence supports.
  const components = componentMisreads(subset);
  return {
    tag,
    label,
    named,
    answered: answered.length,
    namedPct,
    truthPct,
    gap: namedPct - truthPct,
    correct,
    minorityHits: minorityHits(subset),
    independentHits: independent,
    beatsBase,
    namedMisses: namedMisses.length,
    placedMisses: placed,
    placement,
    components,
    prior:
      !placement &&
      !components?.halfRead &&
      (!beatsBase || (correct ? (independent / correct) * 100 : 0) < 25),
  };
}

/**
 * Concentration is a PER-TAG measurement, for the same reason the baseline is:
 * pooled across tags it compares a column size against footing questions that
 * could never have been answered with one, and dilutes the very number it
 * exists to expose. Across a multi-tag set this reports the worst offender and
 * names which tag it was.
 */
export function answerConcentration(rows) {
  const tags = [...new Set(rows.map((r) => r.tag))];
  // Computed ONCE over the whole set: the bay is measured from the
  // intersections the cases name, and a single tag's subset is the same grid.
  const drifted = drift(rows);
  if (tags.length <= 1) return tagConcentration(rows, tags[0] ?? null, drifted);
  const each = tags
    .map((t) => tagConcentration(rows.filter((r) => r.tag === t), t, drifted))
    .filter(Boolean);
  if (!each.length) return null;
  // Prior-shaped tags first, THEN widest gap: a tag whose hits are mostly
  // minority labels is reading the sheet and leaning on a label, and naming it
  // as the set's worst offender would bury the tag that is only guessing.
  return each.sort((a, b) => Number(b.prior) - Number(a.prior) || b.gap - a.gap)[0];
}

/**
 * Misses that named the RIGHT label at the WRONG intersection.
 *
 * The six-way outcome cannot express this. "F10 at 3/B" scores off-target —
 * "named some other label of that kind" — which reads as a model that could not
 * read the mark. But F10 IS the truth at 4/B, 148pt away, one grid bay across,
 * and six of eight footing misses in one run were exactly that. The mark was
 * read correctly and placed one bay off.
 *
 * The distinction decides the next move and the two are opposite. A misread
 * glyph wants resolution. A correctly-read label at the wrong intersection
 * wants LOCALITY — the grid bubbles are at the sheet's edge and the
 * intersections are in the middle, so a higher-resolution whole-sheet image
 * makes each tile cover less of the drawing and the association harder, not
 * easier. Raising DPI is then the wrong lever twice over.
 *
 * Reported rather than scored: adding a seventh outcome would silently rebase
 * every tally in this file's history against runs that never measured it.
 */
function bayLength(rows) {
  // The sheet's own spacing, from the cases: the shortest gap between two
  // distinct intersections is one bay. No constant can be right across sheets.
  const points = rows.map((r) => r.point).filter((p) => Array.isArray(p) && p.length === 2);
  let shortest = Infinity;
  for (let i = 0; i < points.length; i += 1) {
    for (let j = i + 1; j < points.length; j += 1) {
      const gap = Math.hypot(points[i][0] - points[j][0], points[i][1] - points[j][1]);
      if (gap > 1 && gap < shortest) shortest = gap;
    }
  }
  return Number.isFinite(shortest) ? shortest : null;
}

export function drift(rows) {
  const bay = bayLength(rows);
  if (!bay) return [];
  const found = [];
  for (const row of rows) {
    if (!["wrong", "off-target"].includes(row.outcome) || !Array.isArray(row.point)) continue;
    const named = String(row.said ?? "").split(",").map((x) => x.trim()).filter(Boolean);
    let best = null;
    for (const home of rows) {
      if (home.tag !== row.tag || !Array.isArray(home.point)) continue;
      if (!named.includes(home.expected) || home.grid === row.grid) continue;
      const away = Math.hypot(row.point[0] - home.point[0], row.point[1] - home.point[1]);
      // NOT `grid` and `label`: spreading those over the row would overwrite
      // the row's OWN grid, and the annotation would then be printed against
      // whichever case the drifted label belongs to rather than the one that
      // named it.
      if (!best || away < best.away) best = { truthAt: home.grid, named: home.expected, away };
    }
    // Within 1.5 bays is "next door"; further away it is a label from
    // elsewhere on the sheet, which off-target already says.
    if (best && best.away <= bay * 1.5) found.push({ ...row, ...best, bay });
  }
  return found;
}

/**
 * Whether a tag's drifted misses all point the SAME WAY.
 *
 * `drift` says each miss named a label that lives one bay off, and annotates
 * them one at a time. That reads as N independent slips. It is not always: on
 * the run that forced this, SEVEN of the footing tag's ten drifted misses were
 * the identical offset — one column line to the right, same row line — with the
 * rest split between two row shifts. Column 4 answered with column 3's footing,
 * 4.6 with 4's, 6 with 4.6's, 7 with 6's, 9 with 8's, every one in the same
 * direction and every one exactly one grid step.
 *
 * That is ONE error repeated, not ten. The model named the grid correctly and
 * then walked its values along it off by one — an ENUMERATION failure, which is
 * a different thing from reading a label wrong and a different thing again from
 * placing a correctly-read label at a random neighbour. It is also the failure
 * a crop removes completely, because a crop is handed its coordinate rather
 * than counting its way to one.
 *
 * Offsets are measured in GRID INDEX space, not points: "one column line over"
 * is the claim, and the bays on this sheet run 130 to 218pt, so a distance
 * cannot say it. The order comes from the cases' own coordinates.
 *
 * And then it collided with the measure written directly above it, on the very
 * next run. Three of the column tag's six drifted misses shared one offset —
 * row C answered with row F's size — and the report duly called it an
 * enumeration error. But row C's columns are HSS8X8 where row F's are HSS6X6
 * at the same thickness, so "took the value one row down" and "read 8X8 as
 * 6X6" predict the IDENTICAL string. Both gates fired on the same three cases
 * and told opposite stories about them, two lines apart, each presented as
 * independent evidence.
 *
 * The tie-break is the misses the other hypothesis cannot reach. That same
 * substitution appeared on four more misses with NO neighbour holding what was
 * said, where displacement is not available as an explanation; no drifted miss
 * needed an offset that a substitution could not produce. So the misread has
 * evidence of its own and the offset has none, and a miss that agrees with
 * both must not be counted as if it agreed with one.
 *
 * `componentSwap` decides it per miss. Only misses it CANNOT explain vote on
 * an offset, which is why this is not symmetric: an atomic label — a footing
 * mark — has no substitution to be confused with, so the seven-of-ten footing
 * offset that this function was written for stands exactly as it did.
 */
export function systematicOffset(rows) {
  const drifted = drift(rows);
  if (drifted.length < 3) return null;
  const axis = (index) => {
    const at = new Map();
    for (const row of rows) {
      if (!Array.isArray(row.point) || typeof row.grid !== "string") continue;
      const label = row.grid.split("/")[index];
      if (label !== undefined) at.set(label, row.point[index]);
    }
    return [...at].sort((a, b) => a[1] - b[1]).map(([label]) => label);
  };
  const columns = axis(0);
  const rowLines = axis(1);
  const steps = [];
  for (const miss of drifted) {
    const from = String(miss.grid ?? "").split("/");
    const to = String(miss.truthAt ?? "").split("/");
    const dc = columns.indexOf(to[0]) - columns.indexOf(from[0]);
    const dr = rowLines.indexOf(to[1]) - rowLines.indexOf(from[1]);
    if (![dc, dr].every(Number.isFinite) || (!dc && !dr)) continue;
    // An index of -1 means a label this set does not place; it cannot be an
    // offset from anywhere.
    if ([to[0], to[1], from[0], from[1]].some((l, i) => (i % 2 ? rowLines : columns).indexOf(l) < 0)) {
      continue;
    }
    // Does a one-component misread of this case's OWN truth produce the same
    // string? Then this miss is evidence for neither hypothesis on its own.
    steps.push({ key: `${dc},${dr}`, swap: componentSwap(miss.expected, miss.said) });
  }
  if (!steps.length) return null;
  const total = steps.length;
  const ambiguous = steps.filter((s) => s.swap).length;

  // What the misread hypothesis has that the offset hypothesis does not: the
  // same substitution on a miss that drift could NOT explain. Those cases are
  // the whole tie-break, so they are counted over the tag's misses rather than
  // over the drifted subset.
  const driftedGrids = new Set(drifted.map((d) => d.grid));
  const elsewhere = new Map();
  for (const row of rows) {
    if (!["wrong", "off-target", "invented"].includes(row.outcome)) continue;
    if (driftedGrids.has(row.grid)) continue;
    const swap = componentSwap(row.expected, row.said);
    if (swap) elsewhere.set(swap.key, (elsewhere.get(swap.key) ?? 0) + 1);
  }
  const swapped = new Map();
  for (const s of steps) {
    if (s.swap) swapped.set(s.swap.key, (swapped.get(s.swap.key) ?? 0) + 1);
  }
  // Ranked by the evidence the OFFSET cannot reach, then by how much of the
  // drift it accounts for. `elsewhere: 0` is still reported: there neither
  // hypothesis has a miss the other cannot explain, which is a weaker finding
  // than a misread but a worse one than a confident offset claim.
  const confound = [...swapped]
    .map(([key, n]) => ({ key, n, elsewhere: elsewhere.get(key) ?? 0 }))
    .sort((a, b) => b.elsewhere - a.elsewhere || b.n - a.n)[0] ?? null;

  // Only the misses a component misread CANNOT account for may vote on an
  // offset. An ambiguous one agrees with whichever story it is asked about.
  const step = new Map();
  for (const s of steps) {
    if (s.swap) continue;
    step.set(s.key, (step.get(s.key) ?? 0) + 1);
  }
  const ranked = [...step].sort((a, b) => b[1] - a[1])[0];
  const [key, count] = ranked ?? [null, 0];
  const [dc, dr] = key ? key.split(",").map(Number) : [0, 0];
  const discriminating = [...step.values()].reduce((a, b) => a + b, 0);
  return {
    drifted: total,
    ambiguous,
    discriminating,
    // The substitution that explains the ambiguous misses AND appears where no
    // offset could have produced it. Null means nothing is confounded.
    confound,
    count,
    columns: dc,
    rows: dr,
    // Half the DISCRIMINATING misses sharing one offset is no longer a
    // coincidence of a small sheet; it is the same mistake made repeatedly.
    systematic: count >= 3 && count * 2 >= discriminating,
    describe: !key
      ? ""
      : [
          dc ? `${Math.abs(dc)} column line${Math.abs(dc) > 1 ? "s" : ""} ${dc > 0 ? "over" : "back"}` : "",
          dr ? `${Math.abs(dr)} row line${Math.abs(dr) > 1 ? "s" : ""} ${dr > 0 ? "down" : "up"}` : "",
        ]
          .filter(Boolean)
          .join(" and "),
  };
}

/**
 * Whether this set has stopped being able to tell two runs apart.
 *
 * A benchmark that has been beaten has to SAY so, for the same reason every
 * other gate in this report exists: the encouraging number must be the one the
 * evidence supports. When no answer is wrong, off-target, invented or hedged,
 * everything left is an abstention, and the headroom is (cases left / cases)
 * — on a 40-case set one case is 2.5 points. The run-to-run spread measured on
 * this very set at a FIXED configuration was 43 points. A future change cannot
 * demonstrate anything through a gap that small, in either direction: a run
 * scoring 3 points lower is not a regression and one scoring 2 higher is not an
 * improvement.
 *
 * That is not a complaint about the score. It is the point at which the next
 * measurement needs a harder SET rather than a better pipeline, and the thing
 * this report must not do is let someone keep quoting a number it can no longer
 * earn. Reported, never scored — like drift, the offset and the citation check.
 */
export function saturation(rows) {
  if (!rows.length) return null;
  const missed = rows.filter((r) =>
    ["wrong", "off-target", "invented", "hedged"].includes(r.outcome),
  ).length;
  if (missed) return null;
  const left = rows.filter((r) => r.outcome !== "correct").length;
  const points = (left / rows.length) * 100;
  // Four cases on a forty-case set. Above that there is still something a
  // change could move that a reader could believe.
  return points <= 10 ? { left, points, cases: rows.length } : null;
}

export function tally(subset) {
  const n = subset.length || 1;
  const count = (k) => subset.filter((r) => r.outcome === k).length;
  const base = baseline(subset);
  const correct = count("correct");
  return {
    n: subset.length,
    correct,
    wrong: count("wrong"),
    offTarget: count("off-target"),
    invented: count("invented"),
    hedged: count("hedged"),
    abstained: count("abstained"),
    pct: (correct / n) * 100,
    base,
    beatsBase: (correct / n) * 100 > base.pct,
    // The baseline ANSWERS EVERY CASE. A run that declines cannot beat it on
    // raw accuracy however well it reads the ones it does answer — it has
    // forfeited the rest — so a bare "NOT BEATEN" against an abstaining run
    // compares two different things. Coverage says how many it was willing to
    // answer; selective accuracy says how it did on those. Random abstention
    // leaves selective accuracy at the raw rate; abstaining where it is unsure
    // raises it. That gap is the claim, and it needs both numbers to be read.
    answered: subset.length - count("abstained"),
    coverage: ((subset.length - count("abstained")) / n) * 100,
    selective: subset.length - count("abstained")
      ? (correct / (subset.length - count("abstained"))) * 100
      : 0,
    minorityHits: minorityHits(subset),
    concentration: answerConcentration(subset),
    // How many of these cases had a vision description in the prompt at all.
    // Measured, not inferred from env: the VLM_* variables are read by the
    // WORKER at ingest, so this process's environment says nothing about what
    // is actually in the chunks being scored.
    withDescription: subset.filter((r) => r.descriptionChunks > 0).length,
  };
}

/**
 * What this set has scored BEFORE, read off the run files every run already
 * writes. It exists because a single run was being read as a result.
 *
 * Two ingests of the same sheet, on code whose description path was
 * byte-identical between them, scored 63% and 80%. Either a setting moved or
 * that is the spread of asking one model twice — and nothing on disk could
 * say which, because `VLM_*` is read by the WORKER at ingest and the chunks
 * carry none of it. Every comparison recorded against this benchmark is n=1,
 * and n=1 cannot distinguish a 17-point improvement from a 17-point spread.
 *
 * Two groupings, because they answer different questions:
 *
 *  - Runs sharing the same DESCRIPTION CHUNK IDS asked the same corpus. With
 *    `temperature: 0` those must score identically; a disagreement means the
 *    SCORER or the chat path changed, which is worth knowing loudly and is
 *    invisible any other way.
 *  - Runs with different ids are separate INGESTS. Their spread is the error
 *    bar on every claim made by comparing two runs.
 *
 * It reports the spread and refuses to interpret it: the harness cannot see
 * the vision settings, so it says what varied and leaves the cause to whoever
 * knows what they changed.
 */
// How many recent ingests the history line shows. The full list runs back to
// runs with no vision pass at all, which are a different system rather than a
// different setting; the tail is the part anyone is comparing against.
const RECENT_INGESTS = 5;

export function runHistory(records) {
  const byCorpus = new Map();
  for (const r of records) {
    const ids = [...(r.descriptionChunkIds ?? [])].sort();
    // A run with NO descriptions has no corpus identity, so it is its own
    // sample. Keying it on the empty string collapsed every pre-vision run
    // into one group and reported them as ONE corpus that had scored four
    // different numbers — a fabricated alarm about a corpus that does not
    // exist, from the feature added to stop a number being over-read. A
    // benchmark may report bad news; it may never invent it.
    const key = ids.length ? ids.join(",") : `no-descriptions:${r.ranAt}`;
    if (!byCorpus.has(key)) byCorpus.set(key, []);
    byCorpus.get(key).push(r);
  }
  const ingests = [...byCorpus.entries()]
    .map(([key, runs]) => ({
      key,
      runs,
      described: !key.startsWith("no-descriptions:"),
      pct: runs[runs.length - 1].pct,
      ranAt: runs[runs.length - 1].ranAt,
      projectId: runs[runs.length - 1].projectId ?? "unknown",
      label: runs[runs.length - 1].label ?? null,
      byTag: runs[runs.length - 1].byTag ?? null,
    }))
    .sort((a, b) => String(a.ranAt).localeCompare(String(b.ranAt)));
  // Same chunks in, different score out. temperature: 0 says this cannot
  // happen from the model, so it is the harness that moved. Only ever claimed
  // of runs that name the SAME described corpus.
  const rescored = [...byCorpus.entries()]
    .filter(([key]) => !key.startsWith("no-descriptions:"))
    .map(([, runs]) => runs)
    .filter((runs) => runs.length > 1 && new Set(runs.map((r) => r.pct)).size > 1)
    .map((runs) => ({
      projectId: runs[0].projectId ?? "unknown",
      scores: [...new Set(runs.map((r) => r.pct))].sort((a, b) => a - b),
    }));
  // The measurement this whole feature was built to demand, and the first one
  // that is an ERROR BAR rather than a history. Two ingests carrying the same
  // --label are the same experiment repeated, so the spread between them is
  // run-to-run variance with nothing else moving.
  //
  // It arrived and it was brutal. Two ingests at a byte-identical, fully
  // logged configuration — VLM_MAX_TOKENS=4000, GEMINI_THINKING_LEVEL=minimal,
  // GEMINI_MEDIA_RESOLUTION=ultra_high, 3072 — scored 25% and 68%. The footing
  // tag inside them ran 26% and 95%. That is wider than every effect this
  // repo's vision work has claimed to measure, all of which were n=1 per arm.
  const labelled = new Map();
  for (const i of ingests) {
    if (!i.label) continue;
    if (!labelled.has(i.label)) labelled.set(i.label, []);
    labelled.get(i.label).push(i);
  }
  const repeats = [...labelled]
    .filter(([, runs]) => runs.length > 1)
    .map(([label, runs]) => {
      const scores = runs.map((r) => r.pct);
      const tags = new Set(runs.flatMap((r) => Object.keys(r.byTag ?? {})));
      const perTag = [...tags]
        .map((tag) => {
          const at = runs.map((r) => r.byTag?.[tag]?.pct).filter((x) => typeof x === "number");
          return at.length > 1
            ? { tag, spread: Math.max(...at) - Math.min(...at), scores: at }
            : null;
        })
        .filter(Boolean)
        .sort((a, b) => b.spread - a.spread);
      return {
        label,
        n: runs.length,
        scores,
        spread: Math.max(...scores) - Math.min(...scores),
        // The widest tag, because a set-wide number averages the variance away:
        // 43 points overall hid 69 on the tag underneath it.
        worstTag: perTag[0] ?? null,
      };
    })
    .sort((a, b) => b.spread - a.spread);

  const recent = ingests.slice(-RECENT_INGESTS);
  const scores = recent.map((i) => i.pct);
  return {
    runs: records.length,
    ingests,
    recent,
    rescored,
    repeats,
    range: scores.length > 1 ? Math.max(...scores) - Math.min(...scores) : 0,
  };
}

/**
 * Load the run files this set has produced. Best-effort by design: a missing
 * directory, an unreadable file or one written by an older shape is skipped
 * rather than failing the run — a benchmark that cannot read its own history
 * should still report the run in front of it.
 *
 * Matched on the SET, not the project: repointing the same questions at a
 * fresh project per configuration is exactly the workflow this measures.
 */
export function readRunHistory(dir, set) {
  let names;
  try {
    names = readdirSync(dir).filter((n) => n.endsWith(".json"));
  } catch {
    return null;
  }
  const records = [];
  for (const name of names) {
    try {
      const raw = JSON.parse(readFileSync(resolve(dir, name), "utf8"));
      const pct = raw?.summary?.all?.pct;
      if (typeof pct !== "number" || basename(raw.set ?? "") !== basename(set ?? "")) continue;
      records.push({
        ranAt: raw.ranAt ?? name,
        projectId: raw.projectId,
        descriptionChunkIds: raw.descriptionChunkIds ?? [],
        label: raw.label ?? null,
        byTag: raw.summary?.byTag ?? null,
        pct,
      });
    } catch {
      // An unreadable run file is one lost data point, not a failed run.
    }
  }
  return records.length ? runHistory(records) : null;
}

/**
 * Whether the chunks an answer CITED can account for the label it gave.
 *
 * FR-13 is this project's central promise — every statement traceable to a
 * chunk, a page and a bbox, verifiable in one click — and nothing measured it.
 * The scorer grades WHAT was answered and never whether the citation supports
 * it, so a correct answer hung on an unrelated chunk scores full marks and a
 * reader who clicks it finds nothing.
 *
 * It is not hypothetical. In the run that first showed it, several answers read
 * "Per the Column Footing Schedule on S-100.0, the footing mark at the
 * intersection of column line 4 and row line B is F10" and cited only the
 * schedule chunk. A footing schedule maps marks to sizes and reinforcing; the
 * thing being claimed is a POSITION, which is exactly the fact the vision pass
 * exists to supply because the text layer does not hold it.
 *
 * Deliberately a weak test, stated as what it is: it asks only whether the
 * label appears ANYWHERE in the text of a cited chunk. A chunk that merely
 * lists F10 passes, so this cannot prove a citation supports its claim — it
 * can only catch the citation that could not possibly support it. Reported,
 * never scored, for the same reason drift is: a new outcome would rebase every
 * tally in this file's history against runs that never measured it.
 */
/**
 * Split a label into the parts a drafter would read separately.
 *
 * A footing mark is atomic: "F9" is one token and either read or not. A member
 * size is NOT — `HSS8X8X3/8` is a SECTION (8x8) and a WALL THICKNESS (3/8),
 * printed as one string and read as two facts. Everything in this report that
 * counts labels treats them as atoms, and on a compound label that is the
 * difference between "guessed" and "read half of it".
 *
 * Returns null for anything that does not decompose, so a caller gets a plain
 * "cannot tell" rather than a fabricated split.
 */
const MEMBER_PARTS = /^HSS\s*(\d+(?:\.\d+)?)\s*X\s*(\d+(?:\.\d+)?)\s*X\s*(\d+\s*\/\s*\d+)$/i;

export function labelComponents(label) {
  const found = MEMBER_PARTS.exec(String(label ?? "").trim());
  if (!found) return null;
  const clean = (x) => x.replace(/\s+/g, "");
  return [
    { name: "section", value: `${clean(found[1])}X${clean(found[2])}`.toUpperCase() },
    { name: "thickness", value: clean(found[3]) },
  ];
}

/**
 * The one-component substitution that turns one label into another, or null.
 *
 * `HSS8X8X3/8` against `HSS6X6X3/8` is a SECTION swap with the thickness held.
 * `HSS8X8X3/8` against `F9` is not a substitution at all, and neither is a
 * label differing in both parts — those are two errors, not one, and the point
 * of this is to name the single edit a misread would have to make.
 *
 * It exists for the tie-break in `systematicOffset`, and it returns null for an
 * ATOMIC label on purpose: a footing mark has no parts, so `F10` for `F12` can
 * only ever be a misread of the whole thing or a label taken from somewhere
 * else, never half of one. That asymmetry is why the footing tag can still
 * prove an enumeration error and the column tag, on this sheet, cannot.
 */
export function componentSwap(expected, said) {
  const named = String(said ?? "").split(",").map((x) => x.trim()).filter(Boolean);
  if (named.length !== 1) return null;
  const want = labelComponents(expected);
  const got = labelComponents(named[0]);
  if (!want || !got || got.length !== want.length) return null;
  const differ = want.map((c, i) => c.value !== got[i].value);
  if (differ.filter(Boolean).length !== 1) return null;
  const i = differ.indexOf(true);
  return { part: want[i].name, key: `${want[i].value} read as ${got[i].value}` };
}

/**
 * Which PART of a compound label a tag is getting wrong.
 *
 * The run that forced this: the column tag scored 33% against a 52% baseline
 * and the report called it "reaching for one label far more often than the
 * drawing offers it… right by coincidence, while reading nothing". Of its 14
 * misses, THIRTEEN carried the thickness exactly — 3/8, 5/8 and 1/2 each landed
 * where the drawing puts them — and got only the section wrong, always 8X8 read
 * as 6X6 and never once the reverse.
 *
 * A model reading nothing does not place three different thicknesses correctly
 * thirteen times. That is one glyph pair misread, in one direction, at every
 * intersection on the sheet — a different failure from a frequency prior and
 * with a different fix: 8 against 6 at 73 DPI is a resolution problem, and it
 * is the kind a crop at roughly 990 makes go away.
 *
 * This is the fourth time a measure in this report has treated a compound thing
 * as an atom and read a specific failure as a guess (the pooled baseline, the
 * minority-hit defence, the placement gate). Reported, never scored.
 */
export function componentMisreads(subset) {
  const pairs = [];
  for (const row of subset) {
    if (["correct", "abstained"].includes(row.outcome)) continue;
    const want = labelComponents(row.expected);
    const said = String(row.said ?? "").split(",").map((x) => x.trim()).filter(Boolean);
    // A hedged answer names several labels and has no single reading to
    // decompose, so it contributes nothing rather than an arbitrary pick.
    if (!want || said.length !== 1) continue;
    const got = labelComponents(said[0]);
    if (!got || got.length !== want.length) continue;
    pairs.push({ want, got });
  }
  if (!pairs.length) return null;
  const names = pairs[0].want.map((c) => c.name);
  const right = names.map(
    (_, i) => pairs.filter((p) => p.want[i].value === p.got[i].value).length,
  );
  // The interesting shape is exactly one part wrong, the same part every time.
  const wrongIndex = right.indexOf(Math.min(...right));
  const swaps = new Map();
  for (const p of pairs) {
    if (p.want[wrongIndex].value === p.got[wrongIndex].value) continue;
    const key = `${p.want[wrongIndex].value} read as ${p.got[wrongIndex].value}`;
    swaps.set(key, (swaps.get(key) ?? 0) + 1);
  }
  const ranked = [...swaps].sort((a, b) => b[1] - a[1]);
  const heldIndex = 1 - wrongIndex;
  const held = right[heldIndex] ?? 0;
  // The held part must VARY, and this is the condition that makes the whole
  // measure worth anything. A tag answering one constant string holds whichever
  // component the truth happens to share with it — answer HSS6X6X3/8 to
  // everything on a sheet whose columns are mostly X3/8 and the thickness
  // "matches" every time, from a model that never looked. Reading shows up as
  // the held part TRACKING the drawing: three different thicknesses, each
  // landing where the sheet puts it. One distinct value is an artifact of the
  // truth distribution; several is the claim.
  const heldValues = new Set(
    pairs
      .filter((p) => p.want[heldIndex]?.value === p.got[heldIndex]?.value)
      .map((p) => p.got[heldIndex]?.value),
  );
  return {
    misses: pairs.length,
    names,
    right,
    wrongPart: names[wrongIndex],
    // The part that is nearly always RIGHT is what makes this a read rather
    // than a guess, so it is carried explicitly rather than left to arithmetic.
    heldPart: names[heldIndex] ?? null,
    held,
    heldDistinct: heldValues.size,
    swaps: ranked,
    // Two thirds rather than a bare majority — this has to be a pattern, not a
    // lean — AND more than one value held, or a constant answer would qualify.
    halfRead: held >= pairs.length * (2 / 3) && heldValues.size > 1,
  };
}

export function citationSupport(said, citedIds, textById) {
  const labels = String(said ?? "").split(",").map((x) => x.trim()).filter(Boolean);
  if (!labels.length) return "no-label";
  if (!citedIds.length) return "uncited";
  const bodies = citedIds.map((id) => textById.get(id) ?? "");
  return labels.every((label) => bodies.some((body) => mentions(body, label)))
    ? "supported"
    : "unsupported";
}

export function summarize(rows) {
  const tags = [...new Set(rows.map((r) => r.tag))].sort();
  return {
    all: tally(rows),
    byTag: Object.fromEntries(tags.map((t) => [t, tally(rows.filter((r) => r.tag === t))])),
  };
}

export function report(rows, json, onSheet, history = null) {
  if (json) {
    console.log(JSON.stringify({ summary: summarize(rows), cases: rows }, null, 2));
    return;
  }

  const describeConcentration = (c, label) =>
    `named "${c.label}" on ${c.named} of ${c.answered}` +
    (label === c.tag ? "" : ` ${c.tag}`) +
    ` answers (${c.namedPct.toFixed(0)}%) where it is the truth on ${c.truthPct.toFixed(0)}%` +
    (c.gap < OVER_NAMING_POINTS
      ? " — in step with the sheet"
      : c.prior
        ? ` — ${c.gap.toFixed(0)}pt of over-naming, which is the shape of a guess`
        : c.components?.halfRead
          ? ` — ${c.gap.toFixed(0)}pt of over-naming, but ${c.components.held} of its ` +
            `${c.components.misses} misses carry the right ${c.components.heldPart}: ` +
            `one ${c.components.wrongPart} misread, not a label guessed at`
          : c.placement
          ? ` — ${c.gap.toFixed(0)}pt of over-naming, but ${c.placedMisses} of the ` +
            `${c.namedMisses} misses naming it are that label's own intersection a bay away: ` +
            "placement, not a prior"
          : ` — ${c.gap.toFixed(0)}pt of over-naming, but ${c.independentHits} of its ${c.correct} ` +
            "hits name neither that label nor the tag's majority, which fixation does not produce");

  const block = (label, t) =>
    `  ${label.padEnd(14)} ${String(t.n).padStart(3)}   ` +
    `correct ${String(t.correct).padStart(3)} (${t.pct.toFixed(0).padStart(3)}%)   ` +
    `wrong ${String(t.wrong).padStart(2)}   off-target ${String(t.offTarget).padStart(2)}   ` +
    `invented ${String(t.invented).padStart(2)}   ` +
    `hedged ${String(t.hedged).padStart(2)}   abstained ${String(t.abstained).padStart(3)}\n` +
    `  ${"".padEnd(14)}     baseline ${t.base.describe} ${t.base.pct.toFixed(0)}%` +
    ` ${t.beatsBase ? "beaten" : "NOT BEATEN"}` +
    `   |   minority-label hits ${t.minorityHits}/${t.correct}` +
    `   |   description in prompt ${t.withDescription}/${t.n}` +
    (t.abstained
      ? `\n  ${"".padEnd(14)}     answered ${t.answered}/${t.n}` +
        ` (${t.coverage.toFixed(0)}% coverage), and of those ${t.selective.toFixed(0)}% correct` +
        ` — the baseline answers all ${t.n}`
      : "") +
    (t.concentration ? `\n  ${"".padEnd(14)}     ${describeConcentration(t.concentration, label)}` : "");

  const tags = [...new Set(rows.map((r) => r.tag))].sort();
  console.log("\n  Drawing comprehension\n");
  console.log(block("ALL", tally(rows)));
  for (const tag of tags) {
    console.log("");
    console.log(block(tag, tally(rows.filter((r) => r.tag === tag))));
  }

  // "wrong" and "off-target" are both answers, and both get poured. They are
  // reported together and separated only by how far the miss landed.
  const missed = rows.filter((r) => ["wrong", "off-target", "invented"].includes(r.outcome));
  if (missed.length) {
    console.log("\n  Answered, but not with the truth:");
    const drifted = new Map(drift(rows).map((d) => [`${d.tag}|${d.grid}`, d]));
    for (const r of missed) {
      const nearby = drifted.get(`${r.tag}|${r.grid}`);
      console.log(
        `    ${r.grid.padEnd(8)} ${r.tag.padEnd(14)} expected ${String(r.expected).padEnd(13)}` +
          ` said ${r.said || "(no label)"}` +
          (r.outcome === "correct" ? "" : `   [${r.outcome}]`) +
          (nearby
            ? `   <- ${nearby.named} is the truth at ${nearby.truthAt}, ${Math.round(nearby.away)}pt away`
            : ""),
      );
    }
  }

  // Said once, per tag, because one annotated row reads as a coincidence and
  // six of eight reads as a mechanism.
  for (const tag of tags) {
    const tagRows = rows.filter((r) => r.tag === tag);
    const near = drift(tagRows);
    const missed = tagRows.filter((r) => ["wrong", "off-target"].includes(r.outcome)).length;
    if (near.length < 2 || !missed) continue;
    const distances = near.map((d) => Math.round(d.away)).sort((a, b) => a - b);
    console.log(
      `\n  ${near.length} of ${missed} ${tag} misses name the truth at an ADJACENT intersection ` +
        `(${distances[0]}-${distances[distances.length - 1]}pt, one bay is ` +
        `${Math.round(near[0].bay)}pt). Those labels were read correctly and placed wrong, which ` +
        "is not a reading failure: more pixels do not fix it, and on a whole-sheet image they " +
        "make it worse, because the grid bubbles are at the edge and the intersections are in " +
        "the middle. Locality is the lever — a crop carrying its own grid lines.",
    );
    // And whether those misses are N slips or ONE mistake made N times. The
    // line above cannot tell the difference, and they are not the same finding.
    const offset = systematicOffset(tagRows);
    if (offset?.systematic) {
      console.log(
        `    ${offset.count} of those ${offset.drifted} are the SAME offset — ${offset.describe}. ` +
          "That is not drift in different directions, it is the grid ENUMERATED off by one and " +
          "then read correctly along it: one mistake made " +
          `${offset.count} times rather than ${offset.count} mistakes. A crop cannot make it, ` +
          "because a crop is handed its coordinate instead of counting its way to one.",
      );
    } else if (offset?.confound && offset.ambiguous >= 3) {
      // The gate that refuses the claim has to SAY so, or the reader is left
      // with the drift line above and no idea an offset was considered and
      // could not be separated from the misread reported two lines down.
      const { key, elsewhere } = offset.confound;
      console.log(
        `    No offset claim: ${offset.ambiguous} of those ${offset.drifted} name a label that ` +
          `"${key}" produces from this case's OWN truth, so displacement and a component ` +
          "misread predict the same string and the miss cannot tell them apart. " +
          (elsewhere
            ? `That substitution also appears on ${elsewhere} miss${elsewhere > 1 ? "es" : ""} ` +
              "where no neighbour holds what was said, which the offset cannot explain at all — " +
              "so the reading failure has evidence of its own and the enumeration failure has " +
              "none here. Fix the glyph before blaming the count."
            : "Neither has a miss the other cannot account for, so on this sheet's label " +
              "distribution the two are unfalsifiable and no mechanism may be claimed."),
      );
    }
  }

  // Without it, a mark the model made up is indistinguishable from a refusal,
  // and the report would be reading one as the other in silence.
  // The same class of blind spot one level up: the SHAPE tells an invented
  // label from a refusal, and the VOCABULARY tells it from a real mark
  // elsewhere on the drawing. Missing either one inflates "invented".
  if (rows.length && rows.every((r) => !(r.sheetLabels ?? []).length)) {
    console.log(
      "\n  No case carries sheetLabels, so the label vocabulary is only what this SET asks " +
        "about — a real mark at an intersection the set skips scores INVENTED rather than " +
        "off-target. Regenerate with drawing_truth.py to read the whole sheet's labels.",
    );
  }

  const blind = rows.filter((r) => !r.labelPattern).length;
  if (blind) {
    console.log(
      `\n  ${blind}/${rows.length} cases carry no labelPattern, so a label the model INVENTED ` +
        "cannot be told apart from a refusal — both land in \"abstained\". Regenerate the set " +
        "with drawing_truth.py to score them.",
    );
  }

  const overall = tally(rows);
  const beaten = tags.filter((t) => tally(rows.filter((r) => r.tag === t)).beatsBase);
  if (!beaten.length) {
    // The verdict has to survive its own evidence. "The correct answers are a
    // frequency prior" is a claim about WHICH labels were named, and the
    // minority-hit column measures exactly that — so asserting it over a run
    // whose hits are mostly minority labels is the report contradicting
    // itself, which is the same failure as the pooled baseline above: the most
    // decisive line on the screen produced by arithmetic rather than by the
    // system under test. Say only what the numbers support.
    // A pure frequency prior produces minority hits at a rate of ZERO, by
    // construction — every one of its hits is the majority label. So the
    // fraction of correct answers that are minority hits is the measurement
    // that decides this verdict, and a quarter is already far outside what
    // guessing explains.
    const minorityPct = overall.correct ? (overall.minorityHits / overall.correct) * 100 : 0;
    const prior = minorityPct < 25;
    console.log(
      `\n  Below baseline. Guessing ${overall.base.describe}, without opening a drawing, scores ` +
        `${overall.base.pct.toFixed(0)}% on this set — it answers every case — and no tag here ` +
        `beat that on raw accuracy.` +
        (prior
          ? " Read this as ZERO comprehension: only " +
            `${overall.minorityHits} of ${overall.correct} correct answers named a label that ` +
            "is NOT its tag's most common one, so what is left is a frequency prior over the " +
            "retrieved chunks, not the geometry the questions ask about."
          : ` But ${overall.minorityHits} of ${overall.correct} correct answers ` +
            `(${minorityPct.toFixed(0)}%) named a label that is NOT its tag's most common one, ` +
            "which a frequency prior produces at a rate of zero — and it declined " +
            `${overall.abstained}/${overall.n} rather than guessing, scoring ` +
            `${overall.selective.toFixed(0)}% on the ${overall.answered} it did answer against a ` +
            `baseline of ${overall.base.pct.toFixed(0)}%. That is not zero comprehension. It is ` +
            "a model reading part of the sheet and refusing the rest, which is the trade FR-14 " +
            "asks for — so the question is whether it refused the RIGHT cases, and whether the " +
            "description covers the intersections it declined."),
    );
  } else {
    console.log(
      `\n  Beat the baseline: ${beaten.join(", ")}. Across the set ${overall.minorityHits} of ` +
        `${overall.correct} correct answers named a label that is NOT its tag's most common one` +
        " — the part a frequency prior cannot fake.",
    );
  }

  const spent = saturation(rows);
  if (spent) {
    console.log(
      `\n  THIS SET IS SPENT: no answer is wrong, off-target, invented or hedged, so the only ` +
        `headroom left is ${spent.left} of ${spent.cases} case${spent.left === 1 ? "" : "s"} — ` +
        `${spent.points.toFixed(1)} points. Run-to-run spread at a FIXED configuration has been ` +
        "measured on this set at 43 points, so nothing a future change does can be shown " +
        "through a gap this small: a lower score is not a regression and a higher one is not an " +
        "improvement. The next measurement needs a harder SET, not a better pipeline — " +
        "regenerate against every intersection the grid has rather than the ones that survived " +
        "the jitter test, and add a tag this pipeline is not already built to answer.",
    );
  }

  // A set-wide verdict hides a tag running on a prior: this set beat its
  // baseline overall (63% against 43%) on a run whose column tag tied its own
  // baseline to the case and said one word to 83% of the questions it answered.
  // "Beat the baseline" was true and was the wrong thing to take away.
  const worst = overall.concentration;
  if (worst && worst.gap >= OVER_NAMING_POINTS) {
    console.log(
      `\n  Watch ${worst.tag}: it answered "${worst.label}" ${worst.named} times in ` +
        `${worst.answered} (${worst.namedPct.toFixed(0)}%), where that is what the sheet shows ` +
        `${worst.truthPct.toFixed(0)}% of the time. Whatever that tag scored, it is reaching for ` +
        "one label far more often than the drawing offers it" +
        (worst.components?.halfRead
          ? `. But look at WHICH PART is wrong: ${worst.components.held} of its ` +
            `${worst.components.misses} misses carry the right ${worst.components.heldPart} ` +
            `and miss only the ${worst.components.wrongPart}` +
            (worst.components.swaps.length
              ? ` (${worst.components.swaps
                  .slice(0, 2)
                  .map(([swap, n]) => `${swap}, ${n}x`)
                  .join("; ")})`
              : "") +
            ". A model reading nothing does not place the other half correctly that often. " +
            "This is one glyph pair misread at every intersection — a resolution failure on " +
            "a compound label, not a frequency prior, and the two want opposite fixes."
          : worst.placement
          ? `. But ${worst.placedMisses} of the ${worst.namedMisses} misses naming it are that ` +
            "label's OWN intersection one bay away, so this is not a frequency prior — it is a " +
            "size read correctly and put in the wrong place. The two look identical in this " +
            "count and want opposite fixes: a prior wants comprehension, a misplacement wants " +
            "locality, and a higher-resolution whole-sheet image makes locality worse."
          : worst.prior
          ? (worst.beatsBase
              ? ", and only " +
                `${worst.independentHits} of its ${worst.correct} correct answers name a label ` +
                "that is neither that one nor the tag's majority"
              : `, and it scored ${((worst.correct / tally(rows.filter((r) => r.tag === worst.tag)).n) * 100).toFixed(0)}% ` +
                "where guessing this tag's most common label scores " +
                `${tally(rows.filter((r) => r.tag === worst.tag)).base.pct.toFixed(0)}%`) +
            " — so its hits ride on a frequency rather than on the intersection each question " +
            "names. Where the over-named label happens to be the truth, a fixated answer is " +
            "right by coincidence, and it counts as a minority hit while reading nothing."
          : `, though ${worst.independentHits} of its ${worst.correct} correct answers name a ` +
            "label that is neither that one nor the tag's majority, which fixation does not " +
            "produce. It is leaning on one label and still reading the rest."),
    );
  }

  // FR-13, measured for the first time. Printed above the run history because
  // it is a claim about THIS run rather than about the series.
  const unsupported = rows.filter((r) => r.support === "unsupported");
  const uncited = rows.filter((r) => r.support === "uncited");
  if (unsupported.length || uncited.length) {
    const parts = [];
    if (unsupported.length) {
      parts.push(
        `${unsupported.length} named a label that appears in NONE of the chunks they cited`,
      );
    }
    if (uncited.length) parts.push(`${uncited.length} named one and cited nothing at all`);
    console.log(
      `\n  Citations: of ${rows.length} answers, ${parts.join(", and ")}. FR-13 says every ` +
        "statement is traceable to a chunk a reader can open, so those are answers whose " +
        "chain does not hold — scored CORRECT wherever the label was right, because the " +
        "scorer grades what was answered and not what it was hung on. This is a weak test " +
        "and only catches a citation that could not possibly support its claim: it asks " +
        "whether the label appears anywhere in the cited text, so a chunk that merely lists " +
        "the mark passes.",
    );
    for (const r of unsupported.slice(0, 6)) {
      console.log(`    ${r.grid.padEnd(8)} ${r.tag.padEnd(14)} said ${r.said} [${r.outcome}]`);
    }
  }

  // Printed FIRST among the history lines, because it is the only one that
  // bounds what any other number on this screen is allowed to claim.
  for (const r of history?.repeats ?? []) {
    console.log(
      `\n  ERROR BAR: "${r.label}" has been ingested ${r.n} times — ` +
        `${r.scores.map((p) => `${p.toFixed(0)}%`).join(", ")}, a ${r.spread.toFixed(0)}-point ` +
        "spread with NOTHING changed between them" +
        (r.worstTag
          ? `. The ${r.worstTag.tag} tag inside those runs ran ` +
            `${r.worstTag.scores.map((p) => `${p.toFixed(0)}%`).join(", ")} — ` +
            `${r.worstTag.spread.toFixed(0)} points, because a set-wide number averages the ` +
            "variance away"
          : "") +
        `.\n  Read every comparison above against that: a difference smaller than ` +
        `${r.spread.toFixed(0)} points is not evidence of anything, however well it fits the ` +
        "story. That includes differences this report itself describes as large.",
    );
  }

  if (history && history.ingests.length > 1) {
    const line = history.recent
      .map((i) => `${i.pct.toFixed(0)}%${i.described ? "" : " (no descriptions)"}`)
      .join(", ");
    console.log(
      `\n  This set has been scored ${history.runs} times over ` +
        `${history.ingests.length} corpora. The last ${history.recent.length}, oldest first: ` +
        `${line}.\n  Those span configurations this harness cannot see — VLM_* is read by the ` +
        "worker at ingest and the chunks carry none of it — so the " +
        `${history.range.toFixed(0)}-point range between them is NOT an error bar. It mixes ` +
        "real changes with the spread of asking one model twice, and only REPEATING one " +
        "configuration separates those. Until that is done a difference between two runs is a " +
        "hypothesis, not a measurement.",
    );
  }
  if (history?.rescored.length) {
    for (const r of history.rescored) {
      console.log(
        `\n  ALARM: the same descriptions (project ${r.projectId}) have scored ` +
          `${r.scores.map((p) => `${p.toFixed(0)}%`).join(" and ")}. Same chunks in, and ` +
          "temperature: 0 means same answer out — so this is the SCORER or the chat path " +
          "changing under the set, not the model. Fix that before reading any comparison above.",
      );
    }
  }

  // The descriptions are named, not counted. A configuration change that did
  // not reach the corpus leaves these ids untouched, and that is the first
  // thing to check before reading any number above as a result.
  const descriptions = [...new Set(rows.flatMap((r) => r.descriptionChunkIds ?? []))].sort();
  console.log(
    `\n  Config: CHAT_PROVIDER=${process.env.CHAT_PROVIDER ?? "claude"} ` +
      `HYBRID_RETRIEVAL=${process.env.HYBRID_RETRIEVAL ?? "true"} ` +
      `k=${RETRIEVAL_LIMIT}` +
      `\n  Project: ${rows[0]?.projectId ?? "?"}` +
      `\n  Descriptions scored: ${descriptions.length ? descriptions.join(", ") : "NONE"}` +
      `\n  ${describeCoverage(onSheet ?? [], descriptions)}\n`,
  );
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) {
    console.log(readFileSync(fileURLToPath(import.meta.url), "utf8").split("*/")[0]);
    return;
  }

  let cases = JSON.parse(readFileSync(args.set, "utf8"));
  // Applied before the preflight and before the vocabulary, so one generated
  // set can score any project holding the sheet. The workflow this exists for
  // is ingesting the same PDF into a fresh project per configuration; without
  // it the harness keeps asking whichever project the set was generated
  // against and returns a stale answer rather than an error.
  cases = applyProjectOverride(cases, args.project);
  if (args.limit > 0) cases = cases.slice(0, args.limit);
  if (!cases.length) throw new Error(`${args.set} has no cases`);

  const { retrieval, answer, citations, prisma } = await loadApi();
  await preflight(prisma, cases);
  const descriptionsOnSheet = await descriptionChunksOnSheets(prisma, cases);

  // Built from the WHOLE set, never from the sliced --limit view: a five-case
  // smoke run must score against the same vocabulary as the full one, or its
  // outcomes are not comparable to anything.
  const vocabularies = labelVocabulary(JSON.parse(readFileSync(args.set, "utf8")));
  const projectId = cases[0].projectId;

  const rows = [];
  // Every chunk body this run has seen, so a cited id can be checked against
  // what it actually says. Accumulated rather than re-queried: the same chunks
  // come back for case after case.
  const textById = new Map();
  for (const [index, testCase] of cases.entries()) {
    const { chunkIds } = await retrieval.retrieveChunkIds(testCase.projectId, testCase.question, {
      limit: RETRIEVAL_LIMIT,
    });
    const chunkRows = await prisma.chunk.findMany({
      where: { id: { in: chunkIds } },
      include: { page: { include: { document: true } } },
    });
    const byId = new Map(chunkRows.map((c) => [c.id, c]));
    const ordered = chunkIds.flatMap((id) => byId.get(id) ?? []);
    for (const c of chunkRows) textById.set(c.id, c.text ?? "");

    // The same call chat.ts makes, with no history: a benchmark question has no
    // conversation behind it, and an inherited one would leak between cases.
    const text = await answer.answerFromChunks(
      testCase.question,
      ordered.map((c) => ({
        chunkId: c.id,
        filename: c.page.document.filename,
        combinedPageNumber: c.page.combinedPageNumber,
        text: c.text,
        sheetNumber: c.page.sheetNumber,
        discipline: c.page.discipline,
        // Without this the harness scores a prompt production never builds. The
        // chat route passes it at both of its mapping sites, and answer.ts uses
        // it to mark the block kind="description" and apply the rules that go
        // with one — write "the drawing shows", never quote it, and let the
        // sheet's own text win any disagreement. Dropping it here left the
        // model reading a vision model's account as though it were words lifted
        // off the drawing, which is the one confusion this whole phase exists
        // to prevent, and it did so in the direction that flatters the result.
        kind: c.kind,
      })),
      [],
    );

    const vocabulary = vocabularies.get(testCase.tag) ?? [];
    const outcome = score(text, testCase, vocabulary);
    const said =
      namedLabels(text, vocabulary).join(", ") ||
      inventedLabel(text, testCase, vocabulary) ||
      "";
    // The API's own extractor, not a second regex here: it already knows every
    // bracket shape the model emits, including `[chunk:a, chunk:b]`, which a
    // pattern anchored on one id per bracket matches neither half of.
    const citedChunkIds = citations.extractCitedChunkIds(text);
    rows.push({
      grid: `${testCase.derivation.gridColumn}/${testCase.derivation.gridRow}`,
      // Where this intersection IS, in the PDF's own points. Carried so the
      // report can tell a misread label from a correctly-read one placed at
      // the wrong intersection — two failures with opposite fixes.
      point: testCase.derivation.intersectionPt ?? null,
      projectId: testCase.projectId,
      tag: testCase.tag,
      expected: testCase.expected,
      distractor: testCase.distractor,
      outcome,
      // Every label of this kind the answer named. For a miss it is the whole
      // point — "said F11" and "said nothing" are different failures.
      said,
      // WHICH chunks the answer hung itself on, and whether any of them so much
      // as contains the label it gave. FR-13's chain is the product's central
      // promise and the scorer has never looked at it.
      citedChunkIds,
      support: citationSupport(said, citedChunkIds, textById),
      labelPattern: testCase.labelPattern ?? null,
      sheetLabels: testCase.sheetLabels ?? [],
      retrieved: ordered.length,
      descriptionChunks: ordered.filter((c) => c.kind === "description").length,
      // The ids, not just the count. Two runs citing the same description ids
      // are the same experiment however different their configuration looked:
      // chunk ids are uuid4 assigned per ingest (db.replace_page_chunks deletes
      // and reinserts), so an unchanged id proves the descriptions were never
      // regenerated. Three runs in a row went unnoticed for want of this.
      descriptionChunkIds: ordered.filter((c) => c.kind === "description").map((c) => c.id),
      answer: text,
    });
    if (!args.json) {
      process.stderr.write(`  [${index + 1}/${cases.length}] ${outcome}\r`);
    }
  }

  // Always, not just under --json. A run costs one completion per case, and the
  // previous shape computed every answer and then threw it away unless asked --
  // so the first question anyone had about a surprising bucket ("did it really
  // decline 21 times, or did it answer with the wrong label?") could only be
  // settled by paying for the whole set again.
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  const outPath = args.out ?? resolve(here, "runs", `drawing-eval-${stamp}.json`);
  mkdirSync(dirname(outPath), { recursive: true });
  writeFileSync(
    outPath,
    JSON.stringify(
      {
        ranAt: new Date().toISOString(),
        set: args.set,
        projectId,
        descriptionChunkIds: [...new Set(rows.flatMap((r) => r.descriptionChunkIds))].sort(),
        // What EXISTS, against what was reached above. A run that retrieved a
        // description for every one of its cases still only ever surfaced one.
        descriptionChunksOnSheet: [...descriptionsOnSheet].sort(),
        retrievalLimit: RETRIEVAL_LIMIT,
        label: args.label ?? null,
        chatProvider: process.env.CHAT_PROVIDER ?? "claude",
        hybridRetrieval: process.env.HYBRID_RETRIEVAL ?? "true",
        summary: summarize(rows),
        cases: rows,
      },
      null,
      2,
    ),
  );

  report(rows, args.json, descriptionsOnSheet, readRunHistory(dirname(outPath), args.set));
  process.stderr.write(`  Answers: ${outPath}\n\n`);
  await prisma.$disconnect();
}

// Guarded so the scoring functions can be imported by the test file without
// the harness firing off a run — and a run costs one completion per case.
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((err) => {
    console.error(`\n${err.message}\n`);
    process.exit(1);
  });
}
