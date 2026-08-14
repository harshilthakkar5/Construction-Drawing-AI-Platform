import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckIcon, ChevronDownIcon, RefreshCwIcon, SparklesIcon } from "lucide-react";
import { useEffect, useState } from "react";
import type { SummaryItem } from "@cdip/shared";
import { api } from "@/api";
import { Spinner } from "@/components/shared";
import { SummaryConfirm } from "@/components/SummaryConfirm";
import { Button } from "@/components/ui/button";
import { useAppStore } from "@/store";

/** How long the panel keeps showing "Summarizing…" before giving up waiting. */
const REBUILD_TIMEOUT_MS = 5 * 60 * 1000;

/** Highlights shown before "Show N more" — a long list buries the overview. */
const COLLAPSED_ITEMS = 5;

/**
 * FR-10..12 + FR-18/19: shows the project summary by default and the selected
 * portion's summary when one is picked (FR-16). Every summary item cites
 * chunk IDs; clicking it jumps the viewer to the item's source page AND
 * highlights the first cited chunk's bounding box (resolved via the
 * chunk-location endpoint — the same chain as chat citations).
 *
 * Summaries are user-approved: nothing is generated until a category's button
 * (PortionsPanel) is pressed, so "no summary yet" is a normal resting state
 * here, not a fault to diagnose.
 */
