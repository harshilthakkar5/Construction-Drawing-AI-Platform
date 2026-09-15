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
 *
 * Every case costs one chat completion, so the full set is a real spend. Start
 * with --limit.
 *
 * It needs the API's environment (DATABASE_URL, QDRANT_URL, the embedding key
 * and the chat provider's key) because it runs the API's own retrieval and
 * answer code rather than a copy.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath, pathToFileURL } from "node:url";
import { dirname, resolve } from "node:path";

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
    const { prisma } = await load("apps/api/src/db.ts");
    return { retrieval, answer, prisma };
  } catch (err) {
    throw new Error(
      "could not load the API's code. This harness runs the real thing, so it needs the " +
        "API's environment — copy .env to apps/api/.env, or export DATABASE_URL, QDRANT_URL, " +
        `the embedding provider's key and the chat provider's key.\n\n  Underlying error: ${err.message}`,
    );
  }
}

function parseArgs(argv) {
  const args = { set: resolve(here, "drawing_eval_set.json"), json: false, limit: 0 };
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === "--json") args.json = true;
    else if (arg === "--set") args.set = resolve(process.cwd(), argv[++i]);
    else if (arg === "--limit") args.limit = Number(argv[++i]);
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
 * Four outcomes, not two.
 *
 * A model that declines to answer is not a model that answers wrongly, and on
 * construction drawings the difference is the whole point: "the drawings do not
 * show this" sends someone to look at the sheet, while a confident wrong
 * footing mark gets poured. Collapsing them into one "incorrect" bucket would
 * hide the only failure that is actually dangerous, and would punish exactly
 * the behaviour FR-14 asks for.
 */
export function score(answer, testCase) {
  const hasExpected = mentions(answer, testCase.expected);
  const hasDistractor = mentions(answer, testCase.distractor);
  if (hasExpected && !hasDistractor) return "correct";
  if (hasExpected && hasDistractor) return "hedged";
  if (hasDistractor) return "wrong";
  return "abstained";
}

/** The corpus must be able to answer at all before a run means anything: a set
 *  pointed at a deleted or re-uploaded project would report confident zeros. */
async function preflight(prisma, cases) {
  const ids = [...new Set(cases.map((c) => c.projectId))];
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
  }
}

function report(rows, json) {
  if (json) {
    console.log(JSON.stringify({ cases: rows }, null, 2));
    return;
  }
  const tally = (subset) => {
    const n = subset.length || 1;
    const count = (k) => subset.filter((r) => r.outcome === k).length;
    return {
      n: subset.length,
      correct: count("correct"),
      wrong: count("wrong"),
      hedged: count("hedged"),
      abstained: count("abstained"),
      pct: ((count("correct") / n) * 100).toFixed(0),
    };
  };
  const line = (label, t) =>
    `  ${label.padEnd(16)} ${String(t.n).padStart(3)}  ` +
    `correct ${String(t.correct).padStart(3)} (${t.pct.padStart(3)}%)  ` +
    `wrong ${String(t.wrong).padStart(3)}  hedged ${String(t.hedged).padStart(3)}  ` +
    `abstained ${String(t.abstained).padStart(3)}`;

  console.log("\n  Drawing comprehension\n");
  console.log(line("ALL", tally(rows)));
  for (const tag of [...new Set(rows.map((r) => r.tag))].sort()) {
    console.log(line(tag, tally(rows.filter((r) => r.tag === tag))));
  }
  const wrong = rows.filter((r) => r.outcome === "wrong");
  if (wrong.length) {
    console.log("\n  Wrong answers (said the distractor, not the truth):");
    for (const r of wrong) {
      console.log(`    ${r.grid.padEnd(8)} ${r.tag.padEnd(14)} expected ${r.expected}, said ${r.distractor}`);
    }
  }
  console.log(
    `\n  Config: CHAT_PROVIDER=${process.env.CHAT_PROVIDER ?? "claude"} ` +
      `HYBRID_RETRIEVAL=${process.env.HYBRID_RETRIEVAL ?? "true"} ` +
      `k=${RETRIEVAL_LIMIT}\n`,
  );
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) {
    console.log(readFileSync(fileURLToPath(import.meta.url), "utf8").split("*/")[0]);
    return;
  }

  let cases = JSON.parse(readFileSync(args.set, "utf8"));
  if (args.limit > 0) cases = cases.slice(0, args.limit);
  if (!cases.length) throw new Error(`${args.set} has no cases`);

  const { retrieval, answer, prisma } = await loadApi();
  await preflight(prisma, cases);

  const rows = [];
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
      })),
      [],
    );

    const outcome = score(text, testCase);
    rows.push({
      grid: `${testCase.derivation.gridColumn}/${testCase.derivation.gridRow}`,
      tag: testCase.tag,
      expected: testCase.expected,
      distractor: testCase.distractor,
      outcome,
      answer: text,
    });
    if (!args.json) {
      process.stderr.write(`  [${index + 1}/${cases.length}] ${outcome}\r`);
    }
  }

  report(rows, args.json);
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
