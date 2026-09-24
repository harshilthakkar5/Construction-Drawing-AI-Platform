import { Router } from "express";
import { z } from "zod";
import {
  RFI_CANDIDATE_STATUSES,
  RFI_CONFIDENCES,
  type RfiCandidateStatus,
  type RfiConfidence,
  type RfiEvidenceDto,
  type RfiUsageTotalsDto,
} from "@cdip/shared";
import { currentUser } from "../auth.js";
import { prisma } from "../db.js";
import { rfiScanQueue } from "../queues.js";
import { summaryLimiter } from "../rateLimit.js";
import { meetsConfidence, scanIsActive, toScanDto } from "../rfiScanRules.js";
import { estimateCostUsd } from "../usage.js";
import { createRfi, recordEvent, resolvePins, toCandidateDto, toDto } from "../rfiStore.js";

/**
 * Generated RFIs: scan the drawings, review what was found, keep what is real.
 *
 * The scan runs in the worker (workers/src/rfi_scan.py) — deterministic checks
 * decide what is missing and a model only words each finding. What lands here
 * is a CANDIDATE: no number, not issued, not in the log. A person accepts it
 * (it becomes a numbered, open RFI pinned where the finding is) or dismisses
 * it (and a re-scan will not propose it again).
 *
 * That review step is kept on purpose even though the goal is "the system
 * writes the RFIs". The checks are built for precision, but a check cannot
 * know that a sheet was left out of the upload deliberately or that a TBD is
 * already being handled — and an RFI number, once issued, is quoted in
 * correspondence. "Accept all high-confidence" is one click for the case where
 * the reviewer trusts the scan.
 */
export const rfiGeneratedRouter = Router({ mergeParams: true });

const projectParam = z.object({ projectId: z.string().uuid() });
const candidateParams = projectParam.extend({ candidateId: z.string().uuid() });

const listQuery = z.object({
  status: z
    .enum(RFI_CANDIDATE_STATUSES as [RfiCandidateStatus, ...RfiCandidateStatus[]])
    .default("pending"),
});
const acceptAllBody = z.object({
  minConfidence: z.enum(RFI_CONFIDENCES as [RfiConfidence, ...RfiConfidence[]]).default("high"),
});

/** Bounds one "Accept all" request; the scan itself caps each check at 100. */
const ACCEPT_ALL_MAX = 300;

/** Thrown inside a transaction to abandon it with a 409. */
class NotPending extends Error {}

// --- Scanning ---------------------------------------------------------------

rfiGeneratedRouter.get("/scan", async (req, res) => {
  const { projectId } = projectParam.parse(req.params);
  const latest = await prisma.rfiScan.findFirst({
    where: { projectId },
    orderBy: { createdAt: "desc" },
  });
  res.json(latest ? toScanDto(latest, estimateCostUsd) : null);
});

/**
 * Everything this project has spent on RFI wording, across every scan —
 * read from usage_events, the same rows the dashboard prices, so the two
 * figures cannot disagree. Thinking is inside outputTokens here: usage_events
 * has no separate column for it (both vendors bill it as output). The
 * per-scan figure, which does split it out where the provider reports it,
 * rides on GET /scan.
 */
