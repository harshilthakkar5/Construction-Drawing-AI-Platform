#!/usr/bin/env node
/**
 * Retrieval evaluation harness.
 *
 * Every retrieval change so far — hybrid search, the identifier arm, the
 * reranker — claims to make answers better, and until this existed the only
 * instrument was asking a question and looking at the result. That cannot tell
 * a real gain from a lucky one, and cannot see a regression at all.
 *
 * This runs a fixed set of questions through the API's real retrieval path and
 * reports two numbers:
 *
 *   recall@k   did the chunk that answers the question come back at all?
 *              This is the one that matters. A chunk that is not retrieved
 *              cannot be cited, so recall is the ceiling on answer quality.
 *   MRR        how high up was the first right one? Position matters because
 *              the model reads the top of the list most carefully.
 *
 * Usage
 *   node benchmarks/retrieval_eval.mjs                     # run the set
 *   node benchmarks/retrieval_eval.mjs --set my.json       # a different set
 *   node benchmarks/retrieval_eval.mjs --json              # machine-readable
 *   node benchmarks/retrieval_eval.mjs --capture "question" --project <id>
 *                                                          # draft a new case
 *
 * A case says what SHOULD come back, as expectedText (a short phrase quoted
 * off the sheet — preferred), expectedPages, or expectedChunkIds. Prefer text:
 * page numbers are derived from document order and silently repoint at the
 * wrong sheet when a set is re-uploaded. Expectations are checked against the
 * corpus before the run, so one that can never match is an error rather than a
 * reported miss.
 *
 * To compare two configurations, run it twice with different env — the point
 * of the numbers is the difference between runs:
 *
 *   HYBRID_RETRIEVAL=false node benchmarks/retrieval_eval.mjs
 *   RERANK_PROVIDER=cohere node benchmarks/retrieval_eval.mjs
 *
 * It needs the same environment the API does (DATABASE_URL, QDRANT_URL and the
 * embedding provider's key) because it calls the API's own retrieval code
 * rather than a copy of it — a harness that measured a reimplementation would
 * measure the wrong thing.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath, pathToFileURL  } from "node:url";
import { dirname, resolve } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, "..");

// The API is TypeScript; tsx compiles it on the way in. Imported lazily so
// --help works without a database.
async function loadApi() {
  // A benchmark's calls are not a user's: without this every question would be
  // billed to the project in the dashboard, and a placeholder project id would
  // raise a foreign-key error per question on top.
  process.env.USAGE_TRACKING = "off";

  const { register } = await import("tsx/esm/api");
  register();
  // env.ts validates on import and reads .env relative to the cwd, which is
  // how the API itself is run.
  process.chdir(resolve(root, "apps/api"));
  try {
    // const retrieval = await import(resolve(root, "apps/api/src/retrieval.ts"));
    const retrieval = await import(
  pathToFileURL(resolve(root, "apps/api/src/retrieval.ts")).href
);
        const { prisma } = await import(
      pathToFileURL(resolve(root, "apps/api/src/db.ts")).href
    );
    return { retrieval, prisma };
  } catch (err) {
    throw new Error(
      "could not load the API's retrieval code. This harness runs the real thing, so it " +
        "needs the API's environment — copy .env to apps/api/.env, or export DATABASE_URL, " +
        `QDRANT_URL and the embedding provider's key.\n\n  Underlying error: ${err.message}`,
    );
  }
}

function parseArgs(argv) {
  const args = { set: resolve(here, "retrieval_eval_set.json"), json: false, k: 18 };
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === "--json") args.json = true;
    else if (arg === "--set") args.set = resolve(process.cwd(), argv[++i]);
    else if (arg === "--k") args.k = Number(argv[++i]);
    else if (arg === "--capture") args.capture = argv[++i];
    else if (arg === "--project") args.project = argv[++i];
    else if (arg === "--help" || arg === "-h") args.help = true;
  }
  return args;
}

/**
 * Normalize for text matching: collapse whitespace, upper-case.
 *
 * Extracted drawing text carries the PDF's own line breaks, so a phrase a
 * person reads as one line ("IT-2 STEEL CONSTRUCTION") can hold a newline in
 * the middle of it. Matching on the raw string would fail for reasons that
 * have nothing to do with retrieval.
 */
function normalizeText(value) {
  return String(value ?? "").replace(/\s+/g, " ").trim().toUpperCase();
}

/** expectedText accepts one string or several; any one matching is a pass. */
function expectedTexts(testCase) {
  const raw = testCase.expectedText ?? [];
  return (Array.isArray(raw) ? raw : [raw]).map(String).filter((s) => s.trim());
}

