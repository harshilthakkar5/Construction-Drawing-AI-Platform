import { useMutation, useQuery } from "@tanstack/react-query";
import { CoinsIcon, PlayIcon, SearchIcon, TriangleAlertIcon, XIcon } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import {
  RFI_REVIEW_CHECKS,
  RFI_REVIEW_THINKING,
  type RfiReviewCheckId,
  type RfiReviewPageDto,
  type RfiReviewRunDto,
  type RfiReviewThinking,
} from "@cdip/shared";
import { api, type RfiReviewPlanRequest } from "@/api";
import { Notice, Spinner } from "@/components/shared";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { cn } from "@/lib/utils";

/**
 * Targeted RFI review: one sheet, or two to four sheets compared.
 *
 * Two steps, on purpose. "Plan" finds the sheet, picks the checks, finds the
 * related pages and prices the review — no review model is called. Only
 * "Start review" spends money, on exactly the pages shown, so a person always
 * sees the scope and the cost first. What the review finds lands in the same
 * "Needs your review" list as the project checks: a finding, never an issued
 * RFI, until someone accepts it.
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

function dollars(n: number): string {
  if (n === 0) return "$0";
  return n < 0.01 ? "<$0.01" : `$${n.toFixed(2)}`;
}

const targetLabel = (run: RfiReviewRunDto) =>
  run.target.type === "sheet" ? run.target.value : run.target.values.join(" vs ");

function pageLabel(p: RfiReviewPageDto): string {
  const page = p.combinedPageNumber ?? p.pageNumber;
  return p.sheetNumber ? `${p.sheetNumber} · page ${page}` : `page ${page}`;
}

export function RfiTargetedReview({
  projectId,
  onFinished,
}: {
  projectId: string;
  onFinished: () => void;
}) {
  const [mode, setMode] = useState<"sheet" | "compare">("sheet");
  const [sheets, setSheets] = useState<string[]>(["", ""]);
  const [checkMode, setCheckMode] = useState<"auto" | "custom">("auto");
  const [custom, setCustom] = useState<RfiReviewCheckId[]>(RFI_REVIEW_CHECKS.map((c) => c.id));
  const [thinking, setThinking] = useState<RfiReviewThinking>("medium");
  const [runId, setRunId] = useState<string | null>(null);
  const [lastRequest, setLastRequest] = useState<RfiReviewPlanRequest | null>(null);

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
    if (lastStatus.current !== now && (now === "ready" || now === "failed")) onFinished();
    lastStatus.current = now;
  }, [run.data?.status]);

  const plan = useMutation({
    mutationFn: (body: RfiReviewPlanRequest) => api.planRfiReview(projectId, body),
    onSuccess: (planned, body) => {
      setLastRequest(body);
      setRunId(planned.id);
      run.refetch();
    },
  });
  const start = useMutation({
    mutationFn: (id: string) => api.startRfiReview(projectId, id),
    onSuccess: () => run.refetch(),
  });
  const cancel = useMutation({
    mutationFn: (id: string) => api.cancelRfiReview(projectId, id),
    onSuccess: () => {
      setRunId(null);
      void recent.refetch();
    },
  });

  const values = (mode === "sheet" ? sheets.slice(0, 1) : sheets).map((s) => s.trim()).filter(Boolean);
  const canPlan =
    (mode === "sheet" ? values.length === 1 : values.length >= 2) &&
    (checkMode === "auto" || custom.length > 0);

  const request = (excludePageIds: string[] = []): RfiReviewPlanRequest => ({
    target: mode === "sheet" ? { type: "sheet", value: values[0]! } : { type: "compare", values },
    checkMode,
    checkIds: checkMode === "custom" ? custom : [],
    depth: "standard",
    thinking,
    excludePageIds,
  });

  const current = runId ? run.data : undefined;
  const error = plan.error ?? start.error ?? cancel.error;

  if (current) {
    return (
      <RunView
        run={current}
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
        Review one sheet, or compare sheets that should agree (for example the structural and
        the architectural plan of one level). You see the pages and the cost before anything is
        sent to the AI.
      </p>
      <ToggleGroup
        type="single"
        variant="outline"
        size="sm"
        value={mode}
        onValueChange={(value) => value && setMode(value as "sheet" | "compare")}
        className="w-full"
        aria-label="What to review"
      >
        <ToggleGroupItem value="sheet" className="flex-1 text-xs">
          One sheet
        </ToggleGroupItem>
        <ToggleGroupItem value="compare" className="flex-1 text-xs">
          Compare sheets
        </ToggleGroupItem>
      </ToggleGroup>

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

      <div>
        <p className="text-xs font-medium">Checks</p>
        <ToggleGroup
          type="single"
          variant="outline"
          size="sm"
          className="mt-1.5 w-full"
          value={checkMode}
          onValueChange={(value) => value && setCheckMode(value as "auto" | "custom")}
          aria-label="Which checks"
        >
          <ToggleGroupItem value="auto" className="flex-1 text-xs">
            Choose for me
          </ToggleGroupItem>
          <ToggleGroupItem value="custom" className="flex-1 text-xs">
            I will choose
          </ToggleGroupItem>
        </ToggleGroup>
        {checkMode === "custom" && (
          <ul className="mt-2 flex flex-col gap-1.5">
            {RFI_REVIEW_CHECKS.map((check) => (
              <li key={check.id}>
                <label className="flex items-start gap-2 text-xs">
                  <input
                    type="checkbox"
                    className="mt-0.5"
                    checked={custom.includes(check.id)}
                    onChange={(e) =>
                      setCustom((ids) =>
                        e.target.checked ? [...ids, check.id] : ids.filter((id) => id !== check.id),
                      )
                    }
                  />
                  <span>
                    <span className="font-medium">{check.label}</span>
                    <span className="text-muted-foreground block">{check.objective}</span>
                  </span>
                </label>
              </li>
            ))}
          </ul>
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

      <Button size="sm" disabled={!canPlan || plan.isPending} onClick={() => plan.mutate(request())}>
        {plan.isPending ? <Spinner /> : <SearchIcon />}
        {plan.isPending ? "Finding pages…" : "Plan review"}
      </Button>
      {!!error && <Notice tone="error">{(error as Error).message}</Notice>}
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
    excludePageIds: [],
  };
}

const checkName = (id: string) => RFI_REVIEW_CHECKS.find((c) => c.id === id)?.label ?? id;

function RunView({
  run,
  busy,
  error,
  onStart,
  onCancel,
  onRemovePage,
  onReplan,
  onNew,
}: {
  run: RfiReviewRunDto;
  busy: boolean;
  error: Error | null;
  onStart: () => void;
  onCancel: () => void;
  onRemovePage: (pageId: string) => void;
  onReplan: () => void;
  onNew: () => void;
}) {
  const active = run.status === "queued" || run.status === "running";
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
      </div>

      {run.status === "planned" && (
        <>
          {run.ambiguous.length > 0 && (
            <p className="text-warning flex items-start gap-1 text-xs">
              <TriangleAlertIcon className="mt-0.5 size-3 shrink-0" />
              More than one page claims {run.ambiguous.join(", ")}. Remove the one you did not mean.
            </p>
          )}
          <div>
            <p className="text-xs font-medium">Checks</p>
            <ul className="text-muted-foreground mt-1 flex flex-col gap-0.5 text-xs">
              {run.checkIds.map((id) => (
                <li key={id}>
                  <span className="text-foreground">{checkName(id)}</span>
                  {run.checkReasons[id] && ` — ${run.checkReasons[id]}`}
                </li>
              ))}
            </ul>
          </div>
          <div>
            <p className="text-xs font-medium">
              Pages <span className="text-muted-foreground font-normal">({run.pages.length})</span>
            </p>
            <ul className="mt-1 flex flex-col gap-1">
              {run.pages.map((p) => (
                <li key={p.pageId} className="flex items-center gap-2 text-xs">
                  <span className={cn(p.role !== "related" && "font-medium")}>{pageLabel(p)}</span>
                  <span className="text-muted-foreground">
                    {p.role === "related" ? "related" : "named"} · {p.chunks} text pieces
                    {p.visual ? " · images" : ""}
                  </span>
                  {removable(p) && (
                    <Button
                      size="sm"
                      variant="ghost"
                      className="ml-auto h-6 px-2"
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
          </div>
          {run.estimate && (
            <p className="text-muted-foreground flex items-center gap-1 text-xs">
              <CoinsIcon className="size-3" />
              About {dollars(run.estimate.costUsd)} · {run.estimate.modelCalls} AI calls ·{" "}
              {run.estimate.imageParts} images · {run.estimate.model}
            </p>
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

      {run.status === "ready" && (
        <>
          <p className="text-sm">
            {run.candidates === 0
              ? "No problems were confirmed on these pages."
              : `${run.candidates} finding${run.candidates === 1 ? "" : "s"} added to “Needs your review” below.`}
          </p>
          {run.usage?.total && (
            <p className="text-muted-foreground flex items-center gap-1 text-xs">
              <CoinsIcon className="size-3" />
              {run.usage.total.calls} AI calls ·{" "}
              {(run.usage.total.inputTokens + run.usage.total.outputTokens).toLocaleString()} tokens ·{" "}
              {dollars(run.usage.total.costUsd)}
            </p>
          )}
          {run.notes.length > 0 && (
            <ul className="text-muted-foreground list-disc pl-5 text-xs leading-relaxed">
              {run.notes.map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
          )}
        </>
      )}

      {(run.status === "failed" || run.status === "stale") && (
        <Notice tone="error">
          {run.status === "stale" ? "The drawings changed since this plan was made: " : "The review failed: "}
          {run.error ?? "unknown error"}
        </Notice>
      )}

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
