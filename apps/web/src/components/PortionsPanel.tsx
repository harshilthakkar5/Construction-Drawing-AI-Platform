import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { PortionDto, PortionSummaryStatus } from "@cdip/shared";
import { api } from "../api";
import { useAppStore } from "../store";
import { SummaryConfirm } from "./SummaryConfirm";

/**
 * The discipline categories (FR-15/16). Clicking one jumps the viewer to its
 * start page and switches the left panel to its summary.
 *
 * Summaries are NOT generated automatically — each category carries its own
 * button, and pressing it is the only thing that spends tokens on that
 * discipline. `summaryStatus` drives the chip and the polling.
 */

const CHIP: Record<PortionSummaryStatus, { label: string; className: string }> = {
  none: { label: "No summary", className: "bg-page text-ink-muted" },
  queued: { label: "Queued", className: "bg-amber-50 text-amber-700" },
  running: { label: "Summarizing…", className: "bg-amber-50 text-amber-700" },
  ready: { label: "Summary ready", className: "bg-emerald-50 text-emerald-700" },
  stale: { label: "Out of date", className: "bg-amber-50 text-amber-700" },
  failed: { label: "Failed", className: "bg-red-50 text-red-700" },
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
  const requestJump = useAppStore((s) => s.requestJump);

  const documents = useQuery({
    queryKey: ["documents", projectId],
    queryFn: () => api.listDocuments(projectId),
  });
  const processing = documents.data?.some(
    (d) => d.status === "uploaded" || d.status === "processing",
  );

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

  function open(portion: PortionDto) {
    if (portion.id === selectedPortionId) {
      selectPortion(null); // toggle back to the project summary
      return;
    }
    selectPortion(portion.id);
    requestJump(portion.startPage);
  }

  return (
    <section className="border-b border-hairline">
      <h3 className="sticky top-0 z-10 bg-surface px-3 pb-1 pt-3 text-xs font-semibold uppercase tracking-wide text-ink-muted">
        Categories
      </h3>
      <ul className="p-2">
        {portions.data?.map((portion) => {
          const chip = CHIP[portion.summaryStatus];
          const busy =
            portion.summaryStatus === "queued" || portion.summaryStatus === "running";
          return (
            <li key={portion.id} className="mb-1">
              <div
                className={`rounded-md border px-2 py-1.5 transition ${
                  portion.id === selectedPortionId
                    ? "border-brand-200 bg-brand-50"
                    : "border-transparent hover:bg-page"
                }`}
              >
                <button
                  className="flex w-full items-center gap-2 text-left"
                  onClick={() => open(portion)}
                  title={`Jump to combined page ${portion.startPage}`}
                >
                  <span
                    className={`min-w-0 flex-1 truncate text-sm ${
                      portion.id === selectedPortionId
                        ? "font-semibold text-brand-700"
                        : "text-ink-soft"
                    }`}
                  >
                    {portion.name}
                  </span>
                  <span className="shrink-0 text-xs tabular-nums text-ink-muted">
                    pp. {portion.startPage}–{portion.endPage}
                  </span>
                </button>

                <div className="mt-1 flex items-center gap-2">
                  <span className="text-[11px] tabular-nums text-ink-muted">
                    {portion.pageCount} sheet{portion.pageCount === 1 ? "" : "s"}
                    {portion.sheetNumberSample && ` · ${portion.sheetNumberSample}`}
                  </span>
                  <span
                    className={`rounded px-1.5 py-0.5 text-[11px] font-medium ${chip.className}`}
                  >
                    {chip.label}
                  </span>
                  <button
                    className="ml-auto shrink-0 rounded border border-hairline px-2 py-0.5 text-[11px] text-ink-soft transition hover:bg-surface disabled:opacity-50"
                    disabled={busy || summarize.isPending}
                    onClick={() => setPending(portion.id)}
                    title={
                      CAN_START.includes(portion.summaryStatus)
                        ? `Summarize the ${portion.name} sheets`
                        : "Regenerate this summary"
                    }
                  >
                    {busy ? "Working…" : buttonLabel(portion.summaryStatus)}
                  </button>
                </div>

                {portion.summaryStatus === "failed" && portion.summaryError && (
                  <p className="mt-1 text-[11px] text-red-600">{portion.summaryError}</p>
                )}
                {portion.summaryStatus === "stale" && (
                  <p className="mt-1 text-[11px] text-ink-muted">
                    These sheets changed since the summary was written.
                  </p>
                )}
              </div>
            </li>
          );
        })}

        {portions.data?.length === 0 && (
          <li className="px-2 py-1 text-sm text-ink-muted">
            {processing
              ? "Processing…"
              : "No categories yet — define the title-block region above."}
          </li>
        )}
      </ul>

      {anyReady && (
        <div className="border-t border-hairline px-3 py-2">
          <button
            className="w-full rounded-md border border-hairline px-2 py-1.5 text-xs text-ink-soft transition hover:bg-page disabled:opacity-50"
            onClick={() => setPending("project")}
            disabled={projectSummary.isPending}
            title="Combine the discipline summaries into one project summary"
          >
            {projectSummary.isPending ? "Queuing…" : "Generate project summary"}
          </button>
        </div>
      )}

      {summarize.isError && (
        <p className="px-3 pb-2 text-[11px] text-red-600">
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
