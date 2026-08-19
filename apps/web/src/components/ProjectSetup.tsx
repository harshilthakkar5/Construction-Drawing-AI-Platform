import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeftIcon,
  ArrowRightIcon,
  CheckIcon,
  LayersIcon,
  ScanLineIcon,
  UploadIcon,
} from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "@/api";
import { DocumentsPanel } from "@/components/DocumentsPanel";
import { RegionBanner } from "@/components/RegionBanner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { formatRanges, pagePrefix } from "@/lib/pageRanges";
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
  // The region is drawn on a rendered page, so step 2 waits for one.
  const processed = live.some((d) => d.status === "completed");
  const hasRegion = Boolean(region.data);
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
  { key: "categories", label: "Categorise sheets", icon: LayersIcon },
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
  const done = [progress.uploaded && progress.processed, progress.hasRegion, progress.scraped];
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
            Three steps and the drawings are searchable. You can skip and come back — the
            workspace has all of these controls too.
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
            title={canContinue ? undefined : "Finish this step first"}
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
      description="Add every PDF in the set. They upload straight to storage and are processed page by page, so a 1 GB set is fine."
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
          The next step needs one processed page to draw the box on — this usually takes a
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
      description="Drag one box over a sheet number. It is applied to every page of every PDF — that one box is what sorts the set into disciplines."
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
  const portions = useQuery({
    queryKey: ["portions", projectId],
    queryFn: () => api.listPortions(projectId),
    refetchInterval: progress.scraped ? false : 3000,
  });
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
    void queryClient.invalidateQueries({ queryKey: ["portions", projectId] });
    void queryClient.invalidateQueries({ queryKey: ["manifest", projectId] });
  }, [scraped, projectId, queryClient]);

  const region = progress.region;
  const found = portions.data?.length ?? 0;
  const scanned = region?.scrapedPages ?? 0;
  const totalPages = region?.totalPages ?? 0;
  const percent = totalPages > 0 ? Math.round((scanned / totalPages) * 100) : 0;

  return (
    <StepCard
      title="Categorise the sheets"
      description="Each sheet number is read out of the box you drew and mapped to a discipline. Nothing is summarized yet — that stays your call, per category."
      state={
        <Badge
          variant={
            progress.scraped ? (found > 0 ? "success" : "warning") : "secondary"
          }
        >
          {progress.scraped
            ? found > 0
              ? `${found} categor${found === 1 ? "y" : "ies"} detected`
              : "No categories detected — try adjusting the box"
            : region
              ? `Scanning sheets… ${scanned}/${totalPages || "?"}`
              : "Waiting for a region"}
        </Badge>
      }
    >
      {!progress.scraped && region && <Progress value={percent} />}

      {portions.data && portions.data.length > 0 && (
        <ul className="divide-y rounded-lg border">
          {portions.data.map((portion) => {
            const pages = (manifest.data ?? [])
              .filter((e) => (e.discipline ?? "unclassified") === portion.discipline)
              .map((e) => e.combinedPageNumber);
            return (
              <li key={portion.id} className="flex items-center gap-3 px-3 py-2 text-sm">
                <span className="min-w-0 flex-1 truncate font-medium">{portion.name}</span>
                <span className="text-muted-foreground text-xs tabular-nums">
                  {pages.length || portion.pageCount} sheet
                  {(pages.length || portion.pageCount) === 1 ? "" : "s"}
                </span>
                <span className="text-muted-foreground w-40 truncate text-right text-xs tabular-nums">
                  {pages.length ? `${pagePrefix(pages)} ${formatRanges(pages, 2)}` : ""}
                </span>
              </li>
            );
          })}
        </ul>
      )}

      {progress.scraped && (region?.notFoundPages ?? 0) > 0 && (
        <p className="text-muted-foreground text-xs">
          {region?.notFoundPages} sheet{region?.notFoundPages === 1 ? "" : "s"} came back empty.
          Step back and adjust the box if a discipline looks wrong.
        </p>
      )}
    </StepCard>
  );
}
