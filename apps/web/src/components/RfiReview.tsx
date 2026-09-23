import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CheckIcon,
  ChevronDownIcon,
  RotateCcwIcon,
  ScanSearchIcon,
  SparklesIcon,
  TriangleAlertIcon,
  XIcon,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import {
  RFI_CHECK_LABELS,
  type RfiCandidateDto,
  type RfiConfidence,
  type RfiEvidenceDto,
  type RfiScanDto,
} from "@cdip/shared";
import { api } from "@/api";
import { Notice, Spinner } from "@/components/shared";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useAppStore } from "@/store";

/**
 * "Find RFIs in drawings", and the review list it fills.
 *
 * The scan runs in the worker: deterministic checks decide what is missing
 * (a sheet referenced and not issued, a mark with no schedule row, a note left
 * TBD) and the AI only words each finding as a question. What comes back here
 * is a FINDING — no number, not issued. Accept makes it a numbered, open RFI
 * pinned where it was found; Dismiss remembers it so the next scan does not
 * propose it again.
 *
 * Every finding shows the drawing's own words and a link to the spot, because
 * the reviewer's job is to check it — and "Accept all high-confidence" exists
 * for the reviewer who has.
 */

const CONFIDENCE: Record<
  RfiConfidence,
  { label: string; variant: "success" | "warning" | "secondary"; hint: string }
> = {
  high: {
    label: "High",
    variant: "success",
    hint: "The check found this directly and every guard agreed.",
  },
  medium: {
    label: "Medium",
    variant: "warning",
    hint: "Real on the evidence, but something the check cannot see could explain it.",
  },
  low: {
    label: "Low",
    variant: "secondary",
    hint: "Often genuine, often boilerplate or a sheet simply not uploaded — worth a look.",
  },
};

const checkLabel = (checkType: string) =>
  (RFI_CHECK_LABELS as Record<string, string>)[checkType] ?? checkType;

function evidenceLabel(e: RfiEvidenceDto): string {
  const page = e.combinedPageNumber ?? e.pageNumber;
  return e.sheetNumber ? `${e.sheetNumber} · page ${page}` : `page ${page}`;
}

function scanSummary(scan: RfiScanDto): string {
  const when = scan.finishedAt ? new Date(scan.finishedAt).toLocaleString() : "";
  const found = `${scan.findings} ${scan.findings === 1 ? "finding" : "findings"}`;
  return `Last scan ${when} — ${found}`;
}

