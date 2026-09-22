import { Router } from "express";
import { z } from "zod";
import {
  RFI_PRIORITIES,
  RFI_STATUSES,
  type RfiDto,
  type RfiEventDto,
  type RfiLocationDto,
  type RfiPriority,
  type RfiStatus,
} from "@cdip/shared";
import { currentUser } from "../auth.js";
import { prisma } from "../db.js";
import { env } from "../env.js";
import { buildWorkbook, buildWorkbookRows } from "../rfiExport.js";
import { canEditQuestion, canTransition, refusalReason } from "../rfiStatus.js";

/**
 * RFIs — Phase 1: the log itself, written entirely by people.
 *
 * No model writes anything here. An RFI answer is a contractual instruction
 * that someone builds from, so `answer` is human-authored by construction, not
 * by policy that a later commit can relax. Later phases add generated
 * CANDIDATES, which arrive as drafts a person accepts, and they will be
 * distinguishable in the export by their `Source` column.
 *
 * Two invariants this file exists to protect:
 *
 *  1. Numbers are allocated by incrementing `projects.rfiCounter` INSIDE the
 *     create transaction, never from count()+1. Two people pressing "New RFI"
 *     at once both read the same count.
 *  2. Status changes go through `rfiStatus.ts` and nowhere else.
 */
export const rfisRouter = Router({ mergeParams: true });

const projectParam = z.object({ projectId: z.string().uuid() });
const rfiParams = projectParam.extend({ rfiId: z.string().uuid() });
const locationParams = rfiParams.extend({ locationId: z.string().uuid() });

/**
 * A whole log in one response. RFI counts are in the hundreds, not the
 * thousands — unlike pages, which is why this is a normal request and not a
 * queued job. The cap is here so that assumption fails loudly if it stops
 * holding, rather than by timing out a request someone is waiting on.
 */
const EXPORT_MAX = 5000;

const bboxSchema = z.object({
  x: z.number(),
  y: z.number(),
  width: z.number().positive(),
  height: z.number().positive(),
});

const locationBody = z.object({
  documentId: z.string().uuid(),
  pageNumber: z.number().int().positive(),
  bbox: bboxSchema.nullish(),
});

const createBody = z.object({
  subject: z.string().trim().min(1).max(300),
  question: z.string().trim().min(1).max(20000),
  discipline: z.string().trim().max(50).nullish(),
  priority: z.enum(RFI_PRIORITIES as [RfiPriority, ...RfiPriority[]]).default("normal"),
  assignedToId: z.string().uuid().nullish(),
  dueAt: z.coerce.date().nullish(),
  locations: z.array(locationBody).max(50).default([]),
  /** Create straight into the log rather than as a private draft. */
  issue: z.boolean().default(false),
});

const patchBody = z
  .object({
    subject: z.string().trim().min(1).max(300),
    question: z.string().trim().min(1).max(20000),
    discipline: z.string().trim().max(50).nullable(),
    priority: z.enum(RFI_PRIORITIES as [RfiPriority, ...RfiPriority[]]),
    assignedToId: z.string().uuid().nullable(),
    dueAt: z.coerce.date().nullable(),
  })
  .partial();

const statusBody = z.object({
  status: z.enum(RFI_STATUSES as [RfiStatus, ...RfiStatus[]]),
});

const answerBody = z.object({
  answer: z.string().trim().min(1).max(20000),
});

const listQuery = z.object({
  status: z.enum(RFI_STATUSES as [RfiStatus, ...RfiStatus[]]).optional(),
  discipline: z.string().trim().max(50).optional(),
  assignedToId: z.string().uuid().optional(),
});

/** What every read returns — locations joined, events only on the detail read. */
const RFI_INCLUDE = {
  createdBy: { select: { id: true, name: true } },
  assignedTo: { select: { id: true, name: true } },
  answeredBy: { select: { id: true, name: true } },
  locations: {
    orderBy: { createdAt: "asc" },
    include: { document: { select: { filename: true } } },
  },
} as const;

type RfiRow = Awaited<ReturnType<typeof readRfi>>;

async function readRfi(projectId: string, rfiId: string) {
  return prisma.rfi.findFirstOrThrow({
    where: { id: rfiId, projectId },
    include: RFI_INCLUDE,
  });
}

