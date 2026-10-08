import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CheckIcon,
  ChevronDownIcon,
  CoinsIcon,
  RefreshCwIcon,
  RotateCcwIcon,
  ScanSearchIcon,
  SparklesIcon,
  TriangleAlertIcon,
  XIcon,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import {
  RFI_CHECK_LABELS,
  RFI_REVIEW_CHECKS,
  type RfiCandidateDto,
  type RfiConfidence,
  type RfiEvidenceDto,
  type RfiScanDto,
  type RfiScanUsageDto,
  type RfiUsageTotalsDto,
} from "@cdip/shared";
import { MarkedUpPdfButton } from "@/components/MarkedUpPdf";
import { api } from "@/api";
import { ConfirmDialog, Notice, Spinner } from "@/components/shared";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { RfiFullScan, canPlanFullScan } from "@/components/RfiFullScan";
import { RfiTargetedReview } from "@/components/RfiTargetedReview";
import { cn } from "@/lib/utils";
import { useAppStore } from "@/store";

/**
 * "Find RFIs in drawings", and the review list it fills.
 *
 * ONE scan in two steps. Project checks and the full AI scan used to be two
 * modes with two buttons for one job, and people asked why. Now the button
 * runs step 1 — code checks over every sheet (a sheet referenced and not
 * issued, a mark with no schedule row, a note left TBD, one tag with two
 * ratings, a grid named two ways, words read by OCR) where the AI only words
 * each finding — and prepares step 2, the AI sheet comparison, whose plan is
 * free and whose run spends only when the person starts it. Both fill the
 * same list. What comes back here is a FINDING — no number, not issued.
 * Accept makes it a numbered, open RFI pinned where it was found; Dismiss
 * remembers it so the next scan does not propose it again.
 *
 * Every finding shows the drawing's own words and a link to the spot, because
 * the reviewer's job is to check it — and "Accept all high-confidence" exists
 * for the reviewer who has.
 */

const CONFIDENCE: Record<
  RfiConfidence,
  { label: string; variant: "success" | "warning" | "secondary"; hint: string }
> = {
  high: {
    label: "High",
    variant: "success",
    hint: "The check found this directly and every guard agreed.",
  },
  medium: {
    label: "Medium",
    variant: "warning",
    hint: "Real on the evidence, but something the check cannot see could explain it.",
  },
  low: {
    label: "Low",
    variant: "secondary",
    hint: "Often genuine, often boilerplate or a sheet simply not uploaded — worth a look.",
  },
};

/** Which part of the scan found it — one list, so every finding says. */
const ORIGIN: Record<string, { label: string; hint: string }> = {
  deterministic_scan: {
    label: "Code check",
    hint: "Found by step 1, the code checks: from the drawings' own text and geometry, no AI judgement.",
  },
  full_scan: {
    label: "AI comparison",
    hint: "Found by step 2, the AI sheet comparison, and checked close up on both sheets. Beta: accuracy not yet measured.",
  },
  targeted_review: { label: "Targeted review", hint: "Found by a targeted review of named sheets." },
};

const checkLabel = (checkType: string) =>
  (RFI_CHECK_LABELS as Record<string, string>)[checkType] ??
  RFI_REVIEW_CHECKS.find((c) => c.id === checkType)?.label ??
  checkType;

function evidenceLabel(e: RfiEvidenceDto): string {
  const page = e.combinedPageNumber ?? e.pageNumber;
  return e.sheetNumber ? `${e.sheetNumber} · page ${page}` : `page ${page}`;
}

const tokens = (n: number) => n.toLocaleString();

function dollars(n: number | null): string {
  if (n === null) return "unknown";
  if (n === 0) return "$0";
  return n < 0.01 ? "<$0.01" : `$${n.toFixed(2)}`;
}

/** "thinking_level=low" / "adaptive, effort=low" / "budget_tokens=2048" as a
 * reader would say it. */
function thinkingLabel(sent: string): string {
  const level = /^thinking_level=(\w+)$/.exec(sent);
  if (level) return level[1]!;
  const effort = /effort=(\w+)/.exec(sent);
  if (effort) return `adaptive, ${effort[1]} effort`;
  const budget = /^(?:thinking_budget|budget_tokens)=(\d+)$/.exec(sent);
  if (budget) return budget[1] === "0" ? "off" : `${tokens(Number(budget[1]))}-token budget`;
  if (sent === "disabled") return "off";
  if (sent === "omitted") return "model default";
  return sent;
}

