import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDownIcon, CoinsIcon, PlayIcon, RotateCcwIcon, ScanSearchIcon, TriangleAlertIcon, XIcon } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { RfiFullScanDto, RfiReviewProvider } from "@cdip/shared";
import { api, type RfiFullScanList } from "@/api";
import { MarkedUpPdfButton } from "@/components/MarkedUpPdf";
import { RfiDiagnosticsButton } from "@/components/RfiDiagnostics";
import { Notice, Spinner } from "@/components/shared";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Input } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";

/**
 * Full AI scan: every pair of sheets that should agree is shown to the AI
 * area by area, and every possible problem is checked again close up.
 *
 * Same two steps as a targeted review. "Plan" reads every page and pairs the
 * sheets — no AI call, no cost — and shows what will be compared, what was
 * left out and why, and roughly what it costs. Only "Start" spends, under a
 * budget the person sets; the scan stops before it would go past it, keeps
 * everything it found, and can be resumed. What it finds goes to "Needs your
 * review" below: a finding, never an issued RFI, until someone accepts it.
 */

const ACTIVE = new Set(["planning", "queued", "running"]);

const STAGE: Record<string, string> = {
  queued: "Waiting for a worker",
  catalogue: "Reading every page",
  pairs: "Pairing and lining up sheets",
  "first look": "AI first look at each area",
  "close look": "Checking each possible problem close up",
};

const KIND_LABEL: Record<string, string> = {
  plan: "plans",
  enlarged_plan: "enlarged plans",
  section: "sections",
  elevation: "elevations",
  detail: "detail sheets",
  schedule: "schedules",
  notes: "notes",
  cover: "cover sheets",
  other: "other",
};

function newKey(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

const money = (usd: number) => (usd < 1 ? `$${usd.toFixed(2)}` : `$${usd.toFixed(usd < 100 ? 2 : 0)}`);
const tokens = (n: number) => (n >= 1_000_000 ? `${(n / 1_000_000).toFixed(1)}M` : `${Math.round(n / 1000)}k`);
const sheetName = (s: RfiFullScanDto["pairs"][number]["a"]) => s.sheetNumber ?? `page ${s.combinedPageNumber ?? s.pageNumber}`;

export function RfiFullScan({ projectId, onFinished }: { projectId: string; onFinished: () => void }) {
  const queryClient = useQueryClient();
  const list = useQuery({
    queryKey: ["rfi-full-scans", projectId],
    queryFn: () => api.listRfiFullScans(projectId),
    refetchInterval: (q) => (q.state.data?.scans[0] && ACTIVE.has(q.state.data.scans[0].status) ? 2500 : false),
  });
  const scan = list.data?.scans[0] ?? null;
  const setScan = (next: RfiFullScanDto) =>
    queryClient.setQueryData<RfiFullScanList>(["rfi-full-scans", projectId], (old) =>
      old ? { ...old, scans: [next, ...old.scans.filter((s) => s.id !== next.id)] } : old,
    );

  // A scan that just finished filled the review list: refresh it once.
  const last = useRef(scan?.status);
  useEffect(() => {
    if (last.current && ACTIVE.has(last.current) && scan && !ACTIVE.has(scan.status)) onFinished();
    last.current = scan?.status;
  }, [scan?.status]);

  const plan = useMutation({ mutationFn: () => api.planRfiFullScan(projectId), onSuccess: setScan });
  const cancel = useMutation({ mutationFn: (id: string) => api.cancelRfiFullScan(projectId, id), onSuccess: setScan });

  if (list.isLoading) return <Spinner />;
  if (list.error) return <Notice tone="error">{(list.error as Error).message}</Notice>;
  const availability = list.data!.availability;
  // A new plan is offered once nothing is in flight or waiting to start.
  const canPlan =
    !scan || ["cancelled", "stale", "ready", "partial"].includes(scan.status) || (scan.status === "failed" && !scan.startedAt);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <h4 className="text-sm font-semibold">Full AI scan</h4>
        {availability === "beta" && (
          <Badge variant="outline" title="Not yet measured against real RFIs. Review every finding before accepting it.">
            Beta — accuracy not measured yet
          </Badge>
        )}
      </div>

      {scan && ACTIVE.has(scan.status) && <Running scan={scan} onCancel={() => cancel.mutate(scan.id)} cancelling={cancel.isPending} />}
      {scan?.status === "planned" && (
        <Planned projectId={projectId} scan={scan} models={list.data!.models} prices={list.data!.prices} onStarted={setScan} onCancel={() => cancel.mutate(scan.id)} />
      )}
      {scan && ["ready", "partial", "failed"].includes(scan.status) && scan.startedAt && (
        <Finished projectId={projectId} scan={scan} onResumed={setScan} />
      )}
      {scan && scan.notes.length > 0 && scan.status !== "planned" && <Notes notes={scan.notes} />}

      {canPlan && (
        <div className="flex flex-col gap-2">
          {!scan && (
            <p className="text-muted-foreground text-xs leading-relaxed">
              Pairs every sheet that should agree — the same level drawn by two disciplines, and each
              enlarged plan with its overall plan — and shows them to the AI area by area. Each possible
              problem is checked again close up, and only what passes the code's rules is listed for you.
              Planning is free; you see the cost and set a budget before anything is sent to the AI.
            </p>
          )}
          {scan?.status === "stale" && <Notice tone="error">{scan.error ?? "The drawings changed since this scan was planned."}</Notice>}
          <Button size="sm" variant={scan ? "outline" : "default"} className="self-start" disabled={plan.isPending} onClick={() => plan.mutate()}>
            {plan.isPending ? <Spinner /> : <ScanSearchIcon />}
            {scan ? "Plan a new full scan" : "Plan a full AI scan"}
          </Button>
          {plan.error && <Notice tone="error">{(plan.error as Error).message}</Notice>}
        </div>
      )}
    </div>
  );
}