function toLocationDto(loc: {
  id: string;
  documentId: string | null;
  pageNumber: number;
  combinedPageNumber: number | null;
  bbox: unknown;
  sheetNumber: string | null;
  drawingRevised: boolean;
  supersededById: string | null;
  createdAt: Date;
  document: { filename: string } | null;
}): RfiLocationDto {
  return {
    id: loc.id,
    documentId: loc.documentId,
    filename: loc.document?.filename ?? null,
    pageNumber: loc.pageNumber,
    combinedPageNumber: loc.combinedPageNumber,
    bbox: (loc.bbox as RfiLocationDto["bbox"]) ?? null,
    sheetNumber: loc.sheetNumber,
    drawingRevised: loc.drawingRevised,
    supersededById: loc.supersededById,
    createdAt: loc.createdAt.toISOString(),
  };
}

function toDto(rfi: RfiRow, events?: RfiEventDto[]): RfiDto {
  return {
    id: rfi.id,
    projectId: rfi.projectId,
    number: rfi.number,
    subject: rfi.subject,
    question: rfi.question,
    status: rfi.status as RfiStatus,
    priority: rfi.priority as RfiPriority,
    discipline: rfi.discipline,
    dueAt: rfi.dueAt?.toISOString() ?? null,
    createdById: rfi.createdById,
    createdByName: rfi.createdBy?.name ?? null,
    assignedToId: rfi.assignedToId,
    assignedToName: rfi.assignedTo?.name ?? null,
    answer: rfi.answer,
    answeredById: rfi.answeredById,
    answeredByName: rfi.answeredBy?.name ?? null,
    answeredAt: rfi.answeredAt?.toISOString() ?? null,
    closedAt: rfi.closedAt?.toISOString() ?? null,
    createdAt: rfi.createdAt.toISOString(),
    updatedAt: rfi.updatedAt.toISOString(),
    locations: rfi.locations.map(toLocationDto),
    ...(events ? { events } : {}),
  };
}

/**
 * Resolve a pin's page metadata at the moment it is made.
 *
 * `combinedPageNumber` and `sheetNumber` are copied onto the location rather
 * than joined on read, because both can move under it: a re-scrape rewrites
 * sheet numbers, a new upload renumbers the combined sequence, and the
 * document itself may later be deleted. What the RFI needs to keep is where
 * the question was asked WHEN it was asked.
 */
async function pageSnapshot(projectId: string, documentId: string, pageNumber: number) {
  const page = await prisma.page.findFirst({
    where: { pageNumber, document: { id: documentId, projectId } },
    select: { combinedPageNumber: true, sheetNumber: true },
  });
  return {
    combinedPageNumber: page?.combinedPageNumber ?? null,
    sheetNumber: page?.sheetNumber ?? null,
  };
}

/** Append-only; never blocks the action it records. */
async function recordEvent(
  rfiId: string,
  actorId: string | null,
  kind: string,
  detail?: unknown,
) {
  await prisma.rfiEvent
    .create({
      data: { rfiId, actorId, kind, detail: (detail ?? undefined) as never },
    })
    .catch((err) => console.error(`[rfis] audit write failed for ${rfiId} (${kind})`, err));
}

// --- Export -----------------------------------------------------------------
// Declared BEFORE /:rfiId: Express matches in order, and "export.xlsx" would
// otherwise be parsed as an rfiId and rejected as a malformed uuid.

/**
 * The RFI log as a workbook. Two sheets — the log people email, and the
 * evidence that keeps FR-13's chain intact once a claim leaves this app.
 */
rfisRouter.get("/export.xlsx", async (req, res) => {
  const { projectId } = projectParam.parse(req.params);
  const project = await prisma.project.findUniqueOrThrow({
    where: { id: projectId },
    select: { name: true },
  });

  const rows = await prisma.rfi.findMany({
    where: { projectId },
    include: RFI_INCLUDE,
    orderBy: { number: "asc" },
    take: EXPORT_MAX + 1,
  });
  if (rows.length > EXPORT_MAX) {
    return void res.status(413).json({
      error: `this project has more than ${EXPORT_MAX} RFIs, which is past what this endpoint ` +
        `builds in one request — filter the log or raise EXPORT_MAX and move it to a queued job`,
    });
  }

  const names = new Map<string, string>();
  for (const rfi of rows) {
    for (const user of [rfi.createdBy, rfi.assignedTo, rfi.answeredBy]) {
      if (user) names.set(user.id, user.name);
    }
  }

  const { log, evidence } = buildWorkbookRows(
    rows.map((rfi) => toDto(rfi)),
    {
      projectId,
      baseUrl: env.APP_URL,
      nameOf: (id) => (id === null ? null : (names.get(id) ?? null)),
      now: new Date(),
    },
  );

  const workbook = buildWorkbook(log, evidence);

  const filename = `RFI-log-${slug(project.name)}-${new Date().toISOString().slice(0, 10)}.xlsx`;
  res.setHeader(
    "Content-Type",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  );
  res.setHeader("Content-Disposition", `attachment; filename="${filename}"`);
  await workbook.xlsx.write(res);
  res.end();
});