function scanSummary(scan: RfiScanDto): string {
  const when = scan.finishedAt ? new Date(scan.finishedAt).toLocaleString() : "";
  const found = `${scan.findings} ${scan.findings === 1 ? "finding" : "findings"}`;
  return `Last ${scan.fresh ? "full rescan" : "scan"} ${when} — ${found}`;
}

export function RfiReview({ projectId }: { projectId: string }) {
  const queryClient = useQueryClient();
  const [showDismissed, setShowDismissed] = useState(false);
  const [showNotes, setShowNotes] = useState(false);
  const [mode, setMode] = useState<"scan" | "targeted">("scan");
  // Turned off on this server (RFI_FULL_SCAN=off) the list 404s: step 2 is
  // not offered and the scan is the code checks alone.
  const fullScan = useQuery({ queryKey: ["rfi-full-scans", projectId], queryFn: () => api.listRfiFullScans(projectId), retry: false });
  const fullScanOffered = !fullScan.isError;

  const scan = useQuery({
    queryKey: ["rfi-scan", projectId],
    queryFn: () => api.latestRfiScan(projectId),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "queued" || status === "running" ? 2000 : false;
    },
  });
  const scanning = scan.data?.status === "queued" || scan.data?.status === "running";
  const [showUsage, setShowUsage] = useState(false);
  const [confirmFresh, setConfirmFresh] = useState(false);
  const usageTotals = useQuery({
    queryKey: ["rfi-usage", projectId],
    queryFn: () => api.rfiUsage(projectId),
    enabled: showUsage,
  });

  const pending = useQuery({
    queryKey: ["rfi-candidates", projectId, "pending"],
    queryFn: () => api.listRfiCandidates(projectId, "pending"),
  });
  const dismissed = useQuery({
    queryKey: ["rfi-candidates", projectId, "dismissed"],
    queryFn: () => api.listRfiCandidates(projectId, "dismissed"),
    enabled: showDismissed,
  });

  const refreshAll = () => {
    void queryClient.invalidateQueries({ queryKey: ["rfi-usage", projectId] });
    void queryClient.invalidateQueries({ queryKey: ["rfi-candidates", projectId] });
    void queryClient.invalidateQueries({ queryKey: ["rfis", projectId] });
  };

  // When a scan finishes, the list it filled is stale: fetch it once.
  const lastStatus = useRef(scan.data?.status);
  useEffect(() => {
    const now = scan.data?.status;
    if (lastStatus.current !== now && (now === "completed" || now === "failed")) refreshAll();
    lastStatus.current = now;
  }, [scan.data?.status]);

  const start = useMutation({
    mutationFn: async (fresh: boolean) => {
      const started = await api.startRfiScan(projectId, { fresh });
      // Step 2's plan comes with it: free (no AI call), and it means the
      // comparison's cost is on screen by the time the checks finish. A plan
      // already waiting or running is left alone; a failure here never
      // undoes the checks, which have already started.
      if (fullScanOffered && canPlanFullScan(fullScan.data?.scans[0])) {
        try {
          await api.planRfiFullScan(projectId);
        } catch {
          // Shown by step 2 itself, which offers "Prepare the comparison only".
        }
        void queryClient.invalidateQueries({ queryKey: ["rfi-full-scans", projectId] });
      }
      return started;
    },
    onSuccess: (started) => {
      queryClient.setQueryData(["rfi-scan", projectId], started);
      setConfirmFresh(false);
    },
  });
  const accept = useMutation({
    mutationFn: (id: string) => api.acceptRfiCandidate(projectId, id),
    onSuccess: refreshAll,
  });
  const dismiss = useMutation({
    mutationFn: (id: string) => api.dismissRfiCandidate(projectId, id),
    onSuccess: refreshAll,
  });
  const restore = useMutation({
    mutationFn: (id: string) => api.restoreRfiCandidate(projectId, id),
    onSuccess: refreshAll,
  });
  const acceptAll = useMutation({
    mutationFn: (min: RfiConfidence) => api.acceptAllRfiCandidates(projectId, min),
    onSuccess: refreshAll,
  });

  const candidates = pending.data?.candidates ?? [];
  const highCount = candidates.filter((c) => c.confidence === "high").length;
  const dismissedCount = pending.data?.counts.dismissed ?? 0;
  const busy = accept.isPending || dismiss.isPending || acceptAll.isPending;
  const error = (confirmFresh ? null : start.error) ?? accept.error ?? dismiss.error ?? acceptAll.error ?? restore.error;

  return (
    <section className="mb-4">
      <ToggleGroup
        type="single"
        variant="outline"
        size="sm"
        className="mb-2 w-full"
        value={mode}
        onValueChange={(value) => value && setMode(value as "scan" | "targeted")}
        aria-label="How to find RFIs"
      >
        <ToggleGroupItem value="scan" className="flex-1 text-xs" title="Every sheet: code checks, then an optional AI comparison">
          Scan all drawings
        </ToggleGroupItem>
        <ToggleGroupItem value="targeted" className="flex-1 text-xs" title="One sheet, one element, or sheets that should agree">
          Targeted review
        </ToggleGroupItem>
      </ToggleGroup>
      {mode === "targeted" ? (
        <div className="bg-muted/40 rounded-lg border p-3">
          <RfiTargetedReview projectId={projectId} onFinished={refreshAll} />
        </div>
      ) : (
      <div className="bg-muted/40 flex flex-col gap-3 rounded-lg border p-3">
        <div className="flex flex-wrap items-center gap-2">
          <Button
            size="sm"
            onClick={() => start.mutate(false)}
            disabled={scanning || start.isPending}
            title={scan.data ? "Look for gaps added since the last scan" : undefined}
          >
            {scanning || start.isPending ? <Spinner /> : <ScanSearchIcon />}
            {scanning
              ? scan.data?.fresh
                ? "Rescanning from scratch…"
                : "Scanning drawings…"
              : scan.data
                ? "Scan again"
                : "Find RFIs in drawings"}
          </Button>
          {scan.data && !scanning && (
            <Button
              size="sm"
              variant="outline"
              onClick={() => setConfirmFresh(true)}
              disabled={start.isPending}
              title="Re-check every sheet and re-word every open finding, including ones you dismissed"
            >
              <RefreshCwIcon />
              Rescan from scratch
            </Button>
          )}
        </div>
        {!scan.data && !scan.isLoading && (
          <p className="text-muted-foreground text-xs leading-relaxed">
            One scan, two steps. <strong className="text-foreground">1 · Code checks</strong> read every
            sheet — including words drawn as shapes, by OCR — for references to sheets not in the set, marks
            with no row in their schedule, notes left open (TBD), one equipment tag with two ratings, and a
            grid named two ways.{" "}
            {fullScanOffered && (
              <>
                <strong className="text-foreground">2 · AI sheet comparison</strong> is prepared at the same
                time and is optional: you see its price and choose a budget before anything is spent.{" "}
              </>
            )}
            Every finding is written up as an RFI question for you to accept or dismiss.
          </p>
        )}

        {scan.data && (
          <div className="flex flex-col gap-1">
            <div className="flex flex-wrap items-center gap-2">
              <h4 className="text-sm font-semibold">1 · Code checks</h4>
              <Badge variant="secondary">Every scan</Badge>
              {scan.data.status === "completed" && (
                <span className="text-muted-foreground text-xs">{scanSummary(scan.data)}</span>
              )}
            </div>
            {scan.data.status === "completed" && scan.data.usage && (
              <ScanUsage
                usage={scan.data.usage}
                totals={usageTotals.data}
                open={showUsage}
                onToggle={() => setShowUsage((v) => !v)}
              />
            )}
            {scanning && <ScanProgressView scan={scan.data} />}
            {scan.data.status === "failed" && (
              <Notice tone="error">
                The last scan failed
                {scan.data.stage && scan.data.stage !== "done" ? ` while ${(STEP_LABEL[scan.data.stage] ?? scan.data.stage).toLowerCase()}` : ""}:{" "}
                {scan.data.error ?? "unknown error"}
              </Notice>
            )}
            {scan.data.notes.length > 0 && (
              <div className="mt-1">
                <button
                  type="button"
                  className="text-muted-foreground flex items-center gap-1 text-xs hover:underline"
                  onClick={() => setShowNotes((v) => !v)}
                >
                  <TriangleAlertIcon className="size-3" />
                  {scan.data.notes.length} note{scan.data.notes.length === 1 ? "" : "s"} about this scan
                  <ChevronDownIcon className={cn("size-3 transition-transform", showNotes && "rotate-180")} />
                </button>
                {showNotes && (
                  <ul className="text-muted-foreground mt-1 list-disc pl-5 text-xs leading-relaxed">
                    {scan.data.notes.map((note) => (
                      <li key={note}>{note}</li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </div>
        )}

        {fullScanOffered && (scan.data || fullScan.data?.scans[0]) && (
          <div className="border-t pt-3">
            <RfiFullScan projectId={projectId} onFinished={refreshAll} />
          </div>
        )}
      </div>
      )}

      {candidates.length > 0 && (
        <div className="mt-3">
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <h4 className="text-sm font-semibold">
              Needs your review <span className="text-muted-foreground">({candidates.length})</span>
            </h4>
            {highCount > 0 && (
              <Button
                size="sm"
                variant="outline"
                className="ml-auto"
                disabled={busy}
                onClick={() => acceptAll.mutate("high")}
              >
                <CheckIcon />
                Accept all high ({highCount})
              </Button>
            )}
          </div>
          <ul className="flex flex-col gap-2">
            {candidates.map((c) => (
              <CandidateCard
                projectId={projectId}
                key={c.id}
                candidate={c}
                busy={busy}
                onAccept={() => accept.mutate(c.id)}
                onDismiss={() => dismiss.mutate(c.id)}
              />
            ))}
          </ul>
        </div>
      )}

      {scan.data?.status === "completed" && candidates.length === 0 && (
        <p className="text-muted-foreground mt-3 text-center text-xs">
          Nothing waiting for review.
          {scan.data.findings === 0
            ? " The scan found no gaps these checks can see."
            : " Every finding has been accepted or dismissed — use Rescan from scratch to look at them again."}
        </p>
      )}

      {confirmFresh && (
        <ConfirmDialog
          title="Rescan from scratch?"
          message={
            <>
              <p>
                Every sheet is checked again and every open finding gets a newly written question.
                Findings you dismissed come back for review, and so does any finding whose RFI was
                voided.
              </p>
              <p className="mt-2">
                RFIs already in the log are left exactly as they are — they have numbers, so they
                are never proposed twice. This uses AI calls for every finding it re-words.
              </p>
            </>
          }
          confirmLabel="Rescan from scratch"
          busyLabel="Starting…"
          busy={start.isPending}
          error={start.error}
          onConfirm={() => start.mutate(true)}
          onCancel={() => setConfirmFresh(false)}
        />
      )}

      {dismissedCount > 0 && (
        <div className="mt-2">
          <button
            type="button"
            className="text-muted-foreground text-xs hover:underline"
            onClick={() => setShowDismissed((v) => !v)}
          >
            {showDismissed ? "Hide" : "Show"} dismissed ({dismissedCount})
          </button>
          {showDismissed && (
            <ul className="mt-1 flex flex-col gap-1">
              {dismissed.data?.candidates.map((c) => (
                <li key={c.id} className="flex items-center gap-2 text-xs">
                  <span className="text-muted-foreground truncate line-through">{c.subject}</span>
                  <Button
                    size="sm"
                    variant="ghost"
                    className="ml-auto h-6 px-2"
                    disabled={restore.isPending}
                    onClick={() => restore.mutate(c.id)}
                  >
                    <RotateCcwIcon className="size-3" />
                    Restore
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {!!error && (
        <div className="mt-2">
          <Notice tone="error">{(error as Error).message}</Notice>
        </div>
      )}
    </section>
  );
}

/** The scan's steps in the order the worker runs them (rfi_scan.STEPS). */
const STEP_ORDER = ["starting", "ocr", "loading", "grids", "checks", "pinpoint", "wording", "saving"] as const;
const STEP_LABEL: Record<string, string> = {
  starting: "Starting",
  ocr: "Reading text drawn as shapes (OCR)",
  loading: "Loading the drawings' text",
  grids: "Reading grid lines",
  checks: "Running the code checks",
  pinpoint: "Pinpointing the evidence",
  wording: "Writing the RFI questions",
  saving: "Saving the findings",
};
/** No progress for this long, and the person is told the worker may be stuck
 * rather than left watching a bar that will never move. Every step reports
 * at least once a minute or so; one OCR page can take a little over that. */
const QUIET_MS = 3 * 60 * 1000;

function minutes(ms: number): string {
  const m = Math.floor(ms / 60_000);
  if (m < 1) return `${Math.max(1, Math.round(ms / 1000))} s`;
  return m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${m % 60} min`;
}

/**
 * What a running scan is doing, so an hour-long rescan reads as work and
 * not as a frozen spinner: the step (n of 8), the worker's own line ("page
 * 12 of 37 (M0.02)"), a bar, how long it has run, and when the worker last
 * reported. A queued scan says it is waiting for a worker, and a scan whose
 * worker has gone quiet says so and where to look.
 */
/** Why a queued scan has not started. The worker runs one scan at a time
 * (RFI_SCAN_CONCURRENCY, across every project), so the usual reason is
 * another scan still running — which nothing else on screen shows. */
function queueLine(queue: RfiScanDto["queue"]): string {
  if (!queue) return "A worker starts it as soon as one is free.";
  if (queue.state === "active") return "A worker has just picked it up.";
  if (queue.state === "missing") return "";
  const parts: string[] = [];
  if (queue.running > 0)
    parts.push(`${queue.running} other scan${queue.running === 1 ? " is" : "s are"} running now`);
  if (queue.ahead > 0) parts.push(`${queue.ahead} ${queue.ahead === 1 ? "is" : "are"} waiting ahead of this one`);
  if (parts.length === 0) return "Nothing is ahead of it — if it does not start within a minute, check that the worker is running.";
  return `${parts.join(" and ")}. The worker takes one scan at a time (RFI_SCAN_CONCURRENCY), so this one starts when ${
    queue.running + queue.ahead === 1 ? "that one finishes" : "those finish"
  }.`;
}

function ScanProgressView({ scan }: { scan: RfiScanDto }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  const queued = scan.status === "queued";
  // Running with no step written: a worker from before step reporting, or
  // one whose database lacks that migration. It IS working; say so.
  const unreported = scan.status === "running" && !scan.stage;
  const since = Date.parse(scan.startedAt ?? scan.createdAt);
  const beat = scan.heartbeatAt ? Date.parse(scan.heartbeatAt) : since;
  const quiet = now - beat > QUIET_MS;
  const index = STEP_ORDER.indexOf((scan.stage ?? "starting") as (typeof STEP_ORDER)[number]);
  return (
    <div className="mt-1 flex flex-col gap-1.5 text-xs" aria-live="polite">
      <div className="flex flex-wrap items-baseline gap-x-2">
        <span className="font-medium">
          {queued
            ? "Waiting for a worker to pick up the scan"
            : unreported
              ? "Running"
              : `Step ${Math.max(1, index + 1)} of ${STEP_ORDER.length} · ${STEP_LABEL[scan.stage!] ?? scan.stage}`}
        </span>
        <span className="text-muted-foreground tabular-nums">{scan.progress}%</span>
      </div>
      <Progress value={scan.progress} aria-label="Scan progress" />
      {queued && queueLine(scan.queue) && (
        <p className="text-muted-foreground leading-relaxed">{queueLine(scan.queue)}</p>
      )}
      {unreported && (
        <p className="text-muted-foreground leading-relaxed">
          The worker has not reported a step. Its code or the database may be older than this screen: pull, run{" "}
          <code>npx prisma migrate deploy</code>, and restart the worker to see each step.
        </p>
      )}
      {scan.detail && !queued && <p className="text-muted-foreground leading-relaxed">{scan.detail}</p>}
      <p className="text-muted-foreground tabular-nums">
        {queued ? "Queued" : "Running"} for {minutes(now - since)}
        {!queued && ` · last update ${minutes(now - beat)} ago`}
      </p>
      {(quiet || (queued && scan.queue?.state === "missing")) && (
        <Notice tone="error">
          {queued && scan.queue?.state === "missing"
            ? "This scan is no longer in the worker's queue (Redis was cleared or the job was removed), so it will never start. It frees itself after 30 minutes; or restart the worker and scan again."
            : queued
            ? `No worker has picked this scan up for ${minutes(now - beat)}. Check that the worker is running.`
            : `No update from the worker for ${minutes(now - beat)}. It may be busy on one large page, or stopped — check the worker's log.`}
        </Notice>
      )}
    </div>
  );
}

/**
 * What the last scan's wording cost, and — opened — what every scan has.
 *
 * The thinking line shows what was SENT, beside what RFI_THINKING asked for
 * when the two differ: a model that refuses the asked-for level is stepped
 * to the nearest one it takes, and the bill follows what ran.
 */
function ScanUsage({
  usage,
  totals,
  open,
  onToggle,
}: {
  usage: RfiScanUsageDto;
  totals: RfiUsageTotalsDto | undefined;
  open: boolean;
  onToggle: () => void;
}) {
  const sent = usage.thinkingSent.map(thinkingLabel);
  const asked = usage.thinkingSetting;
  const summary =
    usage.calls === 0
      ? "No AI calls this scan"
      : `${tokens(usage.inputTokens + usage.outputTokens)} tokens · ${dollars(usage.costUsd)}`;

  return (
    <div className="mt-2">
      <button
        type="button"
        className="text-muted-foreground flex items-center gap-1 text-xs hover:underline"
        onClick={onToggle}
        aria-expanded={open}
      >
        <CoinsIcon className="size-3" />
        {summary}
        <ChevronDownIcon className={cn("size-3 transition-transform", open && "rotate-180")} />
      </button>
      {open && (
        <dl className="text-muted-foreground mt-1.5 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-xs">
          {usage.model && (
            <>
              <dt>Model</dt>
              <dd className="text-foreground truncate">{usage.model}</dd>
            </>
          )}
          <dt>Thinking</dt>
          <dd className="text-foreground">
            {sent.length > 0 ? sent.join(", ") : asked ? thinkingLabel(asked) : "global default"}
            <span className="text-muted-foreground">
              {asked ? ` (RFI_THINKING=${asked})` : " (RFI_THINKING not set)"}
            </span>
            {usage.thinkingAdjusted && (
              <span className="text-warning block">
                This model does not accept {asked}; the nearest setting it takes was used.
              </span>
            )}
          </dd>
          <dt>Calls</dt>
          <dd className="text-foreground">
            {usage.calls}
            {usage.failedCalls > 0 && (
              <span className="text-destructive"> · {usage.failedCalls} failed</span>
            )}
          </dd>
          <dt>Input</dt>
          <dd className="text-foreground tabular-nums">
            {tokens(usage.inputTokens)}
            {usage.cacheReadTokens > 0 && ` (+${tokens(usage.cacheReadTokens)} cached)`}
          </dd>
          <dt>Output</dt>
          <dd className="text-foreground tabular-nums">
            {tokens(usage.outputTokens)}
            {usage.thinkingTokens !== null && usage.thinkingTokens > 0 && (
              <span className="text-muted-foreground">
                {" "}
                — {tokens(usage.thinkingTokens)} thinking,{" "}
                {tokens(Math.max(0, usage.outputTokens - usage.thinkingTokens))} answer
              </span>
            )}
            {usage.thinkingTokens === null && usage.calls > 0 && (
              <span className="text-muted-foreground"> (includes any thinking)</span>
            )}
          </dd>
          <dt>Cost</dt>
          <dd className="text-foreground">{dollars(usage.costUsd)} estimated</dd>
          {totals && totals.calls > 0 && (
            <>
              <dt className="pt-1">All scans</dt>
              <dd className="text-foreground pt-1">
                {totals.calls} call{totals.calls === 1 ? "" : "s"} ·{" "}
                {tokens(totals.inputTokens + totals.outputTokens)} tokens ·{" "}
                {dollars(totals.costUsd)}
              </dd>
            </>
          )}
        </dl>
      )}
    </div>
  );
}

function CandidateCard({
  projectId,
  candidate,
  busy,
  onAccept,
  onDismiss,
}: {
  projectId: string;
  candidate: RfiCandidateDto;
  busy: boolean;
  onAccept: () => void;
  onDismiss: () => void;
}) {
  const requestJump = useAppStore((s) => s.requestJump);
  const confidence = CONFIDENCE[candidate.confidence];
  const found = candidate.evidence.filter((e) => e.role !== "context");
  const context = candidate.evidence.filter((e) => e.role === "context");

  return (
    <li className="bg-card rounded-md border p-3">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant={confidence.variant} title={confidence.hint}>
          {confidence.label}
        </Badge>
        <span className="text-muted-foreground text-xs">{checkLabel(candidate.checkType)}</span>
        <Badge variant="outline" title={ORIGIN[candidate.origin]?.hint}>
          {ORIGIN[candidate.origin]?.label ?? candidate.origin}
        </Badge>
        {candidate.evidence.some((e) => e.source === "ocr") && (
          <Badge variant="outline" title="The words behind this finding are drawn as shapes on the sheet and were read by OCR. Check them on the drawing before accepting.">
            Read by OCR
          </Badge>
        )}
        {candidate.priority && candidate.priority !== "normal" && (
          <Badge variant="outline">{candidate.priority} priority</Badge>
        )}
        {candidate.questionSource === "model" && (
          <span
            className="text-muted-foreground ml-auto flex items-center gap-1 text-[11px]"
            title="Worded by AI from the finding. Checked: it names no sheet, mark or number that the drawing's own text does not contain."
          >
            <SparklesIcon className="size-3" />
            AI-worded
          </span>
        )}
      </div>
      <p className="mt-1.5 text-sm font-medium">{candidate.subject}</p>
      <p className="text-muted-foreground mt-1 text-sm leading-relaxed">{candidate.question}</p>
      {candidate.reasoning && (
        <p className="text-muted-foreground mt-1 text-xs leading-relaxed">
          <span className="text-foreground font-medium">Why flagged: </span>
          {candidate.reasoning}
        </p>
      )}

      <ul className="mt-2 flex flex-col gap-1">
        {found.map((e, i) => (
          <EvidenceLine key={`f${i}`} evidence={e} onOpen={requestJump} />
        ))}
        {context.map((e, i) => (
          <EvidenceLine key={`c${i}`} evidence={e} onOpen={requestJump} context />
        ))}
      </ul>

      <div className="mt-2 flex flex-wrap gap-2">
        <Button size="sm" onClick={onAccept} disabled={busy}>
          <CheckIcon />
          Accept as RFI
        </Button>
        <Button size="sm" variant="ghost" onClick={onDismiss} disabled={busy}>
          <XIcon />
          Dismiss
        </Button>
        <MarkedUpPdfButton
          projectId={projectId}
          items={[{ type: "candidate", id: candidate.id }]}
          label="Marked-up preview"
          variant="ghost"
          title="What this would look like as an RFI: a DRAFT cover form and the sheets with clouds"
        />
      </div>
    </li>
  );
}

/** The drawing's own words at the spot, and a link that opens it — the thing
 * a reviewer actually checks the finding against. */
function EvidenceLine({
  evidence,
  onOpen,
  context = false,
}: {
  evidence: RfiEvidenceDto;
  onOpen: (page: number | null, bbox?: RfiEvidenceDto["bbox"]) => void;
  context?: boolean;
}) {
  const page = evidence.combinedPageNumber;
  return (
    <li className="text-xs">
      <button
        type="button"
        className="text-primary hover:underline disabled:no-underline disabled:opacity-60"
        disabled={page === null}
        onClick={() => onOpen(page, evidence.bbox ?? undefined)}
      >
        {evidence.verification === "not_verified" ? "Not verified — check: " : context ? "Checked against " : ""}
        {evidenceLabel(evidence)}
      </button>
      {evidence.kind === "page" || evidence.kind === "crop" ? (
        <span className="text-muted-foreground">
          {" "}
          — {evidence.kind === "page" ? "whole sheet" : "drawing close-up"}
          {evidence.observation ? `: ${evidence.observation}` : ""}
        </span>
      ) : (
        <span className="text-muted-foreground"> — “{evidence.quote}”</span>
      )}
      {evidence.kind === "description" && (
        <span className="text-muted-foreground"> (AI description — weakest evidence)</span>
      )}
    </li>
  );
}