function Running({ scan, onCancel, cancelling }: { scan: RfiFullScanDto; onCancel: () => void; cancelling: boolean }) {
  const planning = scan.status === "planning";
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2 text-xs">
        <Spinner />
        <span>{STAGE[scan.stage ?? ""] ?? scan.stage ?? "Working"}</span>
        <Button size="sm" variant="ghost" className="ml-auto" onClick={onCancel} disabled={cancelling}>
          <XIcon />
          Stop
        </Button>
      </div>
      <Progress value={scan.progress} />
      {!planning && (
        <p className="text-muted-foreground text-xs">
          {scan.tiles.done} of {scan.tiles.total} areas looked at
          {scan.tiles.failed ? `, ${scan.tiles.failed} failed` : ""} · {scan.findings} saved so far · spent{" "}
          {scan.spent.costUsd !== null ? money(scan.spent.costUsd) : `${tokens(scan.spent.inputTokens + scan.spent.outputTokens)} tokens`}
          {scan.limits?.budgetUsd ? ` of ${money(scan.limits.budgetUsd)}` : ""}
          {scan.useBatch ? " · batch mode can take a while between updates" : ""}
        </p>
      )}
      {planning && <p className="text-muted-foreground text-xs">No AI is used while planning.</p>}
    </div>
  );
}