/** A filename component that survives every OS and email client. */
function slug(value: string): string {
  return (
    value
      .replace(/[^A-Za-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 40) || "project"
  );
}

// --- CRUD -------------------------------------------------------------------

rfisRouter.get("/", async (req, res) => {
  const { projectId } = projectParam.parse(req.params);
  const filters = listQuery.parse(req.query);
  await prisma.project.findUniqueOrThrow({ where: { id: projectId } });

  const rfis = await prisma.rfi.findMany({
    where: {
      projectId,
      ...(filters.status ? { status: filters.status } : {}),
      ...(filters.discipline ? { discipline: filters.discipline } : {}),
      ...(filters.assignedToId ? { assignedToId: filters.assignedToId } : {}),
    },
    include: RFI_INCLUDE,
    orderBy: { number: "desc" },
  });
  res.json(rfis.map((rfi) => toDto(rfi)));
});

/**
 * Create. The number is allocated by incrementing the project's counter inside
 * the transaction, which takes a row lock and serializes concurrent creates.
 * `@@unique([projectId, number])` is the backstop: a race that somehow gets
 * past the lock fails the insert rather than issuing a duplicate number, which
 * is the right way round — a number is quoted in correspondence long before
 * anyone would notice it had been handed out twice.
 */
rfisRouter.post("/", async (req, res) => {
  const { projectId } = projectParam.parse(req.params);
  const body = createBody.parse(req.body);
  const actor = currentUser(req);
  await prisma.project.findUniqueOrThrow({ where: { id: projectId } });

  // Snapshots are read outside the transaction: they are reference data, and
  // holding the counter's row lock across these reads would serialize creates
  // behind page lookups for no benefit.
  const locations = await Promise.all(
    body.locations.map(async (loc) => ({
      documentId: loc.documentId,
      pageNumber: loc.pageNumber,
      bbox: (loc.bbox ?? undefined) as never,
      ...(await pageSnapshot(projectId, loc.documentId, loc.pageNumber)),
    })),
  );

  const created = await prisma.$transaction(async (tx) => {
    const project = await tx.project.update({
      where: { id: projectId },
      data: { rfiCounter: { increment: 1 } },
      select: { rfiCounter: true },
    });
    return tx.rfi.create({
      data: {
        projectId,
        number: project.rfiCounter,
        subject: body.subject,
        question: body.question,
        discipline: body.discipline ?? null,
        priority: body.priority,
        status: body.issue ? "open" : "draft",
        assignedToId: body.assignedToId ?? null,
        dueAt: body.dueAt ?? null,
        createdById: actor.id,
        locations: { create: locations },
      },
      include: RFI_INCLUDE,
    });
  });

  await recordEvent(created.id, actor.id, "created", { number: created.number });
  if (body.issue) {
    await recordEvent(created.id, actor.id, "status_changed", { from: "draft", to: "open" });
  }
  console.log(`[rfis] RFI ${created.number} created for project ${projectId.slice(0, 8)}`);
  res.status(201).json(toDto(created));
});

rfisRouter.get("/:rfiId", async (req, res) => {
  const { projectId, rfiId } = rfiParams.parse(req.params);
  const rfi = await readRfi(projectId, rfiId);
  const events = await prisma.rfiEvent.findMany({
    where: { rfiId },
    include: { actor: { select: { name: true } } },
    orderBy: { createdAt: "asc" },
  });
  res.json(
    toDto(
      rfi,
      events.map((e) => ({
        id: e.id,
        kind: e.kind,
        detail: e.detail,
        actorId: e.actorId,
        actorName: e.actor?.name ?? null,
        createdAt: e.createdAt.toISOString(),
      })),
    ),
  );
});

/**
 * Edit. Only while the RFI is still a question: rewriting the question under
 * an existing answer makes the recorded pair a lie, and there is no way to
 * tell afterwards which version was answered.
 */
rfisRouter.patch("/:rfiId", async (req, res) => {
  const { projectId, rfiId } = rfiParams.parse(req.params);
  const body = patchBody.parse(req.body);
  const actor = currentUser(req);
  const existing = await readRfi(projectId, rfiId);

  const rewritesQuestion = body.subject !== undefined || body.question !== undefined;
  if (rewritesQuestion && !canEditQuestion(existing.status as RfiStatus)) {
    return void res.status(409).json({
      error: `the question of an RFI that is ${existing.status} cannot be rewritten — ` +
        "it would no longer match the answer on record",
    });
  }

  const updated = await prisma.rfi.update({
    where: { id: rfiId },
    data: {
      ...(body.subject !== undefined ? { subject: body.subject } : {}),
      ...(body.question !== undefined ? { question: body.question } : {}),
      ...(body.discipline !== undefined ? { discipline: body.discipline } : {}),
      ...(body.priority !== undefined ? { priority: body.priority } : {}),
      ...(body.assignedToId !== undefined ? { assignedToId: body.assignedToId } : {}),
      ...(body.dueAt !== undefined ? { dueAt: body.dueAt } : {}),
    },
    include: RFI_INCLUDE,
  });
  await recordEvent(rfiId, actor.id, "updated", { fields: Object.keys(body) });
  res.json(toDto(updated));
});

/**
 * Every status change except answering, which carries content and has its own
 * route. The legality question is asked in exactly one place.
 */
rfisRouter.post("/:rfiId/status", async (req, res) => {
  const { projectId, rfiId } = rfiParams.parse(req.params);
  const { status } = statusBody.parse(req.body);
  const actor = currentUser(req);
  const existing = await readRfi(projectId, rfiId);
  const from = existing.status as RfiStatus;

  if (status === "answered") {
    return void res.status(400).json({
      error: "answering an RFI records an answer — use POST /rfis/:rfiId/answer",
    });
  }
  if (!canTransition(from, status)) {
    return void res.status(409).json({ error: refusalReason(from, status) });
  }

  const updated = await prisma.rfi.update({
    where: { id: rfiId },
    data: {
      status,
      closedAt: status === "closed" ? new Date() : null,
    },
    include: RFI_INCLUDE,
  });
  await recordEvent(rfiId, actor.id, from === "answered" && status === "open" ? "reopened" : "status_changed", {
    from,
    to: status,
  });
  res.json(toDto(updated));
});

/**
 * Record the answer.
 *
 * Human-authored, always. This is the one endpoint in the RFI surface that no
 * generated content may ever reach: an RFI response is a contractual
 * instruction, and a wrong one gets built from. Later phases may draft the
 * question and cite supporting chunks; neither writes here.
 */
rfisRouter.post("/:rfiId/answer", async (req, res) => {
  const { projectId, rfiId } = rfiParams.parse(req.params);
  const { answer } = answerBody.parse(req.body);
  const actor = currentUser(req);
  const existing = await readRfi(projectId, rfiId);
  const from = existing.status as RfiStatus;

  if (!canTransition(from, "answered")) {
    return void res.status(409).json({ error: refusalReason(from, "answered") });
  }

  const updated = await prisma.rfi.update({
    where: { id: rfiId },
    data: {
      answer,
      status: "answered",
      answeredById: actor.id,
      answeredAt: new Date(),
    },
    include: RFI_INCLUDE,
  });
  await recordEvent(rfiId, actor.id, "answered", { from });
  console.log(`[rfis] RFI ${updated.number} answered by ${actor.id.slice(0, 8)}`);
  res.json(toDto(updated));
});

// --- Locations --------------------------------------------------------------

rfisRouter.post("/:rfiId/locations", async (req, res) => {
  const { projectId, rfiId } = rfiParams.parse(req.params);
  const body = locationBody.parse(req.body);
  const actor = currentUser(req);
  await readRfi(projectId, rfiId);

  // The document must belong to THIS project: the id comes from the client,
  // and requireProjectMember guards the project in the path, not one named in
  // a body. Without this check a member of project A could pin an RFI to a
  // page of project B and read its sheet number back out of the export.
  const document = await prisma.document.findFirst({
    where: { id: body.documentId, projectId },
    select: { id: true },
  });
  if (!document) {
    return void res.status(404).json({ error: "document not found in this project" });
  }

  await prisma.rfiLocation.create({
    data: {
      rfiId,
      documentId: body.documentId,
      pageNumber: body.pageNumber,
      bbox: (body.bbox ?? undefined) as never,
      ...(await pageSnapshot(projectId, body.documentId, body.pageNumber)),
    },
  });
  await recordEvent(rfiId, actor.id, "location_added", {
    documentId: body.documentId,
    pageNumber: body.pageNumber,
  });
  res.status(201).json(toDto(await readRfi(projectId, rfiId)));
});

rfisRouter.delete("/:rfiId/locations/:locationId", async (req, res) => {
  const { projectId, rfiId, locationId } = locationParams.parse(req.params);
  const actor = currentUser(req);
  await readRfi(projectId, rfiId);

  const deleted = await prisma.rfiLocation.deleteMany({ where: { id: locationId, rfiId } });
  if (deleted.count === 0) {
    return void res.status(404).json({ error: "location not found on this RFI" });
  }
  await recordEvent(rfiId, actor.id, "location_removed", { locationId });
  res.json(toDto(await readRfi(projectId, rfiId)));
});
