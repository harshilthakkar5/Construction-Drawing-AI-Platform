import { describe, expect, it } from "vitest";
import { RFI_STATUSES, type RfiStatus } from "@cdip/shared";
import {
  allowedTransitions,
  ballInCourt,
  canEditQuestion,
  canTransition,
  isOverdue,
  refusalReason,
} from "./rfiStatus.js";

describe("canTransition", () => {
  it("walks the normal path draft → open → answered → closed", () => {
    expect(canTransition("draft", "open")).toBe(true);
    expect(canTransition("open", "answered")).toBe(true);
    expect(canTransition("answered", "closed")).toBe(true);
  });

  it("lets an answered RFI be reopened", () => {
    expect(canTransition("answered", "open")).toBe(true);
  });

  /**
   * The rule most likely to be "helpfully" relaxed later. A closed RFI is a
   * finished contractual exchange that other people have quoted and built
   * from; editing one rewrites a record rather than adding to it.
   */
  it("makes closed terminal", () => {
    for (const to of RFI_STATUSES) {
      expect(canTransition("closed", to), `closed → ${to} must be refused`).toBe(false);
    }
    expect(allowedTransitions("closed")).toEqual([]);
  });

  it("makes voided terminal", () => {
    for (const to of RFI_STATUSES) {
      expect(canTransition("voided", to), `voided → ${to} must be refused`).toBe(false);
    }
  });

  it("refuses skipping the answer: open cannot go straight to closed", () => {
    expect(canTransition("open", "closed")).toBe(false);
  });

  it("refuses a no-op transition to the same status", () => {
    for (const status of RFI_STATUSES) {
      expect(canTransition(status, status), `${status} → ${status}`).toBe(false);
    }
  });

  /**
   * Every status must be a key, not just the ones a test happens to name — a
   * status added to the enum and not to the table would otherwise throw
   * `undefined.includes` from inside a handler at runtime.
   */
  it("has a rule for every status in the shared vocabulary", () => {
    for (const status of RFI_STATUSES) {
      expect(() => allowedTransitions(status), `no rule for ${status}`).not.toThrow();
      expect(Array.isArray(allowedTransitions(status))).toBe(true);
    }
  });

  it("only ever allows transitions to statuses that exist", () => {
    for (const status of RFI_STATUSES) {
      for (const to of allowedTransitions(status)) {
        expect(RFI_STATUSES, `${status} → ${to} names an unknown status`).toContain(to);
      }
    }
  });

  it("can withdraw from any non-terminal status", () => {
    for (const status of ["draft", "open", "answered"] as RfiStatus[]) {
      expect(canTransition(status, "voided"), `${status} → voided`).toBe(true);
    }
  });
});

describe("refusalReason", () => {
  it("says nothing when the transition is legal", () => {
    expect(refusalReason("draft", "open")).toBeNull();
  });

  it("explains a closed RFI by naming what to do instead", () => {
    const reason = refusalReason("closed", "open");
    expect(reason).toContain("new RFI");
  });

  it("names the statuses that ARE reachable", () => {
    const reason = refusalReason("open", "closed");
    // The value of the message is the alternative, not the complaint.
    expect(reason).toContain("answered");
  });

  it("calls a no-op what it is rather than listing alternatives", () => {
    expect(refusalReason("open", "open")).toBe("this RFI is already open");
  });

  it("says a voided number stays reserved", () => {
    expect(refusalReason("voided", "open")).toContain("never reused");
  });

  it("produces a reason for every illegal pair", () => {
    for (const from of RFI_STATUSES) {
      for (const to of RFI_STATUSES) {
        if (canTransition(from, to)) continue;
        const reason = refusalReason(from, to);
        expect(reason, `${from} → ${to} refused with no reason`).toBeTruthy();
        expect(reason!.length).toBeGreaterThan(10);
      }
    }
  });
});

describe("canEditQuestion", () => {
  it("allows edits while the RFI is still a question", () => {
    expect(canEditQuestion("draft")).toBe(true);
    expect(canEditQuestion("open")).toBe(true);
  });

  it("refuses edits once the question has been answered", () => {
    // Rewriting the question under an existing answer makes the pair a lie.
    expect(canEditQuestion("answered")).toBe(false);
    expect(canEditQuestion("closed")).toBe(false);
    expect(canEditQuestion("voided")).toBe(false);
  });
});

describe("ballInCourt", () => {
  const ids = { createdById: "author", assignedToId: "engineer" };

  it("sits with the author while it is a draft", () => {
    expect(ballInCourt({ status: "draft", ...ids })).toBe("author");
  });

  it("sits with the assignee once it is open", () => {
    expect(ballInCourt({ status: "open", ...ids })).toBe("engineer");
  });

  /**
   * An issued-but-unassigned RFI is a real state and the one worth surfacing:
   * nobody has picked it up. Falling back to the author here would fill the
   * column with a plausible name and hide the gap.
   */
  it("reports nobody for an open RFI with no assignee", () => {
    expect(ballInCourt({ status: "open", createdById: "author", assignedToId: null })).toBeNull();
  });

  it("returns to the author once answered, to accept or reopen", () => {
    expect(ballInCourt({ status: "answered", ...ids })).toBe("author");
  });

  it("sits with nobody once closed or voided", () => {
    expect(ballInCourt({ status: "closed", ...ids })).toBeNull();
    expect(ballInCourt({ status: "voided", ...ids })).toBeNull();
  });

  it("has an answer for every status", () => {
    for (const status of RFI_STATUSES) {
      expect(() => ballInCourt({ status, ...ids }), `no branch for ${status}`).not.toThrow();
    }
  });
});

describe("isOverdue", () => {
  const past = new Date("2026-01-01T00:00:00Z");
  const now = new Date("2026-06-01T00:00:00Z");
  const future = new Date("2026-12-01T00:00:00Z");

  it("is false without a due date", () => {
    expect(isOverdue({ status: "open", dueAt: null }, now)).toBe(false);
  });

  it("is true for an open RFI past its date", () => {
    expect(isOverdue({ status: "open", dueAt: past }, now)).toBe(true);
  });

  it("is false before the date", () => {
    expect(isOverdue({ status: "open", dueAt: future }, now)).toBe(false);
  });

  /**
   * An answered RFI met or missed its date already. Leaving it red forever
   * trains people to stop reading the colour, which costs more than the one
   * row it was flagging.
   */
  it("stops flagging once the RFI has been answered", () => {
    for (const status of ["answered", "closed", "voided"] as RfiStatus[]) {
      expect(isOverdue({ status, dueAt: past }, now), `${status} still overdue`).toBe(false);
    }
  });

  it("reads the clock from its argument, not the system", () => {
    // Same RFI, two moments, two answers — this is what makes the rule testable
    // on any day of the year.
    const rfi = { status: "open" as RfiStatus, dueAt: new Date("2026-03-01T00:00:00Z") };
    expect(isOverdue(rfi, new Date("2026-02-01T00:00:00Z"))).toBe(false);
    expect(isOverdue(rfi, new Date("2026-04-01T00:00:00Z"))).toBe(true);
  });

  it("is not overdue exactly at the due moment", () => {
    const at = new Date("2026-06-01T00:00:00Z");
    expect(isOverdue({ status: "open", dueAt: at }, at)).toBe(false);
  });
});
