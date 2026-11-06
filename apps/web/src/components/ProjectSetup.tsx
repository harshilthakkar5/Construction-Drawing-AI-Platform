import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  DEFAULT_PROCESS_DISCIPLINES,
  PROJECT_ROLES,
  processingKey,
  processingLabel,
} from "@cdip/shared";
import {
  ArrowLeftIcon,
  ArrowRightIcon,
  CheckIcon,
  LayersIcon,
  ScanLineIcon,
  UploadIcon,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api } from "@/api";
import { DocumentsPanel } from "@/components/DocumentsPanel";
import { RegionBanner } from "@/components/RegionBanner";
import { processingCost, useProcessing, useStartProcessing } from "@/components/Processing";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Progress } from "@/components/ui/progress";
import { cn } from "@/lib/utils";

/**
 * First-run setup for a new project: upload the PDFs, mark the title block,
 * let the sheets categorise. The three steps already existed as scattered
 * controls in the workspace; the order matters (a region needs a rendered
 * page, categories need a region), so a new project walks them in sequence
 * instead of guessing.
 *
 * Nothing here is a new capability: each step drives the same endpoints the
 * workspace does. Progress is read from the server, never from local state, so
 * closing the tab mid-setup resumes exactly where it stopped.
 */
const SKIP_KEY = (projectId: string) => `cdip-setup-skipped:${projectId}`;

export const setupSkipped = (projectId: string) =>
  localStorage.getItem(SKIP_KEY(projectId)) === "1";

export const skipSetup = (projectId: string) => localStorage.setItem(SKIP_KEY(projectId), "1");

export const resumeSetup = (projectId: string) => localStorage.removeItem(SKIP_KEY(projectId));

/** Where a project is in the sequence, derived entirely from server state. */
export function useSetupProgress(projectId: string) {
  const documents = useQuery({
    queryKey: ["documents", projectId],
    queryFn: () => api.listDocuments(projectId),
    refetchInterval: (query) =>
      query.state.data?.some((d) => d.status === "uploaded" || d.status === "processing")
        ? 2000
        : false,
  });
  const region = useQuery({
    queryKey: ["region", projectId],
    queryFn: () => api.getRegion(projectId),
    refetchInterval: (query) =>
      query.state.data?.scrapeStatus === "running" || query.state.data?.scrapeStatus === "pending"
        ? 2000
        : false,
  });

  const live = (documents.data ?? []).filter((d) => !d.supersededAt);
  const uploaded = live.length > 0;
  const hasRegion = Boolean(region.data);
  // The region is drawn on a rendered page, so step 2 waits for one. A document
  // goes back to "processing" for its full pass; its pages are still viewable.
  const processed = live.some((d) => d.status === "completed") || hasRegion;
  const scraped = region.data?.scrapeStatus === "completed";

  return {
    loading: documents.isLoading || region.isLoading,
    documents: live,
    region: region.data,
    uploaded,
    processed,
    hasRegion,
    scraped,
    complete: uploaded && processed && hasRegion && scraped,
  };
}

const STEPS = [
  { key: "upload", label: "Upload drawings", icon: UploadIcon },
  { key: "region", label: "Mark the title block", icon: ScanLineIcon },
  { key: "categories", label: "Check the disciplines", icon: LayersIcon },
] as const;

