import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { PortionDto, PortionSummaryStatus } from "@cdip/shared";
import { api } from "@/api";
import { SummaryConfirm } from "@/components/SummaryConfirm";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { formatRanges, formatRangesFull, pagePrefix } from "@/lib/pageRanges";
import { cn } from "@/lib/utils";
import { useAppStore } from "@/store";

/**
 * The discipline categories (FR-15/16). Clicking one jumps the viewer to its
 * start page and switches the left panel to its summary.
 *
 * Summaries are NOT generated automatically — each category carries its own
 * button, and pressing it is the only thing that spends tokens on that
 * discipline. `summaryStatus` drives the chip and the polling.
 */

const CHIP: Record<
  PortionSummaryStatus,
  { label: string; variant: "secondary" | "warning" | "success" | "destructive" }
> = {
  none: { label: "No summary", variant: "secondary" },
  queued: { label: "Queued", variant: "warning" },
  running: { label: "Summarizing…", variant: "warning" },
  ready: { label: "Summary ready", variant: "success" },
  stale: { label: "Out of date", variant: "warning" },
  failed: { label: "Failed", variant: "destructive" },
};

/** Statuses where pressing the button does something. */
const CAN_START: PortionSummaryStatus[] = ["none", "stale", "failed"];

function buttonLabel(status: PortionSummaryStatus): string {
  if (status === "stale") return "Refresh";
  if (status === "failed") return "Try again";
  if (status === "ready") return "Regenerate";
  return "Generate summary";
}