export function RfiReview({ projectId }: { projectId: string }) {
  const queryClient = useQueryClient();
  const [showDismissed, setShowDismissed] = useState(false);
  const [showNotes, setShowNotes] = useState(false);

  const scan = useQuery({
    queryKey: ["rfi-scan", projectId],
    queryFn: () => api.latestRfiScan(projectId),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "queued" || status === "running" ? 2000 : false;
    },
  });
  const scanning = scan.data?.status === "queued" || scan.data?.status === "running";

  const pending = useQuery({
    queryKey: ["rfi-candidates", projectId, "pending"],
    queryFn: () => api.listRfiCandidates(projectId, "pending"),
  });
  const dismissed = useQuery({
    queryKey: ["rfi-candidates", projectId, "dismissed"],
    queryFn: () => api.listRfiCandidates(projectId, "dismissed"),
    enabled: showDismissed,
  });

  const refreshAll = () => {
    void queryClient.invalidateQueries({ queryKey: ["rfi-candidates", projectId] });
    void queryClient.invalidateQueries({ queryKey: ["rfis", projectId] });
  };

  // When a scan finishes, the list it filled is stale: fetch it once.
  const lastStatus = useRef(scan.data?.status);
  useEffect(() => {
    const now = scan.data?.status;
    if (lastStatus.current !== now && (now === "completed" || now === "failed")) refreshAll();
    lastStatus.current = now;
  }, [scan.data?.status]);

  const start = useMutation({
    mutationFn: () => api.startRfiScan(projectId),
    onSuccess: (started) => queryClient.setQueryData(["rfi-scan", projectId], started),
  });
  const accept = useMutation({
    mutationFn: (id: string) => api.acceptRfiCandidate(projectId, id),
    onSuccess: refreshAll,
  });
  const dismiss = useMutation({
    mutationFn: (id: string) => api.dismissRfiCandidate(projectId, id),
    onSuccess: refreshAll,
  });
  const restore = useMutation({
    mutationFn: (id: string) => api.restoreRfiCandidate(projectId, id),
    onSuccess: refreshAll,
  });
  const acceptAll = useMutation({
    mutationFn: (min: RfiConfidence) => api.acceptAllRfiCandidates(projectId, min),
    onSuccess: refreshAll,
  });

  const candidates = pending.data?.candidates ?? [];
  const highCount = candidates.filter((c) => c.confidence === "high").length;
  const dismissedCount = pending.data?.counts.dismissed ?? 0;
  const busy = accept.isPending || dismiss.isPending || acceptAll.isPending;
  const error = start.error ?? accept.error ?? dismiss.error ?? acceptAll.error ?? restore.error;

  return (
    <section className="mb-4">
      <div className="bg-muted/40 rounded-lg border p-3">
        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" onClick={() => start.mutate()} disabled={scanning || start.isPending}>
            {scanning ? <Spinner /> : <ScanSearchIcon />}
            {scanning ? "Scanning drawings…" : scan.data ? "Scan again" : "Find RFIs in drawings"}
          </Button>
          {scan.data?.status === "completed" && (
            <span className="text-muted-foreground text-xs">{scanSummary(scan.data)}</span>
          )}
        </div>
        {!scan.data && !scan.isLoading && (
          <p className="text-muted-foreground mt-2 text-xs leading-relaxed">
            Checks every sheet for references to sheets that are not in the set, marks with no
            row in their schedule, and notes left open (TBD, verify in field). Each finding is
            written up as an RFI question for you to accept or dismiss.
          </p>
        )}
        {scan.data?.status === "failed" && (
          <Notice tone="error">The last scan failed: {scan.data.error ?? "unknown error"}</Notice>
        )}
        {scan.data && scan.data.notes.length > 0 && (
          <div className="mt-2">
            <button
              type="button"
              className="text-muted-foreground flex items-center gap-1 text-xs hover:underline"
              onClick={() => setShowNotes((v) => !v)}
            >
              <TriangleAlertIcon className="size-3" />
              {scan.data.notes.length} note{scan.data.notes.length === 1 ? "" : "s"} about this scan
              <ChevronDownIcon className={cn("size-3 transition-transform", showNotes && "rotate-180")} />
            </button>
            {showNotes && (
              <ul className="text-muted-foreground mt-1 list-disc pl-5 text-xs leading-relaxed">
                {scan.data.notes.map((note) => (
                  <li key={note}>{note}</li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>

      {candidates.length > 0 && (
        <div className="mt-3">
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <h4 className="text-sm font-semibold">
              Needs your review <span className="text-muted-foreground">({candidates.length})</span>
            </h4>
            {highCount > 0 && (
              <Button
                size="sm"
                variant="outline"
                className="ml-auto"
                disabled={busy}
                onClick={() => acceptAll.mutate("high")}
              >
                <CheckIcon />
                Accept all high ({highCount})
              </Button>
            )}
          </div>
          <ul className="flex flex-col gap-2">
            {candidates.map((c) => (
              <CandidateCard
                key={c.id}
                candidate={c}
                busy={busy}
                onAccept={() => accept.mutate(c.id)}
                onDismiss={() => dismiss.mutate(c.id)}
              />
            ))}
          </ul>
        </div>
      )}

      {scan.data?.status === "completed" && candidates.length === 0 && (
        <p className="text-muted-foreground mt-3 text-center text-xs">
          Nothing waiting for review.
          {scan.data.findings === 0 && " The scan found no gaps these checks can see."}
        </p>
      )}

      {dismissedCount > 0 && (
        <div className="mt-2">
          <button
            type="button"
            className="text-muted-foreground text-xs hover:underline"
            onClick={() => setShowDismissed((v) => !v)}
          >
            {showDismissed ? "Hide" : "Show"} dismissed ({dismissedCount})
          </button>
          {showDismissed && (
            <ul className="mt-1 flex flex-col gap-1">
              {dismissed.data?.candidates.map((c) => (
                <li key={c.id} className="flex items-center gap-2 text-xs">
                  <span className="text-muted-foreground truncate line-through">{c.subject}</span>
                  <Button
                    size="sm"
                    variant="ghost"
                    className="ml-auto h-6 px-2"
                    disabled={restore.isPending}
                    onClick={() => restore.mutate(c.id)}
                  >
                    <RotateCcwIcon className="size-3" />
                    Restore
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {!!error && (
        <div className="mt-2">
          <Notice tone="error">{(error as Error).message}</Notice>
        </div>
      )}
    </section>
  );
}

function CandidateCard({
  candidate,
  busy,
  onAccept,
  onDismiss,
}: {
  candidate: RfiCandidateDto;
  busy: boolean;
  onAccept: () => void;
  onDismiss: () => void;
}) {
  const requestJump = useAppStore((s) => s.requestJump);
  const confidence = CONFIDENCE[candidate.confidence];
  const found = candidate.evidence.filter((e) => e.role !== "context");
  const context = candidate.evidence.filter((e) => e.role === "context");

  return (
    <li className="bg-card rounded-md border p-3">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant={confidence.variant} title={confidence.hint}>
          {confidence.label}
        </Badge>
        <span className="text-muted-foreground text-xs">{checkLabel(candidate.checkType)}</span>
        {candidate.questionSource === "model" && (
          <span
            className="text-muted-foreground ml-auto flex items-center gap-1 text-[11px]"
            title="Worded by AI from the finding. Checked: it names no sheet, mark or number that the drawing's own text does not contain."
          >
            <SparklesIcon className="size-3" />
            AI-worded
          </span>
        )}
      </div>
      <p className="mt-1.5 text-sm font-medium">{candidate.subject}</p>
      <p className="text-muted-foreground mt-1 text-sm leading-relaxed">{candidate.question}</p>

      <ul className="mt-2 flex flex-col gap-1">
        {found.map((e, i) => (
          <EvidenceLine key={`f${i}`} evidence={e} onOpen={requestJump} />
        ))}
        {context.map((e, i) => (
          <EvidenceLine key={`c${i}`} evidence={e} onOpen={requestJump} context />
        ))}
      </ul>

      <div className="mt-2 flex gap-2">
        <Button size="sm" onClick={onAccept} disabled={busy}>
          <CheckIcon />
          Accept as RFI
        </Button>
        <Button size="sm" variant="ghost" onClick={onDismiss} disabled={busy}>
          <XIcon />
          Dismiss
        </Button>
      </div>
    </li>
  );
}

/** The drawing's own words at the spot, and a link that opens it — the thing
 * a reviewer actually checks the finding against. */
function EvidenceLine({
  evidence,
  onOpen,
  context = false,
}: {
  evidence: RfiEvidenceDto;
  onOpen: (page: number | null, bbox?: RfiEvidenceDto["bbox"]) => void;
  context?: boolean;
}) {
  const page = evidence.combinedPageNumber;
  return (
    <li className="text-xs">
      <button
        type="button"
        className="text-primary hover:underline disabled:no-underline disabled:opacity-60"
        disabled={page === null}
        onClick={() => onOpen(page, evidence.bbox ?? undefined)}
      >
        {context ? "Checked against " : ""}
        {evidenceLabel(evidence)}
      </button>
      <span className="text-muted-foreground"> — “{evidence.quote}”</span>
    </li>
  );
}