/** Whether a case says anything at all about what should come back. */
function isMarked(testCase) {
  return (
    (testCase.expectedPages ?? []).length > 0 ||
    (testCase.expectedChunkIds ?? []).length > 0 ||
    expectedTexts(testCase).length > 0
  );
}

/**
 * A case passes if any EXPECTED chunk, page or TEXT came back.
 *
 * expectedText is the anchor to prefer, and the reason is that the other two
 * are DERIVED. A combined page number depends on document order, so
 * re-uploading the same set in a different order silently repoints every case
 * in the file at the wrong sheet — and the run still reports a number, which
 * is the dangerous part: a broken expectation is indistinguishable from a
 * retrieval failure. The words on the sheet do not move. Anchor on those and
 * the set survives re-uploads, renumbering and re-chunking.
 *
 * Pages remain supported because they are easy to read off the viewer, and an
 * answer citing any chunk on the right page is a correct answer.
 */
function scoreCase(testCase, retrieved) {
  const wantChunks = new Set(testCase.expectedChunkIds ?? []);
  const wantPages = new Set(testCase.expectedPages ?? []);
  const wantText = expectedTexts(testCase).map(normalizeText);

  for (let rank = 0; rank < retrieved.length; rank++) {
    const hit = retrieved[rank];
    const matched =
      wantChunks.has(hit.chunkId) ||
      wantPages.has(hit.combinedPageNumber) ||
      wantText.some((needle) => hit.normalizedText.includes(needle));
    if (matched) return { found: true, rank: rank + 1, reciprocal: 1 / (rank + 1) };
  }
  return { found: false, rank: null, reciprocal: 0 };
}

/** Chunk ids → the page each sits on, so a case can be marked up by page. */
async function locate(prisma, chunkIds) {
  if (chunkIds.length === 0) return [];
  const rows = await prisma.chunk.findMany({
    where: { id: { in: chunkIds } },
    select: {
      id: true,
      text: true,
      page: { select: { combinedPageNumber: true, document: { select: { filename: true } } } },
    },
  });
  const byId = new Map(rows.map((r) => [r.id, r]));
  // Keep retrieval's order: rank is the whole point of the measurement.
  return chunkIds.flatMap((id) => {
    const row = byId.get(id);
    return row
      ? [
          {
            chunkId: id,
            combinedPageNumber: row.page.combinedPageNumber,
            filename: row.page.document.filename,
            preview: row.text.replace(/\s+/g, " ").slice(0, 90),
            normalizedText: normalizeText(row.text),
          },
        ]
      : [];
  });
}

/** --capture: run one question and print a case skeleton to fill in. */
async function capture(api, question, projectId, k) {
  const { chunkIds } = await api.retrieval.retrieveChunkIds(projectId, question, { limit: k });
  const hits = await locate(api.prisma, chunkIds);

  console.log(`\nQuestion: ${question}\n`);
  hits.forEach((hit, i) => {
    console.log(
      `  ${String(i + 1).padStart(2)}. p.${String(hit.combinedPageNumber).padEnd(4)} ` +
        `${hit.filename.slice(0, 34).padEnd(34)} ${hit.preview}`,
    );
  });
  // Deliberately NOT pre-filled with the top hit's page. Doing that made the
  // skeleton assert "whatever retrieval already ranked first is correct",
  // which is circular: the set then measures agreement with the behaviour it
  // was captured from, and every later change looks like a regression. The
  // field is left empty so it has to be answered from the drawing.
  console.log(
    `\nFind the text on the sheet that actually answers it and quote a short,\n` +
      `distinctive phrase of it below — not a page number, which moves when\n` +
      `documents are re-uploaded:\n\n` +
      JSON.stringify({ projectId, question, tag: "", expectedText: "", note: "" }, null, 2) +
      "\n",
  );
}

/**
 * The combined page numbers this project actually has.
 *
 * Used to reject an expectation that can never be met. An expectedPages value
 * outside this range is not a hard case, it is a broken one — and scoring it
 * as a miss reports a retrieval failure that never happened.
 */
async function pageRange(prisma, projectId) {
  const agg = await prisma.page.aggregate({
    where: { document: { projectId, supersededAt: null } },
    _min: { combinedPageNumber: true },
    _max: { combinedPageNumber: true },
  });
  return { min: agg._min.combinedPageNumber, max: agg._max.combinedPageNumber };
}