export function PortionsPanel({ projectId }: { projectId: string }) {
  const queryClient = useQueryClient();
  const selectedPortionId = useAppStore((s) => s.selectedPortionId);
  const selectPortion = useAppStore((s) => s.selectPortion);
  const setPageFilter = useAppStore((s) => s.setPageFilter);
  const requestJump = useAppStore((s) => s.requestJump);

  const documents = useQuery({
    queryKey: ["documents", projectId],
    queryFn: () => api.listDocuments(projectId),
  });
  const processing = documents.data?.some(
    (d) => d.status === "uploaded" || d.status === "processing",
  );

  // The manifest carries each page's discipline, which is what the labels and
  // the viewer filter need — a portion's start/end is only the outer span.
  const manifest = useQuery({
    queryKey: ["manifest", projectId],
    queryFn: () => api.manifest(projectId),
  });

  const portions = useQuery({
    queryKey: ["portions", projectId],
    queryFn: () => api.listPortions(projectId),
    // Poll while a summary is in flight, or while a document is still landing.
    refetchInterval: (query) => {
      const busy = query.state.data?.some(
        (p) => p.summaryStatus === "queued" || p.summaryStatus === "running",
      );
      if (busy) return 3000;
      return processing ? 5000 : false;
    },
  });

  // Which run the user is being asked to confirm: a discipline id, "project"
  // for the rollup, or null when no dialog is open.
  const [pending, setPending] = useState<string | null>(null);
  const confirmingProject = pending === "project";

  const estimate = useQuery({
    queryKey: ["summary-estimate", projectId, pending],
    queryFn: () =>
      confirmingProject
        ? api.projectSummaryEstimate(projectId)
        : api.summaryEstimate(projectId, pending as string),
    enabled: pending !== null,
    staleTime: 30_000,
  });

  const summarize = useMutation({
    mutationFn: (portionId: string) => api.summarizePortion(projectId, portionId),
    onSuccess: () => {
      setPending(null);
      void queryClient.invalidateQueries({ queryKey: ["portions", projectId] });
    },
  });

  const anyReady =
    portions.data?.some((p) => p.summaryStatus === "ready" || p.summaryStatus === "stale") ??
    false;
  const projectSummary = useMutation({
    mutationFn: () => api.generateProjectSummary(projectId),
    onSuccess: () => {
      setPending(null);
      void queryClient.invalidateQueries({ queryKey: ["summaries", projectId] });
    },
  });

  /** Combined page numbers actually carrying this discipline, in order. */
  function pagesOf(portion: PortionDto): number[] {
    return (manifest.data ?? [])
      .filter((entry) => (entry.discipline ?? "unclassified") === portion.discipline)
      .map((entry) => entry.combinedPageNumber)
      .sort((a, b) => a - b);
  }

  /**
   * Clicking a category selects its summary AND filters the viewer to its
   * sheets; clicking it again clears both. The jump targets the first page the
   * discipline actually appears on, not the span start.
   */
  function open(portion: PortionDto) {
    if (portion.id === selectedPortionId) {
      selectPortion(null); // toggle back to the project summary
      setPageFilter(null);
      return;
    }
    selectPortion(portion.id);
    if (portion.discipline) {
      setPageFilter({ discipline: portion.discipline, label: portion.name });
    }
    requestJump(pagesOf(portion)[0] ?? portion.startPage);
  }

  return (
    <section>
      <ul className="px-2">
        {portions.data?.map((portion) => {
          const chip = CHIP[portion.summaryStatus];
          const busy =
            portion.summaryStatus === "queued" || portion.summaryStatus === "running";
          // Fall back to the span only while the manifest is still loading.
          const pages = pagesOf(portion);
          const range = pages.length
            ? `${pagePrefix(pages)} ${formatRanges(pages)}`
            : `pp. ${portion.startPage}–${portion.endPage}`;
          return (
            <li key={portion.id} className="mb-1">
              <div
                className={cn(
                  "rounded-md border px-2 py-1.5 transition",
                  portion.id === selectedPortionId
                    ? "border-primary/30 bg-accent"
                    : "border-transparent hover:bg-muted",
                )}
              >
                <button
                  className="flex w-full items-center gap-2 text-left"
                  onClick={() => open(portion)}
                  title={
                    pages.length
                      ? `Show only the ${portion.name} sheets (pages ${formatRangesFull(pages)})`
                      : `Jump to combined page ${portion.startPage}`
                  }
                >
                  <span
                    className={cn(
                      "min-w-0 flex-1 truncate text-sm",
                      portion.id === selectedPortionId && "font-semibold",
                    )}
                  >
                    {portion.name}
                  </span>
                  <span
                    className="text-muted-foreground shrink-0 text-xs tabular-nums"
                    title={pages.length ? `Pages ${formatRangesFull(pages)}` : undefined}
                  >
                    {range}
                  </span>
                </button>

                {/* The pane is narrow: the count takes its own line so the
                    status chip and the action button never get squeezed. */}
                <div className="mt-1 flex flex-wrap items-center gap-2">
                  <span className="text-muted-foreground w-full text-[11px] tabular-nums">
                    {pages.length || portion.pageCount} sheet
                    {(pages.length || portion.pageCount) === 1 ? "" : "s"}
                    {portion.sheetNumberSample && ` · ${portion.sheetNumberSample}`}
                  </span>
                  <Badge variant={chip.variant} className="text-[11px]">
                    {chip.label}
                  </Badge>
                  <Button
                    variant="outline"
                    size="sm"
                    data-tour="generate-summary"
                    className="ml-auto h-6 shrink-0 px-2 text-[11px]"
                    disabled={busy || summarize.isPending}
                    onClick={() => setPending(portion.id)}
                    title={
                      CAN_START.includes(portion.summaryStatus)
                        ? `Summarize the ${portion.name} sheets`
                        : "Regenerate this summary"
                    }
                  >
                    {busy ? "Working…" : buttonLabel(portion.summaryStatus)}
                  </Button>
                </div>

                {portion.summaryStatus === "failed" && portion.summaryError && (
                  <p className="mt-1 text-[11px] text-destructive">{portion.summaryError}</p>
                )}
                {portion.summaryStatus === "stale" && (
                  <p className="mt-1 text-[11px] text-muted-foreground">
                    These sheets changed since the summary was written.
                  </p>
                )}
              </div>
            </li>
          );
        })}

        {portions.data?.length === 0 && (
          <li className="text-muted-foreground px-2 py-1 text-sm">
            {processing
              ? "Processing…"
              : "No categories yet — define the title-block region under Quick actions."}
          </li>
        )}
      </ul>

      {anyReady && (
        <div className="mt-2 border-t px-4 py-3">
          <Button
            variant="outline"
            size="sm"
            className="w-full text-xs"
            onClick={() => setPending("project")}
            disabled={projectSummary.isPending}
            title="Combine the discipline summaries into one project summary"
          >
            {projectSummary.isPending ? "Queuing…" : "Generate project summary"}
          </Button>
        </div>
      )}

      {summarize.isError && (
        <p className="text-destructive px-4 pb-2 text-[11px]">
          {(summarize.error as Error).message}
        </p>
      )}

      {pending && (
        <SummaryConfirm
          title={
            confirmingProject
              ? "Generate project summary?"
              : `Summarize ${portions.data?.find((p) => p.id === pending)?.name ?? "this discipline"}?`
          }
          estimate={estimate.data}
          isLoading={estimate.isLoading}
          error={estimate.error}
          busy={summarize.isPending || projectSummary.isPending}
          onCancel={() => setPending(null)}
          onConfirm={() =>
            confirmingProject ? projectSummary.mutate() : summarize.mutate(pending)
          }
        />
      )}
    </section>
  );
}
