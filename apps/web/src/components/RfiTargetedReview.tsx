import { useMutation, useQuery } from "@tanstack/react-query";
import { ChevronDownIcon, CoinsIcon, DownloadIcon, PlayIcon, SearchIcon, TriangleAlertIcon, XIcon } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  RFI_REVIEW_CHECKS,
  RFI_REVIEW_DEPTHS,
  RFI_REVIEW_DEPTH_KEYS,
  RFI_REVIEW_THINKING,
  type RfiCheckOutcome,
  type RfiReviewCheckId,
  type RfiReviewCheckMode,
  type RfiReviewDepth,
  type RfiReviewEstimateDto,
  type RfiReviewPageDto,
  type RfiReviewProvider,
  type RfiReviewRunDto,
  type RfiReviewThinking,
} from "@cdip/shared";
import { api, type RfiReviewPlanRequest } from "@/api";
import { MarkedUpPdfButton } from "@/components/MarkedUpPdf";
import { Notice, Spinner } from "@/components/shared";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Input } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { cn } from "@/lib/utils";

/**
 * Targeted RFI review: one sheet, one element (a mark such as C-6), or two to
 * four sheets compared.
 *
 * Two steps, on purpose. "Plan" finds the pages, decides which of the 16
 * original questions apply, finds related and referenced pages and prices
 * the review — no review model is called. Only "Start review" spends money,
 * on exactly the pages shown, so a person always sees the scope and the cost
 * first. What the review finds lands in the same "Needs your review" list as
 * the project checks: a finding, never an issued RFI, until someone accepts it.
 */

const STAGES: Record<string, string> = {
  planning: "Planning",
  discovery: "Reading the drawings",
  reasoning: "Looking for problems",
  verification: "Checking each problem again",
  saving: "Saving findings",
};

const THINKING_HINT: Record<RfiReviewThinking, string> = {
  low: "Fastest and cheapest.",
  medium: "The default: more careful on crowded sheets.",
  high: "Most careful; slower and costs more.",
};

const CHECK_MODES: { value: RfiReviewCheckMode; label: string; hint: string }[] = [
  { value: "auto", label: "Choose for me", hint: "Runs the questions the target points at; every other question says why it was left out." },
  { value: "all_original", label: "All 16", hint: "Runs every original question. Costs more; each one still reports if it does not apply." },
  { value: "custom", label: "I will choose", hint: "Runs exactly the questions you tick." },
];

const OUTCOME: Record<RfiCheckOutcome, { label: string; tone: string }> = {
  candidate_found: { label: "Finding", tone: "bg-warning/15 text-warning" },
  complete_no_issue: { label: "No issue in these pages", tone: "bg-success/15 text-success" },
  insufficient_evidence: { label: "Not enough evidence", tone: "bg-muted text-muted-foreground" },
  not_applicable: { label: "Does not apply", tone: "bg-muted text-muted-foreground" },
  failed: { label: "Failed", tone: "bg-destructive/15 text-destructive" },
  not_selected: { label: "Not run", tone: "bg-muted text-muted-foreground" },
};

function dollars(n: number | null | undefined): string {
  if (n === null || n === undefined) return "unknown";
  if (n === 0) return "$0";
  return n < 0.01 ? "<$0.01" : `$${n.toFixed(2)}`;
}

function range(e: RfiReviewEstimateDto): string {
  if (e.costLowUsd === null || e.costHighUsd === null) return "cost unknown (no published price for this model)";
  return `${dollars(e.costLowUsd)} – ${dollars(e.costHighUsd)}`;
}

const targetLabel = (run: RfiReviewRunDto) => {
  const t = run.target;
  if (t.type === "sheet") return t.value;
  if (t.type === "compare") return t.values.join(" vs ");
  return [t.value, t.level && `level ${t.level}`, t.area].filter(Boolean).join(", ");
};

function pageLabel(p: RfiReviewPageDto): string {
  const page = p.combinedPageNumber ?? p.pageNumber;
  return p.sheetNumber ? `${p.sheetNumber} · page ${page}` : `page ${page}`;
}

const ROLE: Record<string, string> = {
  target: "named",
  element: "element",
  reference: "referenced",
  correspondence: "same level",
  related: "related",
};
const roleName = (role: string) => (role.startsWith("side:") ? "named" : (ROLE[role] ?? role));