rfiGeneratedRouter.get("/usage", async (req, res) => {
  const { projectId } = projectParam.parse(req.params);
  const rows = await prisma.usageEvent.groupBy({
    by: ["model"],
    where: { projectId, kind: "rfi" },
    _count: { _all: true },
    _sum: { inputTokens: true, outputTokens: true, cacheReadTokens: true, cacheWriteTokens: true },
  });
  const byModel = rows.map((r) => {
    const tokens = {
      model: r.model,
      inputTokens: r._sum.inputTokens ?? 0,
      outputTokens: r._sum.outputTokens ?? 0,
      cacheReadTokens: r._sum.cacheReadTokens ?? 0,
      cacheWriteTokens: r._sum.cacheWriteTokens ?? 0,
    };
    return { ...tokens, calls: r._count._all, costUsd: estimateCostUsd(tokens) };
  });
  const sum = (key: "calls" | "inputTokens" | "outputTokens" | "cacheReadTokens" | "cacheWriteTokens" | "costUsd") =>
    byModel.reduce((total, m) => total + m[key], 0);
  const totals: RfiUsageTotalsDto = {
    calls: sum("calls"),
    inputTokens: sum("inputTokens"),
    outputTokens: sum("outputTokens"),
    cacheReadTokens: sum("cacheReadTokens"),
    cacheWriteTokens: sum("cacheWriteTokens"),
    costUsd: sum("costUsd"),
    byModel: byModel.map(({ model, calls, inputTokens, outputTokens, costUsd }) => ({
      model,
      calls,
      inputTokens,
      outputTokens,
      costUsd,
    })),
  };
  res.json(totals);
});

/**
 * Start a scan. Refuses a second while one is live, because two scans of one
 * project would race to upsert the same fingerprints — harmless to the data
 * (the upsert is idempotent) but a waste of the wording calls. A scan stuck
 * past STALE_SCAN_MS does not block: see scanIsActive.
 */
rfiGeneratedRouter.post("/scan", summaryLimiter, async (req, res) => {
  const { projectId } = projectParam.parse(req.params);
  const actor = currentUser(req);

  const documents = await prisma.document.count({
    where: { projectId, supersededAt: null, status: "completed" },
  });
  if (documents === 0) {
    return void res.status(409).json({
      error: "no processed drawings in this project yet — upload them and wait for processing to finish",
    });
  }

  const latest = await prisma.rfiScan.findFirst({
    where: { projectId },
    orderBy: { createdAt: "desc" },
  });
  if (scanIsActive(latest, new Date())) {
    return void res.status(409).json({ error: `a scan is already ${latest!.status}` });
  }

  const scan = await prisma.rfiScan.create({
    data: { projectId, requestedById: actor.id },
  });
  const job = await rfiScanQueue.add("scan", { projectId, scanId: scan.id });
  const updated = await prisma.rfiScan.update({
    where: { id: scan.id },
    data: { jobId: job.id ?? null },
  });
  console.log(`[rfis] scan ${scan.id.slice(0, 8)} queued for project ${projectId.slice(0, 8)}`);
  res.status(202).json(toScanDto(updated, estimateCostUsd));
});

// --- Reviewing --------------------------------------------------------------

rfiGeneratedRouter.get("/", async (req, res) => {
  const { projectId } = projectParam.parse(req.params);
  const { status } = listQuery.parse(req.query);
  const [rows, counts] = await Promise.all([
    prisma.rfiCandidate.findMany({
      where: { projectId, status },
      orderBy: [{ confidence: "asc" }, { createdAt: "asc" }],
    }),
    prisma.rfiCandidate.groupBy({
      by: ["status"],
      where: { projectId },
      _count: { _all: true },
    }),
  ]);
  res.json({
    candidates: rows.map(toCandidateDto),
    counts: Object.fromEntries(counts.map((c) => [c.status, c._count._all])),
  });
});

/**
 * Keep a finding: it becomes a numbered, OPEN RFI, pinned where the finding
 * is — page and bbox, never the chunk id, which the next ingest re-mints.
 *
 * The candidate is CLAIMED first with a conditional update (pending →
 * accepted) inside the same transaction as the number allocation. Two people
 * pressing Accept on one finding at once therefore produce one RFI and one
 * 409, never two RFIs for one gap — the second update matches no row because
 * the first already moved it off `pending`.
 */
