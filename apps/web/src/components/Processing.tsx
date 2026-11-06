import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { processingLabel, type ProcessingStateDto } from "@cdip/shared";
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
  return useMutation({
    mutationFn: (body: { disciplines?: string[]; all?: boolean }) => api.startProcessing(projectId, body),
    onSuccess: (result) => {
      queryClient.setQueryData(["processing", projectId], result.state);
      for (const key of ["documents", "manifest", "portions"]) {
        void queryClient.invalidateQueries({ queryKey: [key, projectId] });
      }
    },
  });
}

/** Every page of this discipline has had the full pass. */
export function isProcessed(state: ProcessingStateDto | undefined, discipline: string) {
  const row = state?.disciplines.find((d) => d.discipline === discipline);
  return !row || row.processed >= row.pages;
}

/** "Process now (40 pages)", or its progress while it runs; nothing once done. */
export function ProcessNow({ projectId, discipline }: { projectId: string; discipline: string }) {
  const state = useProcessing(projectId);
  const start = useStartProcessing(projectId);
  const row = state.data?.disciplines.find((d) => d.discipline === discipline);
  if (!row || row.processed >= row.pages) return null;
  const waiting = row.pages - row.processed;

  if (row.chosen && state.data?.running) {
    return (
      <div className="mt-2 w-full">
        <JobProgress
          text={`Processing ${row.processed} of ${row.pages} pages`}
          percent={Math.round((row.processed / row.pages) * 100)}
          label={`${processingLabel(discipline)} processing`}
        />
      </div>
    );
  }
  return (
    <Button
      size="sm"
      className="h-6 shrink-0 px-2 text-[11px]"
      disabled={start.isPending}
      onClick={() => start.mutate({ disciplines: [discipline] })}
      title={`Read and index the ${processingLabel(discipline)} sheets so chat, summaries and RFIs can use them`}
    >
      {start.isPending ? "Starting…" : `Process now (${waiting} page${waiting === 1 ? "" : "s"})`}
    </Button>
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
