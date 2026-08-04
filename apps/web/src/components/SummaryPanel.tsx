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

  const summaries = useQuery({
    queryKey: ["summaries", projectId],
    queryFn: () => api.listSummaries(projectId),
    refetchInterval: (query) => {
      if (query.state.data?.some((s) => s.level === "project")) return false; // done
      if (rebuilding) return 3000; // watching a run we just kicked off
      return processing ? 5000 : 30_000; // slow poll: the job may still be running
    },
  });

  // Only asked for when there is nothing to show: turns "no summary yet" into
  // the actual reason (job never ran, SUMMARIES_ENABLED=false, no API key…).
  const hasAnySummary = (summaries.data?.length ?? 0) > 0;
  const status = useQuery({
    queryKey: ["summary-status", projectId],
    queryFn: () => api.summaryStatus(projectId),
    enabled:
      !summaries.isLoading && !hasAnySummary && !processing && !rebuilding,
  });

  const queryClient = useQueryClient();
  const rebuild = useMutation({
    mutationFn: () => api.rebuildSummaries(projectId),
    onSuccess: () => {
      setRebuilding(true);
      void queryClient.invalidateQueries({
        queryKey: ["summaries", projectId],
      });
      void queryClient.invalidateQueries({
        queryKey: ["summary-status", projectId],
      });
    },
  });

  // Stop waiting once the run produced a project summary, or after the timeout
  // (a failed job never writes anything — don't spin forever).
  const hasProjectSummary =
    summaries.data?.some((s) => s.level === "project") ?? false;
  useEffect(() => {
    if (!rebuilding) return;
    if (hasProjectSummary) {
      setRebuilding(false);
      return;
    }
    const timer = setTimeout(() => setRebuilding(false), REBUILD_TIMEOUT_MS);
    return () => clearTimeout(timer);
  }, [rebuilding, hasProjectSummary]);

  const summary = selectedPortionId
    ? summaries.data?.find(
        (s) => s.level === "portion" && s.portionId === selectedPortionId,
      )
    : summaries.data?.find((s) => s.level === "project");
  const heading = selectedPortionId ? "Portion summary" : "Project summary";
  // Page summaries exist but the rollup for this level doesn't yet — makes the
  // "still working" case distinguishable from "nothing at all".
  const pagesSummarized =
    summaries.data?.some((s) => s.level === "page") ?? false;

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
                : rebuilding
                  ? "Summarizing… page summaries first, then the rollups. This can take a while on a large set."
                  : pagesSummarized
                    ? `Page summaries are ready; the ${selectedPortionId ? "portion" : "project"} summary is still being written.`
                    : processing
                      ? "No summary yet — it is generated once processing finishes."
                      : (status.data?.hint ??
                        "No summary yet — it is generated after processing (requires ANTHROPIC_API_KEY on the worker).")}
            </p>
            {!summaries.isLoading && !processing && (
              <button
                className="inline-flex items-center gap-1.5 rounded border border-hairline px-2 py-1 text-xs text-ink-soft hover:bg-page disabled:opacity-60"
                onClick={() => rebuild.mutate()}
                disabled={rebuild.isPending || rebuilding}
              >
                {(rebuild.isPending || rebuilding) && <Spinner />}
                {rebuild.isPending
                  ? "Queuing…"
                  : rebuilding
                    ? "Summarizing…"
                    : "Re-run summarization"}
              </button>
            )}
            {rebuild.isError && (
              <p className="text-xs text-red-600">
                Could not queue the job — is the API running?
              </p>
            )}
          </div>
        )}
        {summary && (
          <>
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
