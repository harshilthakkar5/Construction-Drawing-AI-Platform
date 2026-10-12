import { useQuery } from "@tanstack/react-query";
import { ChevronDownIcon, PlusIcon, XIcon } from "lucide-react";
import { useState } from "react";
import {
  fullScope,
  recommendedChecks,
  RFI_CUSTOM_QUESTION_CHARS,
  RFI_CUSTOM_QUESTION_MAX,
  RFI_QUESTION_DISCIPLINES,
  RFI_REVIEW_CHECK_IDS,
  RFI_REVIEW_CHECKS,
  roleLabel,
  type RfiReviewCheckId,
  type RfiScanScopeDto,
  type RfiScanSide,
} from "@cdip/shared";
import { api } from "@/api";
import { CheckPicker } from "@/components/RfiTargetedReview";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { cn } from "@/lib/utils";

/**
 * What "Scan all drawings" compares and asks: two disciplines, the questions
 * (pre-ticked with the ones both sides draw), up to five of the person's own,
 * and whether the whole-project text checks run too. "All disciplines" on
 * both sides with every question is the scan as it always was.
 *
 * The choice is remembered per project in this browser (a convenience: the
 * scan row stores the scope it actually ran with).
 */

const sideLabel = (s: RfiScanSide) => (s === "all" ? "All disciplines" : roleLabel(s));

const storageKey = (projectId: string) => `cdip-rfi-scope:${projectId}`;

export function loadScope(projectId: string): RfiScanScopeDto {
  try {
    const raw = window.localStorage.getItem(storageKey(projectId));
    if (raw) {
      const s = JSON.parse(raw) as Partial<RfiScanScopeDto>;
      const ids = new Set(RFI_REVIEW_CHECK_IDS as string[]);
      return {
        disciplineA: (s.disciplineA as RfiScanSide) ?? "all",
        disciplineB: (s.disciplineB as RfiScanSide) ?? "all",
        checkIds: Array.isArray(s.checkIds) ? (s.checkIds.filter((id) => ids.has(id)) as RfiReviewCheckId[]) : [...RFI_REVIEW_CHECK_IDS],
        customQuestions: Array.isArray(s.customQuestions) ? s.customQuestions.filter((q) => typeof q === "string") : [],
        projectChecks: s.projectChecks !== false,
      };
    }
  } catch {
    // Storage unavailable or a value from an older version: start from everything.
  }
  return fullScope();
}

export function saveScope(projectId: string, scope: RfiScanScopeDto): void {
  try {
    window.localStorage.setItem(storageKey(projectId), JSON.stringify(scope));
  } catch {
    // Not remembering the choice is fine; the scan still runs with it.
  }
}

/** "Structural vs Architectural · 4 questions + 1 of yours". */
export function scopeLine(scope: RfiScanScopeDto | null): string {
  if (!scope) return "Every discipline · every question";
  const { disciplineA: a, disciplineB: b } = scope;
  const pair =
    a === "all" && b === "all"
      ? "Every discipline"
      : b === "all"
        ? `${sideLabel(a)} against every discipline`
        : a === b
          ? `${sideLabel(a)} within itself`
          : `${sideLabel(a)} vs ${sideLabel(b)}`;
  const n = scope.checkIds.length;
  const own = scope.customQuestions.length ? ` + ${scope.customQuestions.length} of yours` : "";
  return `${pair} · ${n === RFI_REVIEW_CHECK_IDS.length ? "every question" : `${n} question${n === 1 ? "" : "s"}`}${own}`;
}

/** Why a question may only ever answer "not shown" for this pair. */
function notDrawnBy(a: RfiScanSide, b: RfiScanSide, id: RfiReviewCheckId): string | null {
  const who = RFI_QUESTION_DISCIPLINES[id];
  if (!who) return null;
  const missing = [a, b].filter((s) => s !== "all" && !who.includes(s));
  if (!missing.length) return null;
  return `${[...new Set(missing)].map(sideLabel).join(" and ")} sheets do not usually draw this — expect “not shown”.`;
}

