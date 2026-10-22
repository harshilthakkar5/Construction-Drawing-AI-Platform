import { z } from "zod";
import {
  PROJECT_ROLES,
  RFI_CUSTOM_QUESTION_CHARS,
  RFI_CUSTOM_QUESTION_MAX,
  RFI_REVIEW_CHECK_IDS,
  roleLabel,
  type Discipline,
  type RfiReviewCheckId,
  type RfiScanScopeDto,
  type RfiScanSide,
} from "@cdip/shared";

/**
 * What "Scan all drawings" compares and asks (docs/rfi-full-scan.md "Scoped
 * scans"). Pure: the route validates a request with it, and every reader of a
 * stored scope goes through `scopeOf`, so one malformed row cannot take the
 * screen down.
 *
 * The rules a scope is normalised by, each a thing a person would otherwise
 * have to get right:
 *   - "all" goes SECOND: "all vs Structural" and "Structural vs all" are one
 *     comparison, and the worker reads the first side as the chosen one;
 *   - questions keep the catalogue's order and appear once;
 *   - custom questions are trimmed, blank ones dropped, repeats dropped;
 *   - a scope must ask SOMETHING — a question, or the whole-project checks;
 *   - a named discipline must have sheets here, or the comparison can only
 *     ever say "no pairs", which reads as "no problems".
 */

const DISCIPLINES: [Discipline, ...Discipline[]] = ["other", ...PROJECT_ROLES.map((r) => r.value)];
const side = z.enum(["all", ...DISCIPLINES] as [RfiScanSide, ...RfiScanSide[]]);

export const scopeInput = z.object({
  disciplineA: side.default("all"),
  disciplineB: side.default("all"),
  checkIds: z.array(z.enum(RFI_REVIEW_CHECK_IDS as [RfiReviewCheckId, ...RfiReviewCheckId[]])).max(64).default([...RFI_REVIEW_CHECK_IDS]),
  customQuestions: z
    .array(z.string().max(RFI_CUSTOM_QUESTION_CHARS, `a question may be at most ${RFI_CUSTOM_QUESTION_CHARS} characters`))
    .max(RFI_CUSTOM_QUESTION_MAX, `at most ${RFI_CUSTOM_QUESTION_MAX} questions of your own`)
    .default([]),
  projectChecks: z.boolean().default(true),
});
export type ScopeInput = z.input<typeof scopeInput>;

export const sideLabel = (s: RfiScanSide) => (s === "all" ? "All disciplines" : roleLabel(s));

/** A scope ready to store, or the reason it cannot be. `sheetsBy` is the
 * project's live sheet count per discipline (rfi analysis input only). */
export function normalizeScope(
  input: z.output<typeof scopeInput>,
  sheetsBy: Record<string, number>,
): { scope: RfiScanScopeDto } | { error: string } {
  let a = input.disciplineA;
  let b = input.disciplineB;
  if (a === "all" && b !== "all") [a, b] = [b, a];
  for (const s of [a, b]) {
    if (s !== "all" && !((sheetsBy[s] ?? 0) > 0)) {
      return { error: `this project has no ${sideLabel(s)} sheets to compare. Choose another discipline.` };
    }
  }
  const asked = new Set(input.checkIds);
  const checkIds = RFI_REVIEW_CHECK_IDS.filter((id) => asked.has(id));
  const seen = new Set<string>();
  const customQuestions: string[] = [];
  for (const raw of input.customQuestions) {
    const q = raw.replace(/\s+/g, " ").trim();
    if (!q || seen.has(q.toLowerCase())) continue;
    seen.add(q.toLowerCase());
    customQuestions.push(q);
  }
  if (!checkIds.length && !customQuestions.length && !input.projectChecks) {
    return { error: "choose at least one question, write one of your own, or tick the whole-project checks" };
  }
  return { scope: { disciplineA: a, disciplineB: b, checkIds, customQuestions, projectChecks: input.projectChecks } };
}

/** Whether a scope asks the AI comparison anything at all. */
export const asksQuestions = (scope: RfiScanScopeDto | null) =>
  !scope || scope.checkIds.length > 0 || scope.customQuestions.length > 0;

/** A stored scope read back, or null for none (or one this code cannot read). */
export function scopeOf(raw: unknown): RfiScanScopeDto | null {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const parsed = scopeInput.safeParse(raw);
  if (!parsed.success) return null;
  const s = parsed.data;
  return {
    disciplineA: s.disciplineA,
    disciplineB: s.disciplineB,
    checkIds: s.checkIds,
    customQuestions: s.customQuestions.filter((q) => q.trim()),
    projectChecks: s.projectChecks,
  };
}

/** "Structural vs Architectural · 4 questions + 1 of yours", for notes and logs. */
export function describeScope(scope: RfiScanScopeDto | null): string {
  if (!scope) return "every discipline, every question";
  const pair =
    scope.disciplineA === "all" && scope.disciplineB === "all"
      ? "every discipline"
      : scope.disciplineB === "all"
        ? `${sideLabel(scope.disciplineA)} against every discipline`
        : scope.disciplineA === scope.disciplineB
          ? `${sideLabel(scope.disciplineA)} within itself`
          : `${sideLabel(scope.disciplineA)} vs ${sideLabel(scope.disciplineB)}`;
  const own = scope.customQuestions.length ? ` + ${scope.customQuestions.length} of yours` : "";
  return `${pair} · ${scope.checkIds.length} question${scope.checkIds.length === 1 ? "" : "s"}${own}`;
}
