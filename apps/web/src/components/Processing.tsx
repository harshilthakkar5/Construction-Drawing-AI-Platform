import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { processingLabel, type ProcessingStateDto } from "@cdip/shared";
import { PlayIcon } from "lucide-react";
import { api } from "@/api";
import { JobProgress } from "@/components/shared";
import { Button } from "@/components/ui/button";

/**
 * Discipline-first processing. An upload is only viewable until its discipline
 * is processed (OCR + search index); these pieces show where each discipline
 * stands and start the full pass.
 */

export function useProcessing(projectId: string) {
  return useQuery({
    queryKey: ["processing", projectId],
    queryFn: () => api.processing(projectId),
    refetchInterval: (query) => {
      const state = query.state.data;
      const working = state?.running || state?.disciplines.some((d) => d.chosen && d.processed < d.pages);
      return working ? 3000 : false;
    },
  });
}

export function useStartProcessing(projectId: string) {
  const queryClient = useQueryClient();
  const key = ["processing", projectId];
  return useMutation({
    mutationFn: (body: { disciplines?: string[]; all?: boolean }) => api.startProcessing(projectId, body),
    // Show the discipline as queued the moment it is clicked: the worker takes a
    // few seconds to pick the job up, and the button must not come back meanwhile.
    onMutate: async (body) => {
      await queryClient.cancelQueries({ queryKey: key });
      const before = queryClient.getQueryData<ProcessingStateDto>(key);
      if (before) {
        queryClient.setQueryData<ProcessingStateDto>(key, {
          ...before,
          disciplines: before.disciplines.map((d) =>
            body.all || body.disciplines?.includes(d.discipline) ? { ...d, chosen: true } : d,
          ),
        });
      }
      return { before };
    },
    onError: (_err, _body, context) => {
      if (context?.before) queryClient.setQueryData(key, context.before);
    },
    onSuccess: (result) => {
      queryClient.setQueryData(key, result.state);
      for (const name of ["documents", "manifest", "portions"]) {
        void queryClient.invalidateQueries({ queryKey: [name, projectId] });
      }
    },
  });
}

/** Where one discipline stands: done, waiting for a click, or chosen and not finished. */
export function processingStage(state: ProcessingStateDto | undefined, discipline: string) {
  const row = state?.disciplines.find((d) => d.discipline === discipline);
  if (!row || row.processed >= row.pages) return { stage: "done" as const, row };
  return { stage: row.chosen ? ("processing" as const) : ("waiting" as const), row };
}

/** "Process now (14 pages)", only while the discipline waits for a click. */
export function ProcessNowButton({ projectId, discipline }: { projectId: string; discipline: string }) {
  const state = useProcessing(projectId);
  const start = useStartProcessing(projectId);
  const { stage, row } = processingStage(state.data, discipline);
  if (stage !== "waiting" || !row) return null;
  const waiting = row.pages - row.processed;
  return (
    <Button
      size="sm"
      className="h-6 shrink-0 gap-1 px-2 text-[11px]"
      onClick={() => start.mutate({ disciplines: [discipline] })}
      title={`Read and index the ${processingLabel(discipline)} sheets so chat, summaries and RFIs can use them`}
    >
      <PlayIcon className="size-3" />
      Process now · {waiting} page{waiting === 1 ? "" : "s"}
    </Button>
  );
}

/** The progress of a discipline that was chosen and is not finished yet. */
export function ProcessingProgress({ projectId, discipline }: { projectId: string; discipline: string }) {
  const state = useProcessing(projectId);
  const start = useStartProcessing(projectId);
  const { stage, row } = processingStage(state.data, discipline);
  if (stage !== "processing" || !row) return null;
  const queued = row.processed === 0 && !state.data?.running;
  return (
    <div className="mt-2">
      <JobProgress
        text={queued ? "Queued, waiting for the worker…" : `Processing ${row.processed} of ${row.pages} pages`}
        percent={Math.round((row.processed / row.pages) * 100)}
        label={`${processingLabel(discipline)} processing`}
      />
      {!state.data?.running && !start.isPending && (
        <button
          className="text-muted-foreground hover:text-foreground mt-1 text-[11px] underline-offset-2 hover:underline"
          onClick={() => start.mutate({ disciplines: [discipline] })}
        >
          Not starting? Queue it again
        </button>
      )}
    </div>
  );
}

/** Unprocessed disciplines, for a line of explanation. */
export function unprocessed(state: ProcessingStateDto | undefined) {
  return (state?.disciplines ?? []).filter((d) => d.processed < d.pages && !d.chosen);
}

export function ProcessAllRemaining({ projectId }: { projectId: string }) {
  const state = useProcessing(projectId);
  const start = useStartProcessing(projectId);
  const waiting = unprocessed(state.data);
  if (!waiting.length) return null;
  const pages = waiting.reduce((sum, d) => sum + d.pages - d.processed, 0);
  return (
    <div className="flex items-center gap-2 px-4 py-2">
      <p className="text-muted-foreground min-w-0 flex-1 text-xs">
        {waiting.length} discipline{waiting.length === 1 ? " is" : "s are"} not processed yet, so chat,
        summaries and RFIs cannot use {waiting.length === 1 ? "it" : "them"}.
      </p>
      <Button
        variant="outline"
        size="sm"
        className="h-7 shrink-0 text-xs"
        disabled={start.isPending}
        onClick={() => start.mutate({ all: true })}
      >
        Process all remaining ({pages} pages)
      </Button>
    </div>
  );
}

/** For the chat: which disciplines it cannot answer from yet. */
export function NotProcessedNotice({ projectId }: { projectId: string }) {
  const state = useProcessing(projectId);
  const waiting = unprocessed(state.data);
  if (!waiting.length) return null;
  return (
    <p className="text-muted-foreground border-b px-3 py-1.5 text-[11px]">
      Not processed yet, so chat cannot answer from them: {waiting.map((d) => processingLabel(d.discipline)).join(", ")}.
      Use "Process now" in Summary &amp; categories.
    </p>
  );
}

/** What processing a discipline has cost, e.g. "$0.0031" — small sums keep their
 * digits, since one discipline's search indexing is often under a cent. */
export function processingCost(row: { costUsd: number; tokens: number; processed: number } | undefined) {
  if (!row || row.processed === 0) return null;
  const usd = row.costUsd >= 0.01 ? `$${row.costUsd.toFixed(2)}` : row.costUsd > 0 ? `$${row.costUsd.toFixed(4)}` : "$0.00";
  return {
    usd,
    title: `${row.tokens.toLocaleString()} AI tokens for search indexing and the vision pass. OCR runs on this machine and costs nothing.`,
  };
}