function Planned({
  projectId,
  scan,
  models,
  prices,
  onStarted,
  onCancel,
}: {
  projectId: string;
  scan: RfiFullScanDto;
  models: RfiFullScanList["models"];
  prices: RfiFullScanList["prices"];
  onStarted: (s: RfiFullScanDto) => void;
  onCancel: () => void;
}) {
  const available = models.options.filter((o) => o.available);
  const [choice, setChoice] = useState(() => `${models.default.provider}|${models.default.model}`);
  const [provider, model] = choice.split("|") as [RfiReviewProvider, string];
  const [useBatch, setUseBatch] = useState(true);
  const price = prices[choice]?.[useBatch ? "batch" : "direct"] ?? null;
  const [budget, setBudget] = useState(() => {
    const high = prices[`${models.default.provider}|${models.default.model}`]?.batch?.high ?? scan.estimate?.costUsd?.high ?? null;
    return high !== null ? String(Math.max(1, Math.ceil(high * 1.2))) : "";
  });
  const key = useRef(newKey());
  const start = useMutation({
    mutationFn: () => {
      const amount = Number(budget);
      return api.startRfiFullScan(projectId, scan.id, { provider, model, useBatch, budgetUsd: amount > 0 ? amount : undefined }, key.current);
    },
    onSuccess: onStarted,
  });
  const est = scan.estimate;
  const nothing = scan.pairs.length === 0;

  return (
    <div className="flex flex-col gap-3 text-xs">
      {scan.catalogue && (
        <p className="text-muted-foreground leading-relaxed">
          Read {scan.catalogue.pages} page{scan.catalogue.pages === 1 ? "" : "s"}:{" "}
          {Object.entries(scan.catalogue.byKind)
            .sort((a, b) => b[1] - a[1])
            .map(([k, n]) => `${n} ${KIND_LABEL[k] ?? k}`)
            .join(", ")}
          . {scan.catalogue.withLevel} with a level, {scan.catalogue.withScale} with a printed scale,{" "}
          {scan.catalogue.withGrid} with a grid
          {scan.catalogue.excluded ? `; ${scan.catalogue.excluded} kept out (old revisions, RFIs, unprocessed)` : ""}.
        </p>
      )}

      <Section title={`${scan.pairs.length} sheet pair${scan.pairs.length === 1 ? "" : "s"} to compare`} defaultOpen={scan.pairs.length <= 6}>
        <ul className="flex flex-col gap-1">
          {scan.pairs.map((p) => (
            <li key={p.index} className="flex flex-wrap items-baseline gap-x-2">
              <span className="font-medium">
                {sheetName(p.a)} ↔ {sheetName(p.b)}
              </span>
              <span className="text-muted-foreground">
                {p.reason} · {p.tiles} area{p.tiles === 1 ? "" : "s"}
              </span>
            </li>
          ))}
        </ul>
      </Section>

      {scan.skipped.length > 0 && (
        <Section title={`Left out (${scan.skipped.reduce((n, s) => n + s.count, 0)})`}>
          <ul className="flex flex-col gap-1">
            {scan.skipped.map((s) => (
              <li key={s.reason}>
                <span className="font-medium">{s.count}×</span> {s.reason}
                {s.examples.length > 0 && <span className="text-muted-foreground"> — {s.examples.join(", ")}</span>}
              </li>
            ))}
          </ul>
        </Section>
      )}

      {nothing ? (
        <>
          {scan.notes.map((n) => (
            <Notice key={n} tone="error">
              {n}
            </Notice>
          ))}
          <Button size="sm" variant="ghost" className="self-start" onClick={onCancel}>
            Close this plan
          </Button>
        </>
      ) : (
        <div className="bg-card flex flex-col gap-2 rounded-md border p-3">
          {est && (
            <p className="flex items-start gap-1.5">
              <CoinsIcon className="mt-0.5 size-3.5 shrink-0" />
              <span>
                {est.calls} AI looks at {est.images} images (~{tokens(est.inputTokens + est.outputTokens)} tokens), plus {est.verifyCalls.low}–
                {est.verifyCalls.high} close-up checks depending on what it finds.{" "}
                {price ? (
                  <strong>
                    About {money(price.low)}–{money(price.high)} on {model}
                    {useBatch ? " in batch mode (half price, slower)" : ""}.
                  </strong>
                ) : (
                  <span className="text-muted-foreground">No price is known for {model}.</span>
                )}
              </span>
            </p>
          )}
          <div className="flex flex-wrap items-center gap-2">
            <Select value={choice} onValueChange={setChoice}>
              <SelectTrigger size="sm" className="w-56">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {available.map((o) => (
                  <SelectItem key={`${o.provider}|${o.model}`} value={`${o.provider}|${o.model}`} disabled={!o.priced}>
                    {o.model}
                    {o.priced ? "" : " (no price known)"}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <ToggleGroup
              type="single"
              variant="outline"
              size="sm"
              value={useBatch ? "batch" : "direct"}
              onValueChange={(v) => v && setUseBatch(v === "batch")}
              aria-label="How to send"
            >
              <ToggleGroupItem value="batch" className="text-xs" title="Half price; results can take minutes to hours">
                Batch
              </ToggleGroupItem>
              <ToggleGroupItem value="direct" className="text-xs" title="Full price; results as they come">
                Direct
              </ToggleGroupItem>
            </ToggleGroup>
          </div>
          <label className="flex flex-wrap items-center gap-2">
            <span>Budget ($)</span>
            <Input
              type="number"
              min={1}
              step={1}
              className="h-8 w-28"
              value={budget}
              onChange={(e) => setBudget(e.target.value)}
              aria-label="Budget in dollars"
            />
            <span className="text-muted-foreground">The scan stops before it would spend more, keeps what it found, and can be resumed.</span>
          </label>
          <div className="flex items-center gap-2">
            <Button size="sm" disabled={start.isPending || !(Number(budget) > 0) || available.length === 0} onClick={() => start.mutate()}>
              {start.isPending ? <Spinner /> : <PlayIcon />}
              Start full scan
            </Button>
            <Button size="sm" variant="ghost" onClick={onCancel}>
              Discard plan
            </Button>
          </div>
          {available.length === 0 && <Notice tone="error">No AI provider key is configured on the server.</Notice>}
          {start.error && <Notice tone="error">{(start.error as Error).message}</Notice>}
        </div>
      )}
    </div>
  );
}

function Finished({ projectId, scan, onResumed }: { projectId: string; scan: RfiFullScanDto; onResumed: (s: RfiFullScanDto) => void }) {
  const [budget, setBudget] = useState(() => String(Math.ceil((scan.limits?.budgetUsd ?? 5) * 1.5)));
  const resume = useMutation({
    mutationFn: () => api.resumeRfiFullScan(projectId, scan.id, Number(budget) > 0 ? { budgetUsd: Number(budget) } : {}),
    onSuccess: onResumed,
  });
  const stopped = scan.status !== "ready";
  return (
    <div className="flex flex-col gap-2 text-xs">
      <p>
        <strong>
          {scan.findings} new finding{scan.findings === 1 ? "" : "s"}
        </strong>{" "}
        from {scan.tiles.done} of {scan.tiles.total} areas · spent{" "}
        {scan.spent.costUsd !== null ? money(scan.spent.costUsd) : `${tokens(scan.spent.inputTokens + scan.spent.outputTokens)} tokens`}
        {scan.findings > 0 ? " — they are in “Needs your review” below." : "."}
      </p>
      {scan.summary && <Outcome summary={scan.summary} />}
      {stopped && (
        <div className="flex flex-col gap-2">
          <Notice tone="error">
            {scan.status === "failed"
              ? `The scan stopped: ${scan.error ?? "unknown error"}.`
              : "The scan stopped before it finished — usually the budget. Everything it found is saved."}
          </Notice>
          <label className="flex flex-wrap items-center gap-2">
            <span>New budget ($)</span>
            <Input type="number" min={1} className="h-8 w-28" value={budget} onChange={(e) => setBudget(e.target.value)} />
            <Button size="sm" disabled={resume.isPending} onClick={() => resume.mutate()}>
              {resume.isPending ? <Spinner /> : <RotateCcwIcon />}
              Resume
            </Button>
          </label>
          {resume.error && <Notice tone="error">{(resume.error as Error).message}</Notice>}
        </div>
      )}
      {scan.findings > 0 && (
        <MarkedUpPdfButton projectId={projectId} fullScanId={scan.id} label="Marked-up PDF of the findings" />
      )}
      <RfiDiagnosticsButton projectId={projectId} kind="full-scan" runId={scan.id} />
    </div>
  );
}

const AREA_LABELS: [string, string][] = [
  ["agree", "agree"],
  ["issues", "had a possible problem"],
  ["unclear", "the AI could not judge"],
  ["misaligned", "were not lined up"],
  ["invalid_location", "raised a problem it could not place"],
  ["unstated", "got no verdict"],
  ["failed", "failed"],
  ["pending", "not reached"],
];

/** What the run concluded beyond the count: a finding already on file is not
 * "no problem", and an area the AI could not judge is a gap, not agreement. */
function Outcome({ summary }: { summary: NonNullable<RfiFullScanDto["summary"]> }) {
  const areas = AREA_LABELS.filter(([key]) => (summary.areas[key] ?? 0) > 0).map(
    ([key, label]) => `${summary.areas[key]} ${label}`,
  );
  const gaps = (summary.areas.unclear ?? 0) + (summary.areas.misaligned ?? 0) + (summary.areas.unstated ?? 0);
  return (
    <div className="text-muted-foreground flex flex-col gap-1">
      {summary.foundAgain.length > 0 && (
        <div>
          <span className="text-foreground font-medium">
            {summary.foundAgain.length} already on file
          </span>{" "}
          — kept on the close look, not added again and not restored:
          <ul className="list-disc pl-5">
            {summary.foundAgain.map((f) => (
              <li key={f.fingerprint ?? f.subject}>
                “{f.subject}” is {f.where}.
              </li>
            ))}
          </ul>
        </div>
      )}
      <p>
        {summary.possibleProblems} possible problem{summary.possibleProblems === 1 ? "" : "s"} on the first look:{" "}
        {summary.newFindings} saved, {summary.foundAgain.length} already on file, {summary.rejected} rejected
        {summary.unclear > 0 ? `, ${summary.unclear} could not be decided close up` : ""}
        {summary.notChecked > 0 ? `, ${summary.notChecked} not yet checked` : ""}
        {summary.unplaceable > 0 ? `, ${summary.unplaceable} dropped (location unreadable)` : ""}.
      </p>
      {areas.length > 0 && <p>Areas: {areas.join(" · ")}.</p>}
      {gaps > 0 && (
        <p>
          {gaps} area{gaps === 1 ? " was" : "s were"} not judged — a gap in this scan, not agreement.
        </p>
      )}
      {summary.pagesRead !== null && (
        <p>
          Compared {summary.pagesCompared} of {summary.pagesRead} pages; the plan lists every page left out and why.
        </p>
      )}
    </div>
  );
}

function Notes({ notes }: { notes: string[] }) {
  return (
    <Section
      title={
        <>
          <TriangleAlertIcon className="size-3" />
          {notes.length} note{notes.length === 1 ? "" : "s"} about this scan
        </>
      }
    >
      <ul className="text-muted-foreground list-disc pl-5 leading-relaxed">
        {notes.map((n) => (
          <li key={n}>{n}</li>
        ))}
      </ul>
    </Section>
  );
}

function Section({ title, children, defaultOpen = false }: { title: React.ReactNode; children: React.ReactNode; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  const id = useMemo(() => newKey(), []);
  return (
    <Collapsible open={open} onOpenChange={setOpen} className="text-xs">
      <CollapsibleTrigger className="text-muted-foreground flex items-center gap-1 hover:underline" aria-controls={id}>
        {title}
        <ChevronDownIcon className={`size-3 transition-transform ${open ? "rotate-180" : ""}`} />
      </CollapsibleTrigger>
      <CollapsibleContent id={id} className="mt-1">
        {children}
      </CollapsibleContent>
    </Collapsible>
  );
}
