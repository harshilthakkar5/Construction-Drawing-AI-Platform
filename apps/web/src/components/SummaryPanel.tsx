import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { SummaryItem } from "@cdip/shared";
import { api } from "../api";
import { useAppStore } from "../store";

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

  const summaries = useQuery({
    queryKey: ["summaries", projectId],
    queryFn: () => api.listSummaries(projectId),
    refetchInterval: (query) => {
      if (query.state.data?.some((s) => s.level === "project")) return false; // done
      return processing ? 5000 : 30_000; // slow poll: the job may still be running
    },
  });

  // Only asked for when there is nothing to show: turns "no summary yet" into
  // the actual reason (job never ran, SUMMARIES_ENABLED=false, no API key…).
  const hasAnySummary = (summaries.data?.length ?? 0) > 0;
  const status = useQuery({
    queryKey: ["summary-status", projectId],
    queryFn: () => api.summaryStatus(projectId),
    enabled: !summaries.isLoading && !hasAnySummary && !processing,
  });

  const queryClient = useQueryClient();
  const rebuild = useMutation({
    mutationFn: () => api.rebuildSummaries(projectId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["summaries", projectId] });
      void queryClient.invalidateQueries({ queryKey: ["summary-status", projectId] });
    },
  });

  const summary = selectedPortionId
    ? summaries.data?.find((s) => s.level === "portion" && s.portionId === selectedPortionId)
    : summaries.data?.find((s) => s.level === "project");
  const heading = selectedPortionId ? "Portion summary" : "Project summary";
  // Page summaries exist but the rollup for this level doesn't yet — makes the
  // "still working" case distinguishable from "nothing at all".
  const pagesSummarized = summaries.data?.some((s) => s.level === "page") ?? false;

  return (
    <div className="border-b border-gray-200 p-3">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-500">{heading}</h3>
      {!summary && (
        <div className="mt-2 space-y-2">
          <p className="text-xs text-gray-400">
            {summaries.isLoading
              ? "Loading…"
              : pagesSummarized
                ? `Page summaries are ready; the ${selectedPortionId ? "portion" : "project"} summary is still being written.`
                : processing
                  ? "No summary yet — it is generated once processing finishes."
                  : (status.data?.hint ??
                    "No summary yet — it is generated after processing (requires ANTHROPIC_API_KEY on the worker).")}
          </p>
          {!summaries.isLoading && !processing && (
            <button
              className="rounded border border-gray-300 px-2 py-1 text-xs text-gray-600 hover:bg-gray-50 disabled:opacity-50"
              onClick={() => rebuild.mutate()}
              disabled={rebuild.isPending}
            >
              {rebuild.isPending
                ? "Queuing…"
                : rebuild.isSuccess
                  ? "Re-run queued — watch the worker log"
                  : "Re-run summarization"}
            </button>
          )}
        </div>
      )}
      {summary && (
        <>
          <p className="mt-2 text-sm text-gray-700">{summary.summary.overview}</p>
          <ul className="mt-2 space-y-1">
            {summary.summary.items.map((item, i) => (
              <li key={i}>
                <button
                  className="w-full rounded px-1.5 py-1 text-left text-xs text-gray-600 hover:bg-blue-50 hover:text-blue-800"
                  onClick={() => void jumpToItem(item)}
                  title={`Jump to combined page ${item.page} and highlight the source`}
                >
                  {item.text}
                  <span className="ml-1 text-blue-500">p.{item.page}</span>
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