export function ScanScopeForm({
  projectId,
  value,
  onChange,
  disabled,
}: {
  projectId: string;
  value: RfiScanScopeDto;
  onChange: (scope: RfiScanScopeDto) => void;
  disabled?: boolean;
}) {
  const disciplines = useQuery({
    queryKey: ["rfi-scan-disciplines", projectId],
    queryFn: () => api.rfiScanDisciplines(projectId),
  });
  const [showQuestions, setShowQuestions] = useState(false);
  const set = (patch: Partial<RfiScanScopeDto>) => onChange({ ...value, ...patch });
  // Changing a side re-ticks the questions both sides draw: the person can
  // still add any question back, but the default is what the pair can answer.
  const setSide = (side: "disciplineA" | "disciplineB", s: RfiScanSide) => {
    const next = { ...value, [side]: s };
    onChange({ ...next, checkIds: recommendedChecks(next.disciplineA, next.disciplineB) });
  };

  const options = disciplines.data ?? [];
  const known = new Set(options.map((d) => d.discipline as string));
  // A remembered choice for a discipline this project no longer has.
  const stale = [value.disciplineA, value.disciplineB].filter((s) => s !== "all" && disciplines.isSuccess && !known.has(s));
  const recommended = recommendedChecks(value.disciplineA, value.disciplineB);
  const own = value.customQuestions;
  const asked = value.checkIds.length + own.filter((q) => q.trim()).length;

  const sidePicker = (side: "disciplineA" | "disciplineB", label: string) => (
    <label className="flex min-w-0 flex-1 flex-col gap-1">
      <span className="text-muted-foreground text-[11px] font-medium uppercase">{label}</span>
      <Select value={value[side]} onValueChange={(s) => setSide(side, s as RfiScanSide)} disabled={disabled}>
        <SelectTrigger size="sm" className="w-full" aria-label={label}>
          {/* The name only; the counts are in the list, and cut the box off. */}
          <SelectValue>{sideLabel(value[side])}</SelectValue>
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">All disciplines</SelectItem>
          {options.map((d) => (
            <SelectItem key={d.discipline} value={d.discipline}>
              {d.label} ({d.sheets} sheet{d.sheets === 1 ? "" : "s"}
              {d.plans !== null ? `, ${d.plans} plan${d.plans === 1 ? "" : "s"}` : ""})
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </label>
  );

  return (
    <div className="flex flex-col gap-3 text-xs">
      <div className="flex flex-wrap items-end gap-2">
        {sidePicker("disciplineA", "Compare")}
        <span className="text-muted-foreground pb-2">with</span>
        {sidePicker("disciplineB", "Against")}
      </div>
      {value.disciplineA !== "all" && value.disciplineA === value.disciplineB && (
        <p className="text-muted-foreground">
          Within one discipline the AI compares each enlarged plan with its overall plan.
        </p>
      )}
      {stale.length > 0 && (
        <p className="text-warning">
          This project has no {stale.map((s) => sideLabel(s as RfiScanSide)).join(" or ")} sheets now — choose
          another discipline.
        </p>
      )}

      <div className="flex flex-col gap-1">
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            className="flex items-center gap-1 font-medium hover:underline"
            onClick={() => setShowQuestions((v) => !v)}
            aria-expanded={showQuestions}
          >
            Questions: {value.checkIds.length} of {RFI_REVIEW_CHECK_IDS.length}
            <ChevronDownIcon className={cn("size-3 transition-transform", showQuestions && "rotate-180")} />
          </button>
          <span className="text-muted-foreground truncate">
            {value.checkIds.length
              ? value.checkIds.map((id) => RFI_REVIEW_CHECKS.find((c) => c.id === id)?.label ?? id).join(" · ")
              : "none ticked"}
          </span>
        </div>
        {showQuestions && (
          <div className="rounded-md border p-2">
            <div className="flex flex-wrap gap-1">
              <Button size="sm" variant="outline" className="h-6 px-2 text-[11px]" disabled={disabled}
                onClick={() => set({ checkIds: recommended })}>
                Recommended for this pair ({recommended.length})
              </Button>
              <Button size="sm" variant="outline" className="h-6 px-2 text-[11px]" disabled={disabled}
                onClick={() => set({ checkIds: [...RFI_REVIEW_CHECK_IDS] })}>
                Select all
              </Button>
              <Button size="sm" variant="outline" className="h-6 px-2 text-[11px]" disabled={disabled}
                onClick={() => set({ checkIds: [] })}>
                Clear
              </Button>
            </div>
            <CheckPicker
              value={value.checkIds}
              onChange={(ids) => set({ checkIds: RFI_REVIEW_CHECK_IDS.filter((id) => ids.includes(id)) })}
              note={(id) => notDrawnBy(value.disciplineA, value.disciplineB, id)}
            />
          </div>
        )}
      </div>

      <div className="flex flex-col gap-1">
        <span className="font-medium">Your own questions (optional)</span>
        {own.map((q, i) => (
          <div key={i} className="flex items-center gap-1">
            <Input
              className="h-8 text-xs"
              value={q}
              maxLength={RFI_CUSTOM_QUESTION_CHARS}
              placeholder="e.g. Do the slab openings on both sheets line up with the shafts?"
              disabled={disabled}
              onChange={(e) => set({ customQuestions: own.map((x, j) => (j === i ? e.target.value : x)) })}
              aria-label={`Your question ${i + 1}`}
            />
            <Button size="icon" variant="ghost" className="size-8 shrink-0" disabled={disabled}
              onClick={() => set({ customQuestions: own.filter((_, j) => j !== i) })} aria-label="Remove this question">
              <XIcon />
            </Button>
          </div>
        ))}
        {own.length < RFI_CUSTOM_QUESTION_MAX && (
          <Button size="sm" variant="ghost" className="h-7 self-start px-2 text-xs" disabled={disabled}
            onClick={() => set({ customQuestions: [...own, ""] })}>
            <PlusIcon />
            Add a question of your own
          </Button>
        )}
        {own.length > 0 && (
          <p className="text-muted-foreground">
            Asked of the AI comparison only. A finding from your own question is never above medium
            confidence — no code check was written for it.
          </p>
        )}
      </div>

      <label className="flex items-start gap-2">
        <input
          type="checkbox"
          className="mt-0.5"
          checked={value.projectChecks}
          disabled={disabled}
          onChange={(e) => set({ projectChecks: e.target.checked })}
        />
        <span>
          Also run the whole-project checks
          <span className="text-muted-foreground block">
            Sheets referenced but missing, marks with no schedule row, notes left TBD, one tag with two
            ratings. Free — no AI decides them.
          </span>
        </span>
      </label>
      {asked === 0 && !value.projectChecks && (
        <p className="text-warning">Choose at least one question, write one, or tick the whole-project checks.</p>
      )}
    </div>
  );
}

/** Whether the form can be started as it stands. */
export function scopeReady(scope: RfiScanScopeDto): boolean {
  return scope.checkIds.length > 0 || scope.customQuestions.some((q) => q.trim()) || scope.projectChecks;
}

/** The scope as sent: blank custom rows dropped. */
export function scopeToSend(scope: RfiScanScopeDto): RfiScanScopeDto {
  return { ...scope, customQuestions: scope.customQuestions.map((q) => q.trim()).filter(Boolean) };
}