function newKey(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

type TargetMode = "sheet" | "element" | "compare";

export function RfiTargetedReview({
  projectId,
  onFinished,
}: {
  projectId: string;
  onFinished: () => void;
}) {
  const [mode, setMode] = useState<TargetMode>("sheet");
  const [sheets, setSheets] = useState<string[]>(["", ""]);
  const [element, setElement] = useState({ value: "", level: "", area: "" });
  const [checkMode, setCheckMode] = useState<RfiReviewCheckMode>("auto");
  const [custom, setCustom] = useState<RfiReviewCheckId[]>(["G01", "C01"]);
  const [depth, setDepth] = useState<RfiReviewDepth>("standard");
  const [thinking, setThinking] = useState<RfiReviewThinking>("medium");
  const [modelKey, setModelKey] = useState<string>("");
  const [maxInput, setMaxInput] = useState<string>("");
  const [maxThinking, setMaxThinking] = useState<string>("");
  const [runId, setRunId] = useState<string | null>(null);
  const [lastRequest, setLastRequest] = useState<RfiReviewPlanRequest | null>(null);

  const options = useQuery({ queryKey: ["rfi-review-options", projectId], queryFn: () => api.rfiReviewOptions(projectId) });
  const model = useMemo(() => {
    const all = options.data?.options ?? [];
    const key = modelKey || (options.data ? `${options.data.default.provider}|${options.data.default.model}` : "");
    return all.find((o) => `${o.provider}|${o.model}` === key) ?? null;
  }, [options.data, modelKey]);

  // Resume the latest unfinished review after a reload.
  const recent = useQuery({
    queryKey: ["rfi-reviews", projectId],
    queryFn: () => api.listRfiReviews(projectId),
  });
  useEffect(() => {
    if (runId || !recent.data?.length) return;
    const latest = recent.data[0]!;
    if (["planned", "queued", "running"].includes(latest.status)) setRunId(latest.id);
  }, [recent.data, runId]);

  const run = useQuery({
    queryKey: ["rfi-review", projectId, runId],
    queryFn: () => api.getRfiReview(projectId, runId!),
    enabled: !!runId,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "queued" || status === "running" ? 2000 : false;
    },
  });

  const lastStatus = useRef(run.data?.status);
  useEffect(() => {
    const now = run.data?.status;
    if (lastStatus.current !== now && (now === "ready" || now === "partial" || now === "failed")) {
      onFinished();
      void recent.refetch();
    }
    lastStatus.current = now;
  }, [run.data?.status]);

  // One key per planned run: a double click or a retried request starts it once.
  const startKeys = useRef(new Map<string, string>());
  const plan = useMutation({
    mutationFn: (body: RfiReviewPlanRequest) => api.planRfiReview(projectId, body),
    onSuccess: (planned, body) => {
      setLastRequest(body);
      setRunId(planned.id);
      run.refetch();
    },
  });
  const start = useMutation({
    mutationFn: (id: string) => {
      if (!startKeys.current.has(id)) startKeys.current.set(id, newKey());
      return api.startRfiReview(projectId, id, startKeys.current.get(id)!);
    },
    onSuccess: () => run.refetch(),
  });
  const cancel = useMutation({
    mutationFn: (id: string) => api.cancelRfiReview(projectId, id),
    onSuccess: () => {
      setRunId(null);
      void recent.refetch();
    },
  });

  const values = (mode === "compare" ? sheets : sheets.slice(0, 1)).map((s) => s.trim()).filter(Boolean);
  const limits = options.data;
  const inputNumber = maxInput.trim() ? Number(maxInput) : undefined;
  const thinkingNumber = maxThinking.trim() && model?.capability.budget ? Number(maxThinking) : undefined;
  const inputOk =
    inputNumber === undefined || (!!limits && inputNumber >= limits.inputLimits.min && inputNumber <= limits.inputLimits.max);
  const thinkingOk =
    thinkingNumber === undefined ||
    (!!limits && thinkingNumber >= limits.thinkingLimits.min && thinkingNumber <= limits.thinkingLimits.max);
  const targetOk =
    mode === "sheet" ? values.length === 1 : mode === "compare" ? values.length >= 2 : element.value.trim().length > 0;
  const canPlan =
    targetOk && inputOk && thinkingOk && (checkMode !== "custom" || custom.length > 0) && model?.available !== false;

  const request = (excludePageIds: string[] = []): RfiReviewPlanRequest => ({
    target:
      mode === "sheet"
        ? { type: "sheet", value: values[0]! }
        : mode === "compare"
          ? { type: "compare", values }
          : {
              type: "element",
              value: element.value.trim(),
              ...(element.level.trim() ? { level: element.level.trim() } : {}),
              ...(element.area.trim() ? { area: element.area.trim() } : {}),
            },
    checkMode,
    checkIds: checkMode === "custom" ? custom : [],
    depth,
    thinking,
    ...(model ? { provider: model.provider, model: model.model } : {}),
    ...(inputNumber !== undefined ? { maxInputTokens: inputNumber } : {}),
    ...(thinkingNumber !== undefined ? { maxThinkingTokens: thinkingNumber } : {}),
    excludePageIds,
  });

  const current = runId ? run.data : undefined;
  const error = plan.error ?? start.error ?? cancel.error;

  if (current) {
    return (
      <RunView
        projectId={projectId}
        run={current}
        others={(recent.data ?? []).filter((r) => r.id !== current.id && (r.status === "ready" || r.status === "partial"))}
        busy={start.isPending || cancel.isPending || plan.isPending}
        error={error as Error | null}
        onStart={() => start.mutate(current.id)}
        onCancel={() => cancel.mutate(current.id)}
        onRemovePage={(pageId) => {
          const base = lastRequest ?? requestFromRun(current);
          plan.mutate({ ...base, excludePageIds: [...base.excludePageIds, pageId] });
        }}
        onReplan={() => plan.mutate({ ...(lastRequest ?? requestFromRun(current)), excludePageIds: [] })}
        onNew={() => {
          setRunId(null);
          plan.reset();
          start.reset();
        }}
      />
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <p className="text-muted-foreground text-xs leading-relaxed">
        Review one sheet, one element (a mark such as C-6), or compare sheets that should agree. You see the
        pages, the questions and the cost before anything is sent to the AI.
      </p>
      <ToggleGroup
        type="single"
        variant="outline"
        size="sm"
        value={mode}
        onValueChange={(value) => value && setMode(value as TargetMode)}
        className="w-full"
        aria-label="What to review"
      >
        <ToggleGroupItem value="sheet" className="flex-1 text-xs">
          One sheet
        </ToggleGroupItem>
        <ToggleGroupItem value="element" className="flex-1 text-xs">
          An element
        </ToggleGroupItem>
        <ToggleGroupItem value="compare" className="flex-1 text-xs">
          Compare sheets
        </ToggleGroupItem>
      </ToggleGroup>

      {mode === "element" ? (
        <div className="flex flex-col gap-1.5">
          <Input
            value={element.value}
            placeholder="Mark as printed, e.g. C-6 or PC1"
            aria-label="Element mark"
            onChange={(e) => setElement((el) => ({ ...el, value: e.target.value }))}
          />
          <div className="flex gap-1.5">
            <Input
              value={element.level}
              placeholder="Level (optional), e.g. 14"
              aria-label="Level"
              onChange={(e) => setElement((el) => ({ ...el, level: e.target.value }))}
            />
            <Input
              value={element.area}
              placeholder="Area (optional)"
              aria-label="Area"
              onChange={(e) => setElement((el) => ({ ...el, area: e.target.value }))}
            />
          </div>
        </div>
      ) : (
        <div className="flex flex-col gap-1.5">
          {(mode === "sheet" ? sheets.slice(0, 1) : sheets).map((value, i) => (
            <Input
              key={i}
              value={value}
              placeholder={mode === "sheet" ? "Sheet number, e.g. S2.105" : `Sheet ${i + 1}, e.g. ${i ? "A3.01" : "S2.105"}`}
              aria-label={`Sheet ${i + 1}`}
              onChange={(e) => setSheets((all) => all.map((s, j) => (j === i ? e.target.value : s)))}
              onKeyDown={(e) => e.key === "Enter" && canPlan && plan.mutate(request())}
            />
          ))}
          {mode === "compare" && sheets.length < 4 && (
            <button
              type="button"
              className="text-primary self-start text-xs hover:underline"
              onClick={() => setSheets((all) => [...all, ""])}
            >
              + Add another sheet
            </button>
          )}
        </div>
      )}

      <div>
        <p className="text-xs font-medium">Questions</p>
        <ToggleGroup
          type="single"
          variant="outline"
          size="sm"
          className="mt-1.5 w-full"
          value={checkMode}
          onValueChange={(value) => value && setCheckMode(value as RfiReviewCheckMode)}
          aria-label="Which questions"
        >
          {CHECK_MODES.map((m) => (
            <ToggleGroupItem key={m.value} value={m.value} className="flex-1 text-xs">
              {m.label}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
        <p className="text-muted-foreground mt-1 text-[11px]">{CHECK_MODES.find((m) => m.value === checkMode)!.hint}</p>
        {checkMode === "custom" && <CheckPicker value={custom} onChange={setCustom} />}
      </div>

      <div>
        <p className="text-xs font-medium">Depth</p>
        <ToggleGroup
          type="single"
          variant="outline"
          size="sm"
          className="mt-1.5 w-full"
          value={depth}
          onValueChange={(value) => value && setDepth(value as RfiReviewDepth)}
          aria-label="Depth"
        >
          {RFI_REVIEW_DEPTH_KEYS.map((key) => (
            <ToggleGroupItem key={key} value={key} className="flex-1 text-xs">
              {RFI_REVIEW_DEPTHS[key].label}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
        <p className="text-muted-foreground mt-1 text-[11px]">
          Up to {RFI_REVIEW_DEPTHS[depth].visualPages} rendered pages, {RFI_REVIEW_DEPTHS[depth].chunks} text pieces,{" "}
          {RFI_REVIEW_DEPTHS[depth].maxBatches} reading call(s), {RFI_REVIEW_DEPTHS[depth].maxTotalTokens.toLocaleString()} tokens
          in all.
        </p>
      </div>

      <div>
        <p className="text-xs font-medium">AI model</p>
        {options.isLoading ? (
          <Spinner />
        ) : (
          <Select value={model ? `${model.provider}|${model.model}` : ""} onValueChange={setModelKey}>
            <SelectTrigger size="sm" className="mt-1.5 w-full text-xs" aria-label="AI model">
              <SelectValue placeholder="Choose a model" />
            </SelectTrigger>
            <SelectContent>
              {(options.data?.options ?? []).map((o) => (
                <SelectItem key={`${o.provider}|${o.model}`} value={`${o.provider}|${o.model}`} disabled={!o.available} className="text-xs">
                  {o.provider === "gemini" ? "Gemini" : "Claude"} · {o.model}
                  {!o.available && " (no API key)"}
                  {o.available && !o.priced && " (price unknown)"}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
      </div>

      <div>
        <p className="text-xs font-medium">How careful</p>
        <ToggleGroup
          type="single"
          variant="outline"
          size="sm"
          className="mt-1.5 w-full"
          value={thinking}
          onValueChange={(value) => value && setThinking(value as RfiReviewThinking)}
          aria-label="Thinking"
        >
          {RFI_REVIEW_THINKING.map((level) => (
            <ToggleGroupItem key={level} value={level} className="flex-1 text-xs capitalize">
              {level}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
        <p className="text-muted-foreground mt-1 text-[11px]">{THINKING_HINT[thinking]}</p>
      </div>

      <Collapsible>
        <CollapsibleTrigger className="text-muted-foreground flex items-center gap-1 text-xs hover:underline">
          <ChevronDownIcon className="size-3" /> Token limits
        </CollapsibleTrigger>
        <CollapsibleContent className="mt-2 flex flex-col gap-2">
          <label className="text-xs">
            Most input tokens per reading call
            <Input
              type="number"
              inputMode="numeric"
              className="mt-1"
              value={maxInput}
              placeholder={limits ? String(limits.inputLimits.default) : ""}
              onChange={(e) => setMaxInput(e.target.value)}
              aria-invalid={!inputOk}
            />
            {limits && (
              <span className={cn("text-[11px]", inputOk ? "text-muted-foreground" : "text-destructive")}>
                {limits.inputLimits.min.toLocaleString()} – {limits.inputLimits.max.toLocaleString()}. Smaller splits the
                reading into more calls; what does not fit the depth's calls is reported as not read.
              </span>
            )}
          </label>
          <label className="text-xs">
            Most thinking tokens per call
            <Input
              type="number"
              inputMode="numeric"
              className="mt-1"
              value={model?.capability.budget ? maxThinking : ""}
              disabled={!model?.capability.budget}
              placeholder={model?.capability.budget ? "set by How careful" : "not supported by this model"}
              onChange={(e) => setMaxThinking(e.target.value)}
              aria-invalid={!thinkingOk}
            />
            <span className={cn("text-[11px]", thinkingOk ? "text-muted-foreground" : "text-destructive")}>
              {model?.capability.note}
              {model?.capability.budget && limits && ` ${limits.thinkingLimits.min.toLocaleString()} – ${limits.thinkingLimits.max.toLocaleString()}.`}
            </span>
          </label>
        </CollapsibleContent>
      </Collapsible>

      <Button size="sm" disabled={!canPlan || plan.isPending} onClick={() => plan.mutate(request())}>
        {plan.isPending ? <Spinner /> : <SearchIcon />}
        {plan.isPending ? "Finding pages…" : "Plan review"}
      </Button>
      {!!error && <Notice tone="error">{(error as Error).message}</Notice>}
    </div>
  );
}

function CheckPicker({ value, onChange }: { value: RfiReviewCheckId[]; onChange: (ids: RfiReviewCheckId[]) => void }) {
  const sections = [...new Set(RFI_REVIEW_CHECKS.map((c) => c.sourceSection))];
  return (
    <div className="mt-2 flex flex-col gap-2">
      {sections.map((section) => (
        <fieldset key={section}>
          <legend className="text-muted-foreground text-[11px] font-medium uppercase">{section}</legend>
          <ul className="mt-1 flex flex-col gap-1.5">
            {RFI_REVIEW_CHECKS.filter((c) => c.sourceSection === section).map((check) => (
              <li key={check.id}>
                <label className="flex items-start gap-2 text-xs">
                  <input
                    type="checkbox"
                    className="mt-0.5"
                    checked={value.includes(check.id)}
                    onChange={(e) =>
                      onChange(e.target.checked ? [...value, check.id] : value.filter((id) => id !== check.id))
                    }
                  />
                  <span>
                    <span className="font-medium">
                      {check.id} {check.label}
                    </span>
                    <span className="text-muted-foreground block">“{check.originalQuestion}”</span>
                  </span>
                </label>
              </li>
            ))}
          </ul>
        </fieldset>
      ))}
    </div>
  );
}

function requestFromRun(run: RfiReviewRunDto): RfiReviewPlanRequest {
  return {
    target: run.target,
    checkMode: run.checkMode,
    checkIds: run.checkMode === "custom" ? run.checkIds : [],
    depth: run.depth,
    thinking: run.thinkingRequested,
    provider: run.provider,
    model: run.model || undefined,
    maxInputTokens: run.limits.maxInputTokens,
    maxThinkingTokens: run.limits.maxThinkingTokens,
    excludePageIds: [],
  };
}

const checkOf = (id: string) => RFI_REVIEW_CHECKS.find((c) => c.id === id);

function Section({ title, count, children, open = false }: { title: string; count?: number; children: React.ReactNode; open?: boolean }) {
  return (
    <Collapsible defaultOpen={open}>
      <CollapsibleTrigger className="flex w-full items-center gap-1 text-left text-xs font-medium hover:underline">
        <ChevronDownIcon className="size-3" />
        {title}
        {count !== undefined && <span className="text-muted-foreground font-normal">({count})</span>}
      </CollapsibleTrigger>
      <CollapsibleContent className="mt-1.5">{children}</CollapsibleContent>
    </Collapsible>
  );
}

function PlannedQuestions({ run }: { run: RfiReviewRunDto }) {
  const ids = RFI_REVIEW_CHECKS.map((c) => c.id);
  const selected = ids.filter((id) => run.checkPlan[id]?.selected);
  return (
    <Section title="Questions" count={selected.length} open>
      <ul className="flex flex-col gap-1 text-xs">
        {[...selected, ...ids.filter((id) => !run.checkPlan[id]?.selected)].map((id) => {
          const plan = run.checkPlan[id];
          return (
            <li key={id} className={cn(!plan?.selected && "text-muted-foreground")}>
              <span className={cn(plan?.selected && "font-medium")}>
                {id} {checkOf(id)?.label}
              </span>
              {!plan?.selected && " — not run"}
              {plan?.applicability === "applicable" && plan.selected && <Badge variant="secondary" className="ml-1 h-4 px-1 text-[10px]">applies</Badge>}
              {plan?.reason && <span className="text-muted-foreground block">{plan.reason}</span>}
            </li>
          );
        })}
      </ul>
    </Section>
  );
}

/** A run that found a problem ALREADY on file (an RFI, a dismissal, a
 * scan's finding) adds no candidate — which must not read as "no problems". */
function headline(run: RfiReviewRunDto): string {
  if (run.candidates > 0) {
    return `${run.candidates} finding${run.candidates === 1 ? "" : "s"} added to “Needs your review” below. None is an RFI until you accept it.`;
  }
  const again = Object.values(run.checkResults ?? {}).filter((r) => r.outcome === "candidate_found").length;
  if (again > 0) {
    return "Nothing new: what this run found was already found before. “The 16 questions” below says where each one is — the RFI log, Needs your review, or Dismissed.";
  }
  return "No problems were confirmed on these pages.";
}

function Outcomes({ run }: { run: RfiReviewRunDto }) {
  if (!run.checkResults) return null;
  return (
    <Section title="The 16 questions" count={16} open>
      <ul className="flex flex-col gap-1.5 text-xs">
        {RFI_REVIEW_CHECKS.map((check) => {
          const result = run.checkResults![check.id];
          const outcome = OUTCOME[result?.outcome ?? "not_selected"];
          return (
            <li key={check.id}>
              <div className="flex items-start gap-2">
                <span className={cn("shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium", outcome.tone)}>{outcome.label}</span>
                <span>
                  <span className="font-medium">
                    {check.id} {check.label}
                  </span>
                  {result?.reason && <span className="text-muted-foreground block">{result.reason}</span>}
                  {result?.gaps.map((gap) => (
                    <span key={gap} className="text-muted-foreground block">
                      Missing: {gap}
                    </span>
                  ))}
                </span>
              </div>
            </li>
          );
        })}
      </ul>
    </Section>
  );
}

function Coverage({ run }: { run: RfiReviewRunDto }) {
  const c = run.coverage;
  const total = c.omittedPages.length + c.unresolvedReferences.length + c.omissions.length + c.searchLog.length;
  if (!total) return null;
  return (
    <Section title="What was and was not looked at" open={c.omissions.length > 0}>
      <div className="text-muted-foreground flex flex-col gap-1.5 text-xs">
        {c.omissions.map((o) => (
          <p key={o} className="text-warning">
            Not read: {o}
          </p>
        ))}
        {c.omittedPages.length > 0 && (
          <div>
            <p className="text-foreground">Left out of the plan</p>
            <ul className="list-disc pl-4">
              {c.omittedPages.map((p, i) => (
                <li key={i}>
                  {p.sheetNumber ?? "a page"}: {p.reason}
                </li>
              ))}
            </ul>
          </div>
        )}
        {c.unresolvedReferences.length > 0 && (
          <p>Referred to but not in this project: {c.unresolvedReferences.join(", ")}</p>
        )}
        {c.searchLog.length > 0 && (
          <div>
            <p className="text-foreground">Searches</p>
            <ul className="list-disc pl-4">
              {c.searchLog.map((s, i) => (
                <li key={i}>
                  {s.query} — {s.found} found ({s.stage})
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </Section>
  );
}

function Inventory({ run }: { run: RfiReviewRunDto }) {
  if (!run.inventory.length) return null;
  return (
    <Section title="Elements recorded" count={run.inventory.length}>
      <ul className="flex flex-col gap-1.5 text-xs">
        {run.inventory.map((item, i) => (
          <li key={i}>
            <span className="font-medium">{item.entity}</span>
            {item.location && <span className="text-muted-foreground"> at {item.location}</span>}
            <span className="text-muted-foreground"> · {item.checkId}</span>
            <ul className="text-muted-foreground pl-3">
              {item.fields.map((f) => (
                <li key={f.name}>
                  {f.name}: {f.state === "supported" ? <span className="text-foreground">{f.value}</span> : f.state.replace("_", " ")}
                  {f.derived && " (worked out)"}
                  {f.evidenceIds.length > 0 && ` [${f.evidenceIds.join(", ")}]`}
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ul>
    </Section>
  );
}

function OtherModelPrice({ projectId, run }: { projectId: string; run: RfiReviewRunDto }) {
  const options = useQuery({ queryKey: ["rfi-review-options", projectId], queryFn: () => api.rfiReviewOptions(projectId) });
  const [key, setKey] = useState("");
  const pick = options.data?.options.find((o) => `${o.provider}|${o.model}` === key);
  const estimate = useQuery({
    queryKey: ["rfi-review-reestimate", run.id, key],
    queryFn: () =>
      api.reestimateRfiReview(projectId, run.id, {
        provider: pick!.provider as RfiReviewProvider,
        model: pick!.model,
        thinking: run.thinkingRequested,
        maxInputTokens: run.limits.maxInputTokens,
        maxThinkingTokens: pick!.capability.budget ? run.limits.maxThinkingTokens : null,
      }),
    enabled: !!pick,
  });
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs">
      <span className="text-muted-foreground">Price it on</span>
      <Select value={key} onValueChange={setKey}>
        <SelectTrigger size="sm" className="h-7 w-48 text-xs" aria-label="Another model">
          <SelectValue placeholder="another model" />
        </SelectTrigger>
        <SelectContent>
          {(options.data?.options ?? [])
            .filter((o) => !(o.provider === run.provider && o.model === run.model))
            .map((o) => (
              <SelectItem key={`${o.provider}|${o.model}`} value={`${o.provider}|${o.model}`} className="text-xs">
                {o.model}
              </SelectItem>
            ))}
        </SelectContent>
      </Select>
      {estimate.data && <span>{range(estimate.data)} — plan again with that model to use it</span>}
      {estimate.error && <span className="text-destructive">{(estimate.error as Error).message}</span>}
    </div>
  );
}

function Compare({ projectId, run, others }: { projectId: string; run: RfiReviewRunDto; others: RfiReviewRunDto[] }) {
  const [otherId, setOtherId] = useState("");
  const comparison = useQuery({
    queryKey: ["rfi-review-compare", run.id, otherId],
    queryFn: () => api.compareRfiReviews(projectId, otherId, run.id),
    enabled: !!otherId,
  });
  if (!others.length) return null;
  const c = comparison.data;
  return (
    <Section title="Compare with an earlier review">
      <div className="flex flex-col gap-1.5 text-xs">
        <Select value={otherId} onValueChange={setOtherId}>
          <SelectTrigger size="sm" className="h-7 w-full text-xs" aria-label="Earlier review">
            <SelectValue placeholder="Choose a review" />
          </SelectTrigger>
          <SelectContent>
            {others.map((o) => (
              <SelectItem key={o.id} value={o.id} className="text-xs">
                {targetLabel(o)} · {o.model} · {new Date(o.createdAt).toLocaleDateString()}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {c && (
          <>
            <p>
              Earlier: {dollars(c.base.costUsd)} on {c.base.model} · This: {dollars(c.other.costUsd)} on {c.other.model}
              {c.differenceUsd !== null && (
                <>
                  {" "}
                  · {c.differenceUsd >= 0 ? "+" : ""}
                  {dollars(Math.abs(c.differenceUsd)).replace("$", c.differenceUsd < 0 ? "−$" : "$")}
                  {c.differencePercent !== null && ` (${c.differencePercent >= 0 ? "+" : ""}${c.differencePercent.toFixed(0)}%)`}
                </>
              )}
            </p>
            <ul className="text-muted-foreground list-disc pl-4">
              {c.caveats.map((caveat) => (
                <li key={caveat}>{caveat}</li>
              ))}
            </ul>
          </>
        )}
      </div>
    </Section>
  );
}

function Downloads({ projectId, run }: { projectId: string; run: RfiReviewRunDto }) {
  const link = (kind: "draft" | "accepted", format: "pdf" | "json", label: string) => (
    <a
      className="text-primary inline-flex items-center gap-1 text-xs hover:underline"
      href={api.rfiReviewReportUrl(projectId, run.id, kind, format)}
      download
    >
      <DownloadIcon className="size-3" />
      {label}
    </a>
  );
  return (
    <div className="flex flex-wrap items-center gap-3">
      {run.candidates > 0 && (
        <MarkedUpPdfButton projectId={projectId} reviewRunId={run.id} label="Marked-up PDF of the findings" />
      )}
      {link("draft", "pdf", "Draft report (PDF)")}
      {link("accepted", "pdf", "Accepted RFIs (PDF)")}
      {link("draft", "json", "JSON")}
    </div>
  );
}

function RunView({
  projectId,
  run,
  others,
  busy,
  error,
  onStart,
  onCancel,
  onRemovePage,
  onReplan,
  onNew,
}: {
  projectId: string;
  run: RfiReviewRunDto;
  others: RfiReviewRunDto[];
  busy: boolean;
  error: Error | null;
  onStart: () => void;
  onCancel: () => void;
  onRemovePage: (pageId: string) => void;
  onReplan: () => void;
  onNew: () => void;
}) {
  const active = run.status === "queued" || run.status === "running";
  const finished = run.status === "ready" || run.status === "partial";
  const replan = !!error && /plan the review again|replan/i.test(error.message);
  // A named sheet's last page cannot be removed — the API would refuse it.
  const namedPages = run.pages.filter((p) => p.role !== "related");
  const removable = (p: RfiReviewPageDto) =>
    p.role === "related" || namedPages.filter((q) => q.role === p.role).length > 1;

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-semibold">Review of {targetLabel(run)}</span>
        <Badge variant={run.status === "failed" || run.status === "stale" ? "destructive" : "secondary"}>
          {run.status === "planned" ? "plan — not started" : run.status}
        </Badge>
        <span className="text-muted-foreground text-[11px]">
          {run.model} · {run.depth} · questions {run.catalogueVersion}
        </span>
      </div>

      {run.status === "planned" && (
        <>
          {run.ambiguous.length > 0 && (
            <p className="text-warning flex items-start gap-1 text-xs">
              <TriangleAlertIcon className="mt-0.5 size-3 shrink-0" />
              More than one page matches {run.ambiguous.join(", ")}. Remove the ones you did not mean.
            </p>
          )}
          <PlannedQuestions run={run} />
          <Section title="Pages" count={run.pages.length} open>
            <ul className="flex flex-col gap-1">
              {run.pages.map((p) => (
                <li key={p.pageId} className="flex items-start gap-2 text-xs">
                  <span className="min-w-0">
                    <span className={cn(p.role !== "related" && "font-medium")}>{pageLabel(p)}</span>
                    <span className="text-muted-foreground">
                      {" "}
                      · {roleName(p.role)} · {p.chunks} text pieces{p.visual ? " · images" : " · text only"}
                    </span>
                    <span className="text-muted-foreground block">{p.reason}</span>
                  </span>
                  {removable(p) && (
                    <Button
                      size="sm"
                      variant="ghost"
                      className="ml-auto h-6 shrink-0 px-2"
                      disabled={busy}
                      onClick={() => onRemovePage(p.pageId)}
                      title="Leave this page out and plan again"
                    >
                      <XIcon className="size-3" />
                      Remove
                    </Button>
                  )}
                </li>
              ))}
            </ul>
          </Section>
          <Coverage run={run} />
          {run.estimate && (
            <div className="flex flex-col gap-1 text-xs">
              <p className="flex items-center gap-1">
                <CoinsIcon className="size-3" />
                Estimated {range(run.estimate)} · {run.estimate.modelCalls} AI calls · {run.estimate.imageParts} images
              </p>
              <p className="text-muted-foreground">
                Planning already spent {dollars(run.estimate.planningCostUsd)} ({run.estimate.planningTokens.toLocaleString()} tokens
                of search). Prices: {run.estimate.pricingVersion}.
              </p>
              <Section title="How this was estimated">
                <ul className="text-muted-foreground list-disc pl-4">
                  {run.estimate.assumptions.map((a) => (
                    <li key={a}>{a}</li>
                  ))}
                </ul>
              </Section>
              <OtherModelPrice projectId={projectId} run={run} />
            </div>
          )}
          {run.notes.map((note) => (
            <p key={note} className="text-muted-foreground text-xs">
              {note}
            </p>
          ))}
          <div className="flex gap-2">
            <Button size="sm" onClick={onStart} disabled={busy}>
              {busy ? <Spinner /> : <PlayIcon />}
              Start review
            </Button>
            <Button size="sm" variant="ghost" onClick={onCancel} disabled={busy}>
              Cancel
            </Button>
          </div>
        </>
      )}

      {active && (
        <>
          <p className="text-muted-foreground text-xs">
            {run.status === "queued" ? "Waiting for a worker…" : STAGES[run.stage ?? "discovery"]}
          </p>
          <Progress value={run.progress} />
          <Button size="sm" variant="ghost" className="self-start" onClick={onCancel} disabled={busy}>
            Stop review
          </Button>
        </>
      )}

      {finished && (
        <>
          {run.status === "partial" && (
            <Notice tone="error">
              Partial review: some of the planned evidence was not read, or the run hit its token limit. Questions it
              could not finish say so below.
            </Notice>
          )}
          <p className="text-sm">{headline(run)}</p>
          <Outcomes run={run} />
          <Inventory run={run} />
          <Coverage run={run} />
          {run.usage?.total && (
            <p className="text-muted-foreground flex items-center gap-1 text-xs">
              <CoinsIcon className="size-3" />
              {run.usage.total.calls} AI calls ·{" "}
              {(run.usage.total.inputTokens + run.usage.total.outputTokens).toLocaleString()} tokens ·{" "}
              {dollars(run.usage.total.costUsd)}
              {run.estimate && <> (estimated {range(run.estimate)})</>}
            </p>
          )}
          <Compare projectId={projectId} run={run} others={others} />
          {run.notes.length > 0 && (
            <Section title="Notes" count={run.notes.length}>
              <ul className="text-muted-foreground list-disc pl-5 text-xs leading-relaxed">
                {run.notes.map((note) => (
                  <li key={note}>{note}</li>
                ))}
              </ul>
            </Section>
          )}
          <Downloads projectId={projectId} run={run} />
        </>
      )}

      {(run.status === "failed" || run.status === "stale" || (run.status === "cancelled" && run.error)) && (
        <Notice tone="error">
          {run.status === "stale" ? "The drawings changed since this plan was made: " : run.status === "cancelled" ? "Stopped: " : "The review failed: "}
          {run.error ?? "unknown error"}
        </Notice>
      )}
      {run.status === "failed" && run.checkResults && <Outcomes run={run} />}

      {!!error && <Notice tone="error">{error.message}</Notice>}

      {(replan || run.status === "stale") && (
        <Button size="sm" variant="outline" className="self-start" onClick={onReplan} disabled={busy}>
          Plan again
        </Button>
      )}
      {!active && run.status !== "planned" && (
        <Button size="sm" variant="outline" className="self-start" onClick={onNew}>
          New review
        </Button>
      )}
    </div>
  );
}