export function ProjectSetup({
  projectId,
  onFinish,
  onSkip,
}: {
  projectId: string;
  onFinish: () => void;
  onSkip: () => void;
}) {
  const progress = useSetupProgress(projectId);
  const processing = useProcessing(projectId);
  // The workspace opens once the disciplines started here are read in full.
  const busy = processing.data?.disciplines.some((d) => d.chosen && d.processed < d.pages) ?? false;
  const done = [progress.uploaded && progress.processed, progress.hasRegion, progress.scraped && !busy];
  // Open on the first unfinished step; the user can still walk back.
  const [step, setStep] = useState(() => {
    const first = done.findIndex((d) => !d);
    return first === -1 ? STEPS.length - 1 : first;
  });

  const canContinue = done[step] ?? false;
  const last = step === STEPS.length - 1;

  return (
    <div className="h-full overflow-y-auto p-4 md:p-6">
      <div className="mx-auto flex max-w-3xl flex-col gap-6">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Set up this project</h1>
          <p className="text-muted-foreground mt-1 text-sm">
            Three steps, then the disciplines you pick are read in full. You can skip and come
            back: the workspace has all of these controls too, and nothing is read in full until
            you ask.
          </p>
        </div>

        {/* Step rail */}
        <ol className="flex items-center gap-2">
          {STEPS.map((s, index) => {
            const complete = done[index] ?? false;
            const active = index === step;
            return (
              <li key={s.key} className="flex flex-1 items-center gap-2">
                <button
                  className="flex min-w-0 items-center gap-2 text-left"
                  onClick={() => setStep(index)}
                >
                  <span
                    className={cn(
                      "grid size-8 shrink-0 place-items-center rounded-full border text-sm font-medium transition-colors",
                      complete && "bg-success/15 border-success/30 text-success",
                      !complete && active && "bg-primary text-primary-foreground border-primary",
                      !complete && !active && "text-muted-foreground",
                    )}
                  >
                    {complete ? <CheckIcon className="size-4" /> : index + 1}
                  </span>
                  <span
                    className={cn(
                      "hidden truncate text-sm sm:inline",
                      active ? "font-medium" : "text-muted-foreground",
                    )}
                  >
                    {s.label}
                  </span>
                </button>
                {index < STEPS.length - 1 && (
                  <span
                    className={cn("h-px flex-1", complete ? "bg-success/40" : "bg-border")}
                    aria-hidden
                  />
                )}
              </li>
            );
          })}
        </ol>

        {step === 0 && <UploadStep projectId={projectId} progress={progress} />}
        {step === 1 && <RegionStep projectId={projectId} progress={progress} />}
        {step === 2 && <CategoriesStep projectId={projectId} progress={progress} />}

        <div className="flex items-center gap-2">
          <Button
            variant="ghost"
            onClick={() => setStep((s) => Math.max(0, s - 1))}
            disabled={step === 0}
          >
            <ArrowLeftIcon />
            Back
          </Button>
          <Button variant="ghost" className="text-muted-foreground" onClick={onSkip}>
            Skip setup
          </Button>
          <Button
            className="ml-auto"
            disabled={!canContinue}
            onClick={() => (last ? onFinish() : setStep((s) => s + 1))}
            title={canContinue ? undefined : busy ? "Wait until processing finishes" : "Finish this step first"}
          >
            {last ? "Open the workspace" : "Next"}
            <ArrowRightIcon />
          </Button>
        </div>
      </div>
    </div>
  );
}

type Progress = ReturnType<typeof useSetupProgress>;

function StepCard({
  title,
  description,
  children,
  state,
}: {
  title: string;
  description: string;
  children: React.ReactNode;
  state: React.ReactNode;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {state}
        {children}
      </CardContent>
    </Card>
  );
}

function UploadStep({ projectId, progress }: { projectId: string; progress: Progress }) {
  const processing = progress.documents.filter(
    (d) => d.status === "uploaded" || d.status === "processing",
  ).length;

  return (
    <StepCard
      title="Upload the drawing PDFs"
      description="Add every PDF in the set. They upload straight to storage and each page is made viewable, so a 1 GB set is fine. The full reading (OCR and search) waits until you choose disciplines in step 3."
      state={
        <Badge variant={progress.processed ? "success" : "secondary"}>
          {progress.processed
            ? `${progress.documents.length} uploaded, at least one processed`
            : processing > 0
              ? `Processing ${processing} document${processing === 1 ? "" : "s"}…`
              : "Nothing uploaded yet"}
        </Badge>
      }
    >
      <div className="rounded-lg border">
        <DocumentsPanel projectId={projectId} />
      </div>
      {!progress.processed && progress.uploaded && (
        <p className="text-muted-foreground text-xs">
          The next step needs one processed page to draw the box on. This usually takes a
          minute or two per document.
        </p>
      )}
    </StepCard>
  );
}