/**
 * How many chunks of this project contain `needle`.
 *
 * Normalized in SQL the same way scoreCase normalizes in JS, so the preflight
 * and the scoring agree about what "contains" means. strpos rather than ILIKE
 * because a needle holding % or _ is a literal here, not a pattern.
 */
async function textHits(prisma, projectId, needle) {
  const rows = await prisma.$queryRaw`
    SELECT count(*)::int AS hits
      FROM chunks c
      JOIN pages p ON c."pageId" = p.id
      JOIN documents d ON p."documentId" = d.id
     WHERE d."projectId" = ${projectId}
       AND d."supersededAt" IS NULL
       AND strpos(upper(regexp_replace(c.text, '\\s+', ' ', 'g')), ${needle}) > 0
  `;
  return Number(rows[0]?.hits ?? 0);
}

/**
 * Preflight: check every expectation against the corpus BEFORE measuring.
 *
 * This exists because of a real failure. An evaluation set was marked up with
 * page numbers taken from the source PDFs' own filenames rather than the
 * app's combined numbering, so most cases pointed at pages the project did not
 * have. Two full runs reported recall of 40% and 20% and were used to compare
 * two retrieval configurations — while the retriever had in fact been
 * returning the right chunk at rank 1. Nothing in the harness objected,
 * because a wrong expectation and a missed chunk score identically.
 *
 * So an expectation that CANNOT match is now an error rather than a zero. A
 * benchmark is allowed to report bad news; it is not allowed to invent it.
 */
async function preflight(prisma, cases) {
  const ranges = new Map();
  for (const projectId of new Set(cases.map((c) => c.projectId))) {
    ranges.set(projectId, await pageRange(prisma, projectId));
  }

  const checked = [];
  for (const testCase of cases) {
    const range = ranges.get(testCase.projectId);
    const pages = testCase.expectedPages ?? [];
    const strayPages =
      range.max === null ? pages : pages.filter((n) => n < range.min || n > range.max);

    const missingText = [];
    for (const needle of expectedTexts(testCase)) {
      if ((await textHits(prisma, testCase.projectId, normalizeText(needle))) === 0) {
        missingText.push(needle);
      }
    }

    // expectedChunkIds are taken on trust: an id is not guessable, so one that
    // is present was read out of this database.
    const reachable =
      pages.length -
      strayPages.length +
      (expectedTexts(testCase).length - missingText.length) +
      (testCase.expectedChunkIds ?? []).length;

    checked.push({ testCase, strayPages, missingText, reachable, range });
  }
  return checked;
}

function summarize(results, k) {
  const total = results.length;
  const found = results.filter((r) => r.found).length;
  const mrr = results.reduce((sum, r) => sum + r.reciprocal, 0) / (total || 1);
  const byTag = new Map();
  for (const result of results) {
    const tag = result.tag ?? "untagged";
    const bucket = byTag.get(tag) ?? { total: 0, found: 0 };
    bucket.total++;
    if (result.found) bucket.found++;
    byTag.set(tag, bucket);
  }
  return {
    cases: total,
    [`recall@${k}`]: total ? found / total : 0,
    mrr,
    byTag: Object.fromEntries(
      [...byTag].map(([tag, b]) => [tag, { recall: b.total ? b.found / b.total : 0, cases: b.total }]),
    ),
  };
}

