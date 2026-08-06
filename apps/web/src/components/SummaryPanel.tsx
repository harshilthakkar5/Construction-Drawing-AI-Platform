import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import type { SummaryItem } from "@cdip/shared";
import { api } from "../api";
import { useAppStore } from "../store";

/** How long the panel keeps showing "Summarizing…" before giving up waiting. */
const REBUILD_TIMEOUT_MS = 5 * 60 * 1000;

function Spinner() {
  return (
    <svg
      className="h-3 w-3 animate-spin"
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden
    >
      <circle
        className="opacity-25"
        cx="12"
        cy="12"
        r="10"
        stroke="currentColor"
        strokeWidth="4"
      />
      <path
        className="opacity-75"
        fill="currentColor"
        d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z"
      />
    </svg>
  );
}

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
  const generate = useMutation({
    mutationFn: () =>
      selectedPortionId
        ? api.summarizePortion(projectId, selectedPortionId)
        : api.generateProjectSummary(projectId),
    onSuccess: () => {
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

  return (
    <section className="border-b border-hairline">
      <h3 className="sticky top-0 z-10 bg-surface px-3 pb-1 pt-3 text-xs font-semibold uppercase tracking-wide text-ink-muted">
        {heading}
      </h3>
      <div className="px-3 pb-3">
        {!summary && (
          <div className="space-y-2">
            <p className="text-xs text-ink-muted">
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
              <button
                className="inline-flex items-center gap-1.5 rounded border border-hairline px-2 py-1 text-xs text-ink-soft hover:bg-page disabled:opacity-60"
                onClick={() => generate.mutate()}
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
              </button>
            )}
            {generate.isError && (
              <p className="text-xs text-red-600">{(generate.error as Error).message}</p>
            )}
          </div>
        )}
        {summary && (
          <>
            {stale && (
              <div className="mb-2 flex items-center gap-2 rounded border border-amber-200 bg-amber-50 px-2 py-1.5">
                <p className="min-w-0 flex-1 text-[11px] leading-snug text-amber-800">
                  These sheets changed after this summary was written.
                </p>
                <button
                  className="shrink-0 rounded border border-amber-300 px-1.5 py-0.5 text-[11px] font-medium text-amber-800 transition hover:bg-amber-100 disabled:opacity-60"
                  onClick={() => generate.mutate()}
                  disabled={generate.isPending || waiting}
                >
                  {generate.isPending || waiting ? "Working…" : "Regenerate"}
                </button>
              </div>
            )}
            <p className="text-sm leading-relaxed text-ink-soft">
              {summary.summary.overview}
            </p>
            <ul className="mt-3 space-y-1">
              {summary.summary.items.map((item, i) => (
                <li key={i}>
                  <button
                    className="flex w-full items-start gap-2 rounded-md px-2 py-1.5 text-left text-xs leading-relaxed text-ink-soft transition hover:bg-brand-50 hover:text-brand-700"
                    onClick={() => void jumpToItem(item)}
                    title={`Jump to combined page ${item.page} and highlight the source`}
                  >
                    <span className="min-w-0 flex-1">{item.text}</span>
                    <span className="mt-px shrink-0 rounded bg-page px-1.5 py-0.5 text-[11px] font-medium tabular-nums text-ink-muted">
                      p.{item.page}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </>
        )}
      </div>
    </section>
  );
}