export function SummaryPanel({ projectId }: { projectId: string }) {
  const selectedPortionId = useAppStore((s) => s.selectedPortionId);
  const requestJump = useAppStore((s) => s.requestJump);

  async function jumpToItem(item: SummaryItem) {
    const chunkId = item.chunkIds[0];
    if (chunkId) {
      try {
        const location = await api.chunkLocation(projectId, chunkId);
        requestJump(location.combinedPageNumber, location.bbox);
        return;
      } catch {
        // fall through to the plain jump — stale chunk after a rebuild
      }
    }
    requestJump(item.page);
  }

  // Poll only while a document is still processing (summaries are written by
  // the job that runs after processing). Once everything is completed we stop
  // rather than re-requesting /summaries forever.
  const documents = useQuery({
    queryKey: ["documents", projectId],
    queryFn: () => api.listDocuments(projectId),
  });
  const processing = documents.data?.some(
    (d) => d.status === "uploaded" || d.status === "processing",
  );

  // True from the moment a rebuild is queued until the summary lands (or the
  // wait times out) — the job runs in the worker, so the button has to keep
  // showing progress across the queue → page → rollup tiers.
  const [rebuilding, setRebuilding] = useState(false);

  // Portions carry the summary lifecycle now: a run is in flight when one of
  // them is queued/running, and that is the only reason to keep polling.
  const portions = useQuery({
    queryKey: ["portions", projectId],
    queryFn: () => api.listPortions(projectId),
  });
  const summaryRunning =
    portions.data?.some(
      (p) => p.summaryStatus === "queued" || p.summaryStatus === "running",
    ) ?? false;

  const summaries = useQuery({
    queryKey: ["summaries", projectId],
    queryFn: () => api.listSummaries(projectId),
    refetchInterval: (query) => {
      if (rebuilding || summaryRunning) return 3000; // a run is in flight
      if (query.state.data?.some((s) => s.level === "project")) return false; // done
      return processing ? 5000 : false; // idle: nothing will appear unasked
    },
  });

  // Only asked for when a run has produced nothing: turns "no summary" into the
  // actual reason (SUMMARIES_ENABLED=false, no API key…). Never asked when
  // simply nobody has pressed a button yet — that is not a fault.
  const hasAnySummary = (summaries.data?.length ?? 0) > 0;
  const everRequested =
    portions.data?.some((p) => p.summaryStatus !== "none") ?? false;
  const status = useQuery({
    queryKey: ["summary-status", projectId],
    queryFn: () => api.summaryStatus(projectId),
    enabled:
      !summaries.isLoading && !hasAnySummary && everRequested && !processing && !rebuilding,
  });

  const queryClient = useQueryClient();
  const selectedPortion = portions.data?.find((p) => p.id === selectedPortionId) ?? null;

  /**
   * The one button in this panel. Which job it starts depends on what is
   * selected: a discipline summarizes just its own sheets, the project view
   * rolls up the discipline summaries that already exist. Neither ever runs on
   * its own — that is the point of this change.
   */
  // Cost confirmation before spending: same dialog the category list uses.
  const [confirming, setConfirming] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const estimate = useQuery({
    queryKey: ["summary-estimate", projectId, selectedPortionId ?? "project"],
    queryFn: () =>
      selectedPortionId
        ? api.summaryEstimate(projectId, selectedPortionId)
        : api.projectSummaryEstimate(projectId),
    enabled: confirming,
    staleTime: 30_000,
  });

  const generate = useMutation({
    mutationFn: () =>
      selectedPortionId
        ? api.summarizePortion(projectId, selectedPortionId)
        : api.generateProjectSummary(projectId),
    onSuccess: () => {
      setConfirming(false);
      setRebuilding(true);
      void queryClient.invalidateQueries({ queryKey: ["portions", projectId] });
      void queryClient.invalidateQueries({ queryKey: ["summaries", projectId] });
      void queryClient.invalidateQueries({ queryKey: ["summary-status", projectId] });
    },
  });

  const summary = selectedPortionId
    ? summaries.data?.find(
        (s) => s.level === "portion" && s.portionId === selectedPortionId,
      )
    : summaries.data?.find((s) => s.level === "project");
  const heading = selectedPortionId
    ? `${selectedPortion?.name ?? "Portion"} summary`
    : "Project summary";

  // Stop waiting once the run produced the summary for THIS level, or after the
  // timeout (a failed job never writes anything — don't spin forever).
  useEffect(() => {
    if (!rebuilding) return;
    if (summary) {
      setRebuilding(false);
      return;
    }
    const timer = setTimeout(() => setRebuilding(false), REBUILD_TIMEOUT_MS);
    return () => clearTimeout(timer);
  }, [rebuilding, summary]);

  const waiting =
    rebuilding ||
    selectedPortion?.summaryStatus === "queued" ||
    selectedPortion?.summaryStatus === "running";
  const stale = selectedPortionId
    ? selectedPortion?.summaryStatus === "stale"
    : (summary?.summary.stale ?? false);
  const portionsReady =
    portions.data?.some(
      (p) => p.summaryStatus === "ready" || p.summaryStatus === "stale",
    ) ?? false;
  // The project rollup combines discipline summaries, so it needs one first.
  const canGenerate = selectedPortionId
    ? (selectedPortion?.pageCount ?? 0) > 0
    : portionsReady;

  const items = summary?.summary.items ?? [];
  const shownItems = expanded ? items : items.slice(0, COLLAPSED_ITEMS);

  return (
    <section className="px-4">
      <div className="flex items-center justify-between gap-3">
        <h3 className="flex items-center gap-2 text-sm font-semibold">
          <SparklesIcon className="text-muted-foreground size-4" />
          {heading}
        </h3>
        {summary && (
          <Button
            variant="outline"
            size="sm"
            className="text-xs"
            onClick={() => setConfirming(true)}
            disabled={generate.isPending || waiting}
          >
            {generate.isPending || waiting ? <Spinner /> : <RefreshCwIcon />}
            {generate.isPending || waiting ? "Working…" : "Regenerate"}
          </Button>
        )}
      </div>

      <div className="mt-3">
        {!summary && (
          <div className="space-y-2">
            <p className="text-muted-foreground text-sm">
              {summaries.isLoading
                ? "Loading…"
                : waiting
                  ? "Summarizing… page summaries first, then the rollups. This can take a while on a large set."
                  : selectedPortion?.summaryStatus === "failed"
                    ? (selectedPortion.summaryError ?? "The last run failed.")
                    : selectedPortionId
                      ? `No summary for ${selectedPortion?.name ?? "this discipline"} yet — generate one when you need it.`
                      : portionsReady
                        ? "No project summary yet — it combines the discipline summaries you have generated."
                        : (status.data?.hint ??
                          "No summary yet. Generate a discipline summary from the categories above, then roll them up into a project summary.")}
            </p>
            {!summaries.isLoading && canGenerate && (
              <Button
                variant="outline"
                size="sm"
                className="text-xs"
                onClick={() => setConfirming(true)}
                disabled={generate.isPending || waiting}
              >
                {(generate.isPending || waiting) && <Spinner />}
                {generate.isPending
                  ? "Queuing…"
                  : waiting
                    ? "Summarizing…"
                    : selectedPortionId
                      ? `Generate ${selectedPortion?.name ?? "portion"} summary`
                      : "Generate project summary"}
              </Button>
            )}
            {generate.isError && (
              <p className="text-xs text-destructive">{(generate.error as Error).message}</p>
            )}
          </div>
        )}
        {summary && (
          <>
            {stale && (
              <div className="border-warning/30 bg-warning/10 mb-3 flex items-center gap-2 rounded-md border px-2 py-1.5">
                <p className="min-w-0 flex-1 text-[11px] leading-snug text-warning">
                  These sheets changed after this summary was written.
                </p>
                <Button
                  variant="outline"
                  size="sm"
                  className="border-warning/40 text-warning hover:bg-warning/20 h-6 shrink-0 px-1.5 text-[11px]"
                  onClick={() => setConfirming(true)}
                  disabled={generate.isPending || waiting}
                >
                  {generate.isPending || waiting ? "Working…" : "Regenerate"}
                </Button>
              </div>
            )}
            <p className="text-sm leading-relaxed">{summary.summary.overview}</p>

            {items.length > 0 && (
              <>
                <h4 className="mt-5 text-sm font-semibold">Key highlights</h4>
                <ul className="mt-2 space-y-0.5">
                  {shownItems.map((item, i) => (
                    <li key={i}>
                      <button
                        className="hover:bg-accent hover:text-accent-foreground flex w-full items-start gap-2 rounded-md px-2 py-1.5 text-left text-xs leading-relaxed transition"
                        onClick={() => void jumpToItem(item)}
                        title={`Jump to combined page ${item.page} and highlight the source`}
                      >
                        <CheckIcon className="text-muted-foreground mt-0.5 size-3.5 shrink-0" />
                        <span className="min-w-0 flex-1">{item.text}</span>
                        <span className="bg-muted text-muted-foreground mt-px shrink-0 rounded px-1.5 py-0.5 text-[11px] font-medium tabular-nums">
                          p.{item.page}
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
                {items.length > COLLAPSED_ITEMS && (
                  <Button
                    variant="ghost"
                    size="sm"
                    className="text-muted-foreground mt-1 w-full text-xs"
                    onClick={() => setExpanded((open) => !open)}
                  >
                    {expanded ? "Show less" : `Show ${items.length - COLLAPSED_ITEMS} more`}
                    <ChevronDownIcon className={expanded ? "rotate-180" : undefined} />
                  </Button>
                )}
              </>
            )}
          </>
        )}
      </div>

      {confirming && (
        <SummaryConfirm
          title={
            selectedPortionId
              ? `Summarize ${selectedPortion?.name ?? "this discipline"}?`
              : "Generate project summary?"
          }
          estimate={estimate.data}
          isLoading={estimate.isLoading}
          error={estimate.error}
          busy={generate.isPending}
          onCancel={() => setConfirming(false)}
          onConfirm={() => generate.mutate()}
        />
      )}
    </section>
  );
}