function report(results, summary, k) {
  const pct = (n) => `${(n * 100).toFixed(1)}%`;
  console.log("");
  for (const r of results) {
    const mark = r.found ? "✓" : "✗";
    const rank = r.found ? `#${r.rank}` : "miss";
    console.log(`  ${mark} ${rank.padEnd(6)} ${r.question.slice(0, 68)}`);
  }
  console.log(
    `\n  ${summary.cases} cases · recall@${k} ${pct(summary[`recall@${k}`])} · MRR ${summary.mrr.toFixed(3)}`,
  );
  const tags = Object.entries(summary.byTag);
  if (tags.length > 1) {
    console.log("");
    for (const [tag, stats] of tags.sort((a, b) => a[1].recall - b[1].recall)) {
      console.log(`    ${tag.padEnd(16)} ${pct(stats.recall).padStart(6)}  (${stats.cases})`);
    }
  }
  console.log(
    `\n  Config: HYBRID_RETRIEVAL=${process.env.HYBRID_RETRIEVAL ?? "true"} ` +
      `RERANK_PROVIDER=${process.env.RERANK_PROVIDER ?? "none"} ` +
      `EMBEDDING_PROVIDER=${process.env.EMBEDDING_PROVIDER ?? "voyage"}\n`,
  );
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) {
    console.log(readFileSync(fileURLToPath(import.meta.url), "utf8").split("*/")[0].slice(3));
    return;
  }

  const api = await loadApi();

  if (args.capture) {
    if (!args.project) throw new Error("--capture needs --project <projectId>");
    await capture(api, args.capture, args.project, args.k);
    await api.prisma.$disconnect();
    return;
  }

  const raw = JSON.parse(readFileSync(args.set, "utf8"));
  // The shipped set leads with a _comment block explaining the format; skip
  // anything that is not a real case rather than crashing on it.
  const cases = (Array.isArray(raw) ? raw : []).filter((c) => c && c.question && c.projectId);
  if (cases.length === 0) {
    throw new Error(
      `${args.set} holds no usable cases (each needs projectId and question).\n` +
        `  Build one with: node benchmarks/retrieval_eval.mjs --capture "a question" --project <id>`,
    );
  }
  // A placeholder id queries a project that does not exist, which returns
  // nothing for every question and reads as 0% recall — a number that looks
  // like a retrieval result and is not one. Refuse rather than report it.
  const placeholders = cases.filter((c) => /REPLACE|<.*>/i.test(c.projectId));
  if (placeholders.length) {
    throw new Error(
      `${placeholders.length}/${cases.length} cases still carry the placeholder projectId.\n` +
        `  Every question would return nothing and score 0%, which is not a retrieval result.\n` +
        `  Put a real project id in ${args.set} first.`,
    );
  }

  const unmarked = cases.filter((c) => !isMarked(c));
  if (unmarked.length === cases.length) {
    throw new Error(
      `no case in ${args.set} says what it expects, so every one scores as a miss.\n` +
        `  Build them with: node benchmarks/retrieval_eval.mjs --capture "a question" --project <id>`,
    );
  }
  if (unmarked.length) {
    console.warn(
      `\n  ${unmarked.length}/${cases.length} cases have nothing expected and are SKIPPED.\n` +
        `  Fill in expectedText to include them.`,
    );
  }

  const marked = cases.filter(isMarked);

  // Verify the expectations against the corpus before measuring anything.
  const checked = await preflight(api.prisma, marked);
  const impossible = checked.filter((c) => c.reachable === 0);
  if (impossible.length) {
    const detail = impossible
      .map(({ testCase, strayPages, missingText, range }) => {
        const why = [
          strayPages.length
            ? `expectedPages ${strayPages.join(", ")} (project has pages ${range.min}-${range.max})`
            : null,
          missingText.length
            ? `expectedText ${missingText.map((t) => JSON.stringify(t)).join(", ")} appears in no chunk`
            : null,
        ].filter(Boolean);
        return `    - ${testCase.question.slice(0, 62)}\n        ${why.join("; ")}`;
      })
      .join("\n");
    throw new Error(
      `${impossible.length}/${marked.length} cases in ${args.set} expect something this project\n` +
        `  cannot return, so they would score as retrieval failures that never happened:\n\n` +
        `${detail}\n\n` +
        `  Fix the expectations — quote text off the sheet with expectedText, which does\n` +
        `  not move when documents are re-uploaded — then run again.`,
    );
  }
  const partial = checked.filter((c) => c.reachable > 0 && (c.strayPages.length || c.missingText.length));
  for (const { testCase, strayPages, missingText, range } of partial) {
    console.warn(
      `\n  Unreachable expectation on "${testCase.question.slice(0, 50)}":\n` +
        (strayPages.length ? `    pages ${strayPages.join(", ")} — project has ${range.min}-${range.max}\n` : "") +
        (missingText.length ? `    text ${missingText.map((t) => JSON.stringify(t)).join(", ")} — in no chunk\n` : "") +
        `    The case can still pass on its other expectations.`,
    );
  }

  const scorable = marked;

  const results = [];
  for (const testCase of scorable) {
    const { chunkIds } = await api.retrieval.retrieveChunkIds(
      testCase.projectId,
      testCase.question,
      { limit: args.k, portionId: testCase.portionId },
    );
    const hits = await locate(api.prisma, chunkIds);
    results.push({ ...scoreCase(testCase, hits), question: testCase.question, tag: testCase.tag });
  }

  const summary = summarize(results, args.k);
  if (args.json) {
    console.log(JSON.stringify({ summary, results }, null, 2));
  } else {
    report(results, summary, args.k);
  }

  await api.prisma.$disconnect();
}

main().catch((err) => {
  console.error(`\n  ${err.message}\n`);
  process.exit(1);
});
