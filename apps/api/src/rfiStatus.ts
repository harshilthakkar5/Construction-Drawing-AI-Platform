import type { RfiStatus } from "@cdip/shared";

/**
 * The RFI lifecycle, in one place.
 *
 * Every rule about which status may follow which lives here rather than as
 * `if`s spread across the handlers, because the handlers are where these rules
 * get quietly contradicted: "close" and "answer" and "edit" each have an
 * opinion about the current status, and three opinions in three files drift.
 * It is a pure module so the rules can be tested without a database.
 *
 *   draft ──▶ open ──▶ answered ──▶ closed
 *     │         │          │           │
 *     └─────────┴──────────┴──▶ voided ┘   (withdraw, from anywhere but closed)
 *                          ▲
 *              reopened ───┘  (answered ──▶ open)
 *
 * `closed` is terminal. That is the one rule worth stating out loud: a closed
 * RFI is a finished contractual exchange, and the correct way to revisit it is
 * a NEW RFI that references it, not an edit to a record other people have
 * already quoted and built from.
 */
const TRANSITIONS: Record<RfiStatus, readonly RfiStatus[]> = {
  draft: ["open", "voided"],
  open: ["answered", "voided"],
  answered: ["open", "closed", "voided"],
  closed: [],
  voided: [],
};

/** Statuses whose subject/question may still be edited. */
const EDITABLE: readonly RfiStatus[] = ["draft", "open"];

export function canTransition(from: RfiStatus, to: RfiStatus): boolean {
  return TRANSITIONS[from].includes(to);
}

export function allowedTransitions(from: RfiStatus): readonly RfiStatus[] {
  return TRANSITIONS[from];
}

/**
 * Why a transition was refused, phrased for the person who tried it. A 409
 * reading "cannot change status" tells them nothing; this names what they can
 * do instead, and says plainly that a closed RFI is finished rather than
 * broken.
 */
export function refusalReason(from: RfiStatus, to: RfiStatus): string | null {
  if (canTransition(from, to)) return null;
  if (from === to) return `this RFI is already ${from}`;
  if (from === "closed") {
    return "this RFI is closed — a closed RFI is a finished exchange others have " +
      "already quoted, so revisit it by raising a new RFI that references this number";
  }
  if (from === "voided") return "this RFI was voided; its number stays reserved and is never reused";
  const allowed = TRANSITIONS[from];
  return allowed.length === 0
    ? `an RFI that is ${from} cannot change status`
    : `an RFI that is ${from} can only become ${allowed.join(" or ")}, not ${to}`;
}

/** Whether the question itself may still be rewritten. */
export function canEditQuestion(status: RfiStatus): boolean {
  return EDITABLE.includes(status);
}

/**
 * Whose move it is — the column every RFI log has and no schema stores,
 * because it is derived rather than set. An RFI waiting for an answer sits
 * with its assignee; once answered it goes back to whoever asked, to accept or
 * reopen; once closed or voided it sits with nobody.
 *
 * Returns the user id to chase, or null when the RFI is nobody's move.
 */
export function ballInCourt(rfi: {
  status: RfiStatus;
  createdById: string | null;
  assignedToId: string | null;
}): string | null {
  switch (rfi.status) {
    case "draft":
      // Still the author's: nobody else can see a draft to act on it.
      return rfi.createdById;
    case "open":
      // Unassigned and open is a real state and a common one — the RFI has
      // been issued but nobody has picked it up. Reporting the author here
      // would hide that, so it reports nobody and the log shows the gap.
      return rfi.assignedToId;
    case "answered":
      return rfi.createdById;
    case "closed":
    case "voided":
      return null;
  }
}

/**
 * Overdue is a question about a MOMENT, so the moment is a parameter. Reading
 * the clock inside would make every caller's test depend on the day it runs.
 *
 * Only an RFI still waiting on an answer can be overdue: once it is answered
 * the due date has been met or missed already, and continuing to flag it red
 * trains people to ignore the colour.
 */
export function isOverdue(
  rfi: { status: RfiStatus; dueAt: Date | null },
  now: Date,
): boolean {
  if (rfi.dueAt === null) return false;
  if (rfi.status !== "open" && rfi.status !== "draft") return false;
  return rfi.dueAt.getTime() < now.getTime();
}