async function accept(projectId: string, candidateId: string, actorId: string) {
  const candidate = await prisma.rfiCandidate.findFirstOrThrow({
    where: { id: candidateId, projectId },
  });
  const evidence = (Array.isArray(candidate.evidence) ? candidate.evidence : []) as unknown as RfiEvidenceDto[];
  // A document deleted since the scan drops its pins rather than failing the
  // accept: the RFI is still true, it just points at fewer places.
  const { pins, discipline } = await resolvePins(
    projectId,
    evidence.map((e) => ({ documentId: e.documentId, pageNumber: e.pageNumber, bbox: e.bbox })),
    { dropForeign: true },
  );

  const rfi = await prisma.$transaction(async (tx) => {
    const claimed = await tx.rfiCandidate.updateMany({
      where: { id: candidateId, projectId, status: "pending" },
      data: { status: "accepted", decidedById: actorId, decidedAt: new Date() },
    });
    if (claimed.count === 0) throw new NotPending();
    const created = await createRfi(tx, projectId, {
      subject: candidate.subject,
      question: candidate.question,
      status: "open",
      discipline,
      createdById: actorId,
      source: "generated",
      checkType: candidate.checkType,
      pins,
    });
    await tx.rfiCandidate.update({ where: { id: candidateId }, data: { rfiId: created.id } });
    return created;
  });

  await recordEvent(rfi.id, actorId, "created", {
    number: rfi.number,
    source: "generated",
    checkType: candidate.checkType,
    candidateId,
    questionSource: candidate.questionSource,
  });
  return rfi;
}

rfiGeneratedRouter.post("/accept-all", async (req, res) => {
  const { projectId } = projectParam.parse(req.params);
  const { minConfidence } = acceptAllBody.parse(req.body ?? {});
  const actor = currentUser(req);

  const pending = await prisma.rfiCandidate.findMany({
    where: { projectId, status: "pending" },
    orderBy: [{ confidence: "asc" }, { createdAt: "asc" }],
    take: ACCEPT_ALL_MAX,
    select: { id: true, confidence: true },
  });
  const chosen = pending.filter((c) => meetsConfidence(c.confidence as RfiConfidence, minConfidence));

  // One transaction per candidate, so one that someone else already decided
  // is skipped rather than rolling back every other accept in the batch.
  const accepted = [];
  let skipped = 0;
  for (const { id } of chosen) {
    try {
      accepted.push(await accept(projectId, id, actor.id));
    } catch (err) {
      if (err instanceof NotPending) {
        skipped++;
        continue;
      }
      throw err;
    }
  }
  res.json({ accepted: accepted.map((r) => toDto(r)), skipped });
});

rfiGeneratedRouter.post("/:candidateId/accept", async (req, res) => {
  const { projectId, candidateId } = candidateParams.parse(req.params);
  const actor = currentUser(req);
  try {
    const rfi = await accept(projectId, candidateId, actor.id);
    res.status(201).json(toDto(rfi));
  } catch (err) {
    if (err instanceof NotPending) {
      return void res.status(409).json({ error: "this finding has already been accepted or dismissed" });
    }
    throw err;
  }
});

/** Not an RFI. Remembered, so the next scan does not propose it again. */
rfiGeneratedRouter.post("/:candidateId/dismiss", async (req, res) => {
  const { projectId, candidateId } = candidateParams.parse(req.params);
  const actor = currentUser(req);
  const done = await prisma.rfiCandidate.updateMany({
    where: { id: candidateId, projectId, status: "pending" },
    data: { status: "dismissed", decidedById: actor.id, decidedAt: new Date() },
  });
  if (done.count === 0) {
    return void res.status(409).json({ error: "this finding has already been accepted or dismissed" });
  }
  res.json({ dismissed: true });
});

/** Undo a dismissal. An accepted finding cannot come back: it has a number. */
rfiGeneratedRouter.post("/:candidateId/restore", async (req, res) => {
  const { projectId, candidateId } = candidateParams.parse(req.params);
  const done = await prisma.rfiCandidate.updateMany({
    where: { id: candidateId, projectId, status: "dismissed" },
    data: { status: "pending", decidedById: null, decidedAt: null },
  });
  if (done.count === 0) {
    return void res.status(409).json({ error: "only a dismissed finding can be restored" });
  }
  res.json({ restored: true });
});
