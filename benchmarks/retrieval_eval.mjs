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
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, "..");

// The API is TypeScript; tsx compiles it on the way in. Imported lazily so
// --help works without a database.
async function loadApi() {
  const { register } = await import("tsx/esm/api");
  register();
  // env.ts validates on import and reads .env relative to the cwd, which is
  // how the API itself is run.
  process.chdir(resolve(root, "apps/api"));
  try {
    const retrieval = await import(resolve(root, "apps/api/src/retrieval.ts"));
    const { prisma } = await import(resolve(root, "apps/api/src/db.ts"));
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
 * A case passes if any EXPECTED chunk or page came back. Pages are the
 * practical unit: a person marking up an evaluation set can read a page number
 * off the viewer, but cannot see chunk ids without going to the database — and
 * an answer citing any chunk on the right page is a correct answer.
 */
function scoreCase(testCase, retrieved) {
  const wantChunks = new Set(testCase.expectedChunkIds ?? []);
  const wantPages = new Set(testCase.expectedPages ?? []);

  for (let rank = 0; rank < retrieved.length; rank++) {
    const hit = retrieved[rank];
    if (wantChunks.has(hit.chunkId) || wantPages.has(hit.combinedPageNumber)) {
      return { found: true, rank: rank + 1, reciprocal: 1 / (rank + 1) };
    }
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
  console.log(
    `\nMark the pages that actually answer it, then add:\n\n` +
      JSON.stringify(
        { projectId, question, expectedPages: [hits[0]?.combinedPageNumber ?? 0], note: "" },
        null,
        2,
      ) +
      "\n",
  );
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
  const unmarked = cases.filter(
    (c) => (c.expectedPages ?? []).length === 0 && (c.expectedChunkIds ?? []).length === 0,
  );
  if (unmarked.length) {
    console.warn(
      `\n  ${unmarked.length}/${cases.length} cases have nothing expected — they can only ever fail.\n` +
        `  Fill in expectedPages before trusting the numbers.`,
    );
  }

  const results = [];
  for (const testCase of cases) {
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
