/**
 * What named RFIs really cost and found, side by side — the measurement for
 * "read the sheets once per RFI" (rfi_review._discover_together):
 *
 *   npx tsx apps/api/src/rfiSetCompare.ts <setId> [<setId> …]
 *
 * Per set: each element's newest review (status, model calls by stage, tokens,
 * cost priced from usage_events exactly as the dashboard prices them) and the
 * findings it saved or found again. Run the same RFI once with
 * RFI_SHARED_SET_READING=false on the worker and once without, then compare.
 * Reads the database only; spends nothing.
 */
import { prisma } from "./db.js";
import { estimateCostUsd } from "./usage.js";

type Stage = { calls: number; input: number; output: number; usd: number };

async function compare(setId: string) {
  const set = await prisma.rfiSet.findUniqueOrThrow({ where: { id: setId } });
  const runs = await prisma.rfiReviewRun.findMany({ where: { setId, status: { not: "cancelled" } }, orderBy: { createdAt: "desc" } });
  const newest = new Map<string, (typeof runs)[number]>();
  for (const r of runs) {
    const element = (r.scope as { library?: { element?: { name?: string } } } | null)?.library?.element?.name ?? r.id;
    if (!newest.has(element)) newest.set(element, r);
  }
  const events = await prisma.usageEvent.findMany({ where: { reviewRunId: { in: [...newest.values()].map((r) => r.id) } } });
  const candidates = await prisma.rfiCandidate.findMany({
    where: { reviewRunId: { in: [...newest.values()].map((r) => r.id) } },
    select: { reviewRunId: true, checkType: true, confidence: true, subject: true, status: true },
  });
  console.log(`\n=== ${set.name} (${setId}) — ${newest.size} element(s)`);
  const total: Stage = { calls: 0, input: 0, output: 0, usd: 0 };
  for (const [element, r] of newest) {
    const stages = new Map<string, Stage>();
    for (const e of events.filter((e) => e.reviewRunId === r.id)) {
      const s = stages.get(e.stage ?? "?") ?? { calls: 0, input: 0, output: 0, usd: 0 };
      s.calls += 1;
      s.input += e.inputTokens;
      s.output += e.outputTokens;
      s.usd += estimateCostUsd(e);
      stages.set(e.stage ?? "?", s);
    }
    const sum = [...stages.values()].reduce((a, s) => ({ calls: a.calls + s.calls, input: a.input + s.input, output: a.output + s.output, usd: a.usd + s.usd }), { calls: 0, input: 0, output: 0, usd: 0 });
    total.calls += sum.calls;
    total.input += sum.input;
    total.output += sum.output;
    total.usd += sum.usd;
    const found = candidates.filter((c) => c.reviewRunId === r.id);
    const by = [...stages].map(([k, s]) => `${k} ${s.calls}`).join(", ") || "no calls";
    console.log(`- ${element}: ${r.status}, ${by}; ${sum.input.toLocaleString()} in / ${sum.output.toLocaleString()} out, $${sum.usd.toFixed(3)}; ${found.length} finding(s)`);
    for (const c of found) console.log(`    [${c.confidence}] ${c.checkType}: ${c.subject}`);
    const again = Object.values((r.checkResults ?? {}) as Record<string, { foundAgain?: string[] }>).flatMap((v) => v.foundAgain ?? []);
    if (again.length) console.log(`    + ${new Set(again).size} found again (already on file)`);
  }
  console.log(`TOTAL: ${total.calls} model call(s), ${total.input.toLocaleString()} in / ${total.output.toLocaleString()} out, $${total.usd.toFixed(3)}`);
  return total;
}

const ids = process.argv.slice(2);
if (!ids.length) {
  console.error("usage: npx tsx apps/api/src/rfiSetCompare.ts <setId> [<setId> …]");
  process.exit(1);
}
const totals = [];
for (const id of ids) totals.push(await compare(id));
if (totals.length === 2 && totals[0]!.usd > 0) {
  console.log(`\nSecond vs first: ${((totals[1]!.usd / totals[0]!.usd) * 100).toFixed(0)}% of the cost, ${totals[1]!.calls} vs ${totals[0]!.calls} calls.`);
}
await prisma.$disconnect();
