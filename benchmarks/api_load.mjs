#!/usr/bin/env node
/**
 * Concurrent-reader load test for the API. No dependencies — plain node.
 *
 * Answers the question "how many people can use this at once", which had never
 * been measured. It drives the READ paths a viewer actually hits (manifest,
 * summaries, portions, page images), because those are what every user loads
 * on every project open and they are served by a single Node process unless
 * API_CLUSTER_WORKERS says otherwise.
 *
 *   node benchmarks/api_load.mjs --url http://localhost:4000 \
 *        --token "$TOKEN" --project "$PROJECT_ID" --users 20 --seconds 30
 *
 * Chat is NOT included by default: every request spends real provider money,
 * so a load test of it is a bill, not a benchmark. --chat opts in and warns.
 *
 * Read the p95, not the mean. The mean hides the requests that make an app
 * feel broken.
 */

const args = Object.fromEntries(
  process.argv.slice(2).flatMap((arg, i, all) =>
    arg.startsWith("--")
      ? [[arg.slice(2), all[i + 1]?.startsWith("--") === false ? all[i + 1] : true]]
      : [],
  ),
);

const BASE = args.url ?? "http://localhost:4000";
const TOKEN = args.token;
const PROJECT = args.project;
const USERS = Number(args.users ?? 10);
const SECONDS = Number(args.seconds ?? 20);

if (!TOKEN || !PROJECT) {
  console.error(
    "need --token and --project.\n" +
      "  token:   POST /auth/login and copy the session token\n" +
      "  project: any project id with a processed document",
  );
  process.exit(1);
}

const paths = [
  `/projects/${PROJECT}/manifest`,
  `/projects/${PROJECT}/summaries`,
  `/projects/${PROJECT}/portions`,
  `/projects/${PROJECT}/summaries/status`,
];
if (args.chat) {
  console.warn("!! --chat: every request spends provider tokens. Keep --seconds small.\n");
}

const samples = [];
const statuses = new Map();
let stop = false;

async function hit(path) {
  const started = performance.now();
  let status = 0;
  try {
    const response = await fetch(`${BASE}${path}`, {
      headers: { authorization: `Bearer ${TOKEN}` },
    });
    status = response.status;
    await response.arrayBuffer(); // include transfer, not just headers
  } catch {
    status = 0; // connection refused / reset — a failure the p95 must not hide
  }
  samples.push({ ms: performance.now() - started, status, path });
  statuses.set(status, (statuses.get(status) ?? 0) + 1);
}

async function user(seed) {
  let i = seed;
  while (!stop) {
    await hit(paths[i++ % paths.length]);
  }
}

const percentile = (sorted, p) =>
  sorted.length ? sorted[Math.min(sorted.length - 1, Math.ceil((p / 100) * sorted.length) - 1)] : 0;

console.log(`${USERS} concurrent users against ${BASE} for ${SECONDS}s\n`);
const began = performance.now();
setTimeout(() => (stop = true), SECONDS * 1000);
await Promise.all(Array.from({ length: USERS }, (_unused, i) => user(i)));
const ran = (performance.now() - began) / 1000;

const ok = samples.filter((s) => s.status >= 200 && s.status < 400);
const sorted = ok.map((s) => s.ms).sort((a, b) => a - b);

console.log(`requests      ${samples.length} in ${ran.toFixed(1)}s`);
console.log(`throughput    ${(samples.length / ran).toFixed(1)} req/s`);

if (ok.length === 0) {
  // Reporting a 0ms p95 over zero successful requests would read as a stellar
  // result. Refuse to print latency at all rather than print a flattering lie.
  console.log(
    `statuses      ${[...statuses].map(([s, n]) => `${s || "failed"}:${n}`).join("  ")}`,
  );
  console.error(
    "\nNo request succeeded — these numbers measure error handling, not the API.\n" +
      "Check --token (a live session token) and --project (a real project id).",
  );
  process.exit(1);
}

console.log(`latency       p50 ${percentile(sorted, 50).toFixed(0)}ms  ` +
            `p95 ${percentile(sorted, 95).toFixed(0)}ms  ` +
            `p99 ${percentile(sorted, 99).toFixed(0)}ms  ` +
            `max ${(sorted.at(-1) ?? 0).toFixed(0)}ms`);
console.log(`statuses      ${[...statuses].map(([s, n]) => `${s || "failed"}:${n}`).join("  ")}`);

if (statuses.get(429)) {
  console.log("\n429s: the rate limiter is doing its job. Raise " +
              "RATE_LIMIT_GENERAL_PER_MINUTE to load-test past it.");
}
if (statuses.get(0)) {
  console.log("\nConnection failures — the API ran out of capacity before the " +
              "latency got bad. That is the number that matters here.");
}

console.log("\nSlowest path:");
const byPath = new Map();
for (const s of ok) (byPath.get(s.path) ?? byPath.set(s.path, []).get(s.path)).push(s.ms);
const ranked = [...byPath].map(([p, ms]) => [p, percentile(ms.sort((a, b) => a - b), 95)]);
for (const [path, p95] of ranked.sort((a, b) => b[1] - a[1])) {
  console.log(`  ${p95.toFixed(0).padStart(6)}ms p95  ${path}`);
}