function RegionStep({ projectId, progress }: { projectId: string; progress: Progress }) {
  return (
    <StepCard
      title="Mark the title block"
      description="Drag one box over a sheet number. It is applied to every page of every PDF and sorts the set into disciplines."
      state={
        <Badge variant={progress.hasRegion ? "success" : "secondary"}>
          {progress.hasRegion
            ? `Region saved (v${progress.region?.version ?? 1})`
            : progress.processed
              ? "No region yet"
              : "Waiting for a processed page"}
        </Badge>
      }
    >
      <RegionBanner projectId={projectId} align="start" />
    </StepCard>
  );
}

function CategoriesStep({ projectId, progress }: { projectId: string; progress: Progress }) {
  const queryClient = useQueryClient();
  const state = useProcessing(projectId);
  const start = useStartProcessing(projectId);
  const manifest = useQuery({
    queryKey: ["manifest", projectId],
    queryFn: () => api.manifest(projectId),
    refetchInterval: progress.scraped ? false : 3000,
  });

  // The scrape rewrites every page's discipline, so both lists are stale the
  // moment it finishes — refetch once on that transition rather than polling on.
  const scraped = progress.scraped;
  useEffect(() => {
    if (!scraped) return;
    for (const key of ["portions", "manifest", "processing"]) {
      void queryClient.invalidateQueries({ queryKey: [key, projectId] });
    }
  }, [scraped, projectId, queryClient]);

  // General, Architectural and Structural are ticked to begin with.
  const [ticked, setTicked] = useState<Set<string>>(() => new Set(DEFAULT_PROCESS_DISCIPLINES));
  const [open, setOpen] = useState<string | null>(null);
  const move = useMutation({
    mutationFn: ({ page, discipline }: { page: number; discipline: string }) =>
      api.setPageDiscipline(projectId, page, discipline),
    onSuccess: () => {
      for (const key of ["region", "manifest", "processing", "portions"]) {
        void queryClient.invalidateQueries({ queryKey: [key, projectId] });
      }
    },
  });

  const region = progress.region;
  const rows = (state.data?.disciplines ?? []).filter((d) => d.pages > 0);
  const scanned = region?.scrapedPages ?? 0;
  const totalPages = region?.totalPages ?? 0;
  const percent = totalPages > 0 ? Math.round((scanned / totalPages) * 100) : 0;
  const toStart = rows.filter((d) => ticked.has(d.discipline) && !d.chosen).map((d) => d.discipline);
  const eta = useProcessingEta(rows.filter((d) => d.chosen));

  return (
    <StepCard
      title="Check the disciplines"
      description="Each sheet number is read from your box and sorted into a discipline. The ticked disciplines are read in full (OCR and search) when you press Start; the others can be processed any time with Process now in Summary & categories."
      state={
        <Badge variant={progress.scraped ? (rows.length > 0 ? "success" : "warning") : "secondary"}>
          {progress.scraped
            ? rows.length > 0
              ? `${rows.length} discipline${rows.length === 1 ? "" : "s"} found`
              : "No disciplines found. Try adjusting the box."
            : region
              ? `Reading sheet numbers… ${scanned}/${totalPages || "?"}`
              : "Waiting for a region"}
        </Badge>
      }
    >
      {!progress.scraped && region && <Progress value={percent} />}

      {eta && (
        <div className="space-y-1.5">
          <Progress value={Math.round((eta.done / eta.total) * 100)} />
          <p className="text-muted-foreground text-xs tabular-nums">
            {eta.done} of {eta.total} pages processed · {eta.total - eta.done} waiting
            {eta.minutesLeft != null && ` · about ${eta.minutesLeft} min left`}. The workspace opens when this finishes.
          </p>
        </div>
      )}

      {rows.length > 0 && (
        <ul className="divide-y rounded-lg border">
          {rows.map((row) => {
            const sheets = (manifest.data ?? []).filter(
              (e) => processingKey(e.discipline) === row.discipline,
            );
            const done = row.processed >= row.pages;
            return (
              <li key={row.discipline} className="px-3 py-2 text-sm">
                <div className="flex items-center gap-3">
                  <Checkbox
                    id={`process-${row.discipline}`}
                    checked={row.chosen || ticked.has(row.discipline)}
                    disabled={row.chosen}
                    onCheckedChange={(on) =>
                      setTicked((prev) => {
                        const next = new Set(prev);
                        if (on) next.add(row.discipline);
                        else next.delete(row.discipline);
                        return next;
                      })
                    }
                    aria-label={`Process ${processingLabel(row.discipline)}`}
                  />
                  <label htmlFor={`process-${row.discipline}`} className="min-w-0 flex-1 truncate font-medium">
                    {processingLabel(row.discipline)}
                  </label>
                  <span className="text-muted-foreground text-xs tabular-nums" title={processingCost(row)?.title}>
                    {done
                      ? `Processed · ${processingCost(row)?.usd ?? "$0.00"}`
                      : row.chosen
                        ? `Processing ${row.processed}/${row.pages}`
                        : "Not processed"}
                  </span>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-7 text-xs"
                    onClick={() => setOpen(open === row.discipline ? null : row.discipline)}
                    aria-expanded={open === row.discipline}
                  >
                    {row.pages} sheet{row.pages === 1 ? "" : "s"}
                  </Button>
                </div>
                {open === row.discipline && (
                  <ul className="mt-2 max-h-64 space-y-1 overflow-y-auto pl-7">
                    {sheets.map((sheet) => (
                      <li key={sheet.combinedPageNumber} className="flex items-center gap-2 text-xs">
                        <span className="sheet-reference min-w-0 flex-1 truncate">
                          {sheet.sheetNumber ?? `Page ${sheet.combinedPageNumber}`}
                        </span>
                        <Select
                          value={sheet.discipline && sheet.discipline !== "unclassified" ? sheet.discipline : undefined}
                          onValueChange={(discipline) => move.mutate({ page: sheet.combinedPageNumber, discipline })}
                          disabled={move.isPending}
                        >
                          <SelectTrigger size="sm" className="h-7 w-44 text-xs" aria-label="Move this sheet to">
                            <SelectValue placeholder="Move to…" />
                          </SelectTrigger>
                          <SelectContent>
                            {[...PROJECT_ROLES, { value: "other", label: "Other" }].map((r) => (
                              <SelectItem key={r.value} value={r.value} className="text-xs">
                                {r.label}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      </li>
                    ))}
                  </ul>
                )}
              </li>
            );
          })}
        </ul>
      )}

      {rows.length > 0 && (
        <div className="flex flex-wrap items-center gap-3">
          <Button disabled={!toStart.length || start.isPending} onClick={() => start.mutate({ disciplines: toStart })}>
            {start.isPending ? "Starting…" : "Start processing"}
          </Button>
          <p className="text-muted-foreground text-xs">
            {toStart.length
              ? `${toStart.map(processingLabel).join(", ")} will be read in full.`
              : "Nothing new ticked. Untouched disciplines keep a Process now button."}
          </p>
        </div>
      )}
      {move.isError && <p className="text-destructive text-xs">{(move.error as Error).message}</p>}
      {start.isError && <p className="text-destructive text-xs">{(start.error as Error).message}</p>}

      {progress.scraped && (region?.notFoundPages ?? 0) > 0 && (
        <p className="text-muted-foreground text-xs">
          {region?.notFoundPages} sheet{region?.notFoundPages === 1 ? "" : "s"} came back empty.
          Step back and adjust the box if a discipline looks wrong.
        </p>
      )}
    </StepCard>
  );
}

/** Pages done and waiting across the disciplines being processed, and the time
 * left at the rate seen since this screen opened. Null when nothing is running. */
function useProcessingEta(chosen: { pages: number; processed: number }[]) {
  const total = chosen.reduce((sum, d) => sum + d.pages, 0);
  const done = chosen.reduce((sum, d) => sum + d.processed, 0);
  const first = useRef<{ at: number; done: number } | null>(null);
  if (total === 0 || done >= total) {
    first.current = null;
    return null;
  }
  first.current ??= { at: Date.now(), done };
  const gained = done - first.current.done;
  const minutesLeft =
    gained > 0 ? Math.max(1, Math.round(((Date.now() - first.current.at) / gained) * (total - done) / 60_000)) : null;
  return { total, done, minutesLeft };
}
