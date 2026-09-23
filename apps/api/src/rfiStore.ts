import type { Prisma } from "@prisma/client";
import type {
  RfiCandidateDto,
  RfiCandidateStatus,
  RfiConfidence,
  RfiDto,
  RfiEventDto,
  RfiEvidenceDto,
  RfiLocationDto,
  RfiPriority,
  RfiStatus,
} from "@cdip/shared";
import { prisma } from "./db.js";

/**
 * Everything that WRITES an RFI goes through here: typed by a person
 * (routes/rfis.ts) and accepted from a generated candidate
 * (routes/rfiGenerated.ts). Two routers creating RFIs their own way would be
 * two number allocators and two ideas of what a valid pin is — and the first
 * version of this code already had exactly that drift: the add-location route
 * checked that a pinned document belonged to the project, and the create route
 * did not.
 */

/** What every read returns — locations joined, events only on the detail read. */
export const RFI_INCLUDE = {
  createdBy: { select: { id: true, name: true } },
  assignedTo: { select: { id: true, name: true } },
  answeredBy: { select: { id: true, name: true } },
  locations: {
    orderBy: { createdAt: "asc" },
    include: { document: { select: { filename: true } } },
  },
} as const;

export type RfiRow = Prisma.RfiGetPayload<{ include: typeof RFI_INCLUDE }>;

export function toLocationDto(loc: RfiRow["locations"][number]): RfiLocationDto {
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

export function toDto(rfi: RfiRow, events?: RfiEventDto[]): RfiDto {
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
    source: rfi.source,
    checkType: rfi.checkType,
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

type CandidateRow = Prisma.RfiCandidateGetPayload<Record<string, never>>;

export function toCandidateDto(row: CandidateRow): RfiCandidateDto {
  return {
    id: row.id,
    checkType: row.checkType,
    confidence: row.confidence as RfiConfidence,
    subject: row.subject,
    question: row.question,
    questionSource: row.questionSource,
    evidence: (Array.isArray(row.evidence) ? row.evidence : []) as unknown as RfiEvidenceDto[],
    status: row.status as RfiCandidateStatus,
    rfiId: row.rfiId,
    createdAt: row.createdAt.toISOString(),
  };
}

/** A pin as a caller asks for it. */
export interface PinRequest {
  documentId: string;
  pageNumber: number;
  bbox?: { x: number; y: number; width: number; height: number } | null;
}

/** A pin ready to insert, with its page snapshots taken. */
export interface ResolvedPin {
  documentId: string;
  pageNumber: number;
  bbox: Prisma.InputJsonValue | undefined;
  combinedPageNumber: number | null;
  sheetNumber: string | null;
}

/** Thrown when a pin names a document outside the project in the path. */
export class ForeignDocumentError extends Error {}

/**
 * Check every pin belongs to THIS project, and snapshot its page.
 *
 * The document id comes from the client, and requireProjectMember guards the
 * project in the path — not one named in a body. Without this a member of
 * project A could pin an RFI to a page of project B and read that document's
 * filename back out of the RFI and its export.
 *
 * `combinedPageNumber` and `sheetNumber` are copied rather than joined,
 * because both can move under the pin: a re-scrape rewrites sheet numbers and
 * a new upload renumbers the combined sequence. What the RFI keeps is where
 * the question was asked WHEN it was asked. The discipline of the first pin
 * is returned too, so a generated RFI can be filed under its sheet's
 * discipline without anyone typing it.
 */
export async function resolvePins(
  projectId: string,
  pins: PinRequest[],
  options: { dropForeign?: boolean } = {},
): Promise<{ pins: ResolvedPin[]; discipline: string | null }> {
  if (pins.length === 0) return { pins: [], discipline: null };
  const documentIds = [...new Set(pins.map((p) => p.documentId))];
  const owned = new Set(
    (
      await prisma.document.findMany({
        where: { id: { in: documentIds }, projectId },
        select: { id: true },
      })
    ).map((d) => d.id),
  );
  const foreign = documentIds.filter((id) => !owned.has(id));
  if (foreign.length > 0 && !options.dropForeign) {
    throw new ForeignDocumentError("document not found in this project");
  }

  const kept = pins.filter((p) => owned.has(p.documentId));
  const pages = await prisma.page.findMany({
    where: {
      OR: kept.map((p) => ({ documentId: p.documentId, pageNumber: p.pageNumber })),
    },
    select: {
      documentId: true,
      pageNumber: true,
      combinedPageNumber: true,
      sheetNumber: true,
      discipline: true,
    },
  });
  const byKey = new Map(pages.map((p) => [`${p.documentId}:${p.pageNumber}`, p]));

  const resolved = kept.map((pin) => {
    const page = byKey.get(`${pin.documentId}:${pin.pageNumber}`);
    return {
      documentId: pin.documentId,
      pageNumber: pin.pageNumber,
      bbox: (pin.bbox ?? undefined) as Prisma.InputJsonValue | undefined,
      combinedPageNumber: page?.combinedPageNumber ?? null,
      sheetNumber: page?.sheetNumber ?? null,
    };
  });
  const first = kept[0] && byKey.get(`${kept[0].documentId}:${kept[0].pageNumber}`);
  return { pins: resolved, discipline: first?.discipline ?? null };
}

export interface NewRfi {
  subject: string;
  question: string;
  status: "draft" | "open";
  priority?: RfiPriority;
  discipline?: string | null;
  assignedToId?: string | null;
  dueAt?: Date | null;
  createdById: string;
  source?: "manual" | "generated";
  checkType?: string | null;
  pins: ResolvedPin[];
}

/**
 * Allocate the next number and create the RFI, inside the caller's
 * transaction.
 *
 * The increment takes the project row's lock, so concurrent creates serialize
 * on it rather than both reading the same count. `@@unique([projectId,
 * number])` is the backstop: a race that somehow got past the lock fails the
 * insert instead of issuing a duplicate — the right way round, because a
 * number is quoted in correspondence long before anyone would notice it had
 * been handed out twice.
 */
export async function createRfi(
  tx: Prisma.TransactionClient,
  projectId: string,
  rfi: NewRfi,
): Promise<RfiRow> {
  const project = await tx.project.update({
    where: { id: projectId },
    data: { rfiCounter: { increment: 1 } },
    select: { rfiCounter: true },
  });
  return tx.rfi.create({
    data: {
      projectId,
      number: project.rfiCounter,
      subject: rfi.subject,
      question: rfi.question,
      discipline: rfi.discipline ?? null,
      priority: rfi.priority ?? "normal",
      status: rfi.status,
      assignedToId: rfi.assignedToId ?? null,
      dueAt: rfi.dueAt ?? null,
      createdById: rfi.createdById,
      source: rfi.source ?? "manual",
      checkType: rfi.checkType ?? null,
      locations: { create: rfi.pins },
    },
    include: RFI_INCLUDE,
  });
}

/** Append-only; never blocks the action it records. */
export async function recordEvent(
  rfiId: string,
  actorId: string | null,
  kind: string,
  detail?: unknown,
): Promise<void> {
  await prisma.rfiEvent
    .create({ data: { rfiId, actorId, kind, detail: (detail ?? undefined) as never } })
    .catch((err) => console.error(`[rfis] audit write failed for ${rfiId} (${kind})`, err));
}
