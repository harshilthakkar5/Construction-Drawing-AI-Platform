import { useMutation, useQuery } from "@tanstack/react-query";
import { FileDownIcon, PencilRulerIcon, RotateCcwIcon } from "lucide-react";
import type { RfiPackageItemRef } from "@cdip/shared";
import { api } from "@/api";
import { Spinner } from "@/components/shared";
import { Button } from "@/components/ui/button";

/**
 * "Marked-up PDF": the RFI the way the team sends it — a cover form, then
 * the drawing sheets with red clouds round the problem, a callout with the
 * question and a leader line. The worker renders it from the original PDFs
 * (a few seconds a sheet); this asks, waits, and then offers the download.
 * The marks are real PDF annotations, so they can be edited in Bluebeam.
 */
export function MarkedUpPdfButton({
  projectId,
  items,
  reviewRunId,
  fullScanId,
  label = "Marked-up PDF",
  size = "sm",
  variant = "outline",
  title,
}: {
  projectId: string;
  items?: RfiPackageItemRef[];
  reviewRunId?: string;
  fullScanId?: string;
  label?: string;
  size?: "sm" | "default";
  variant?: "outline" | "ghost" | "default";
  title?: string;
}) {
  const create = useMutation({ mutationFn: () => api.createRfiPackage(projectId, { items, reviewRunId, fullScanId }) });
  const packageId = create.data?.id;
  const pkg = useQuery({
    queryKey: ["rfi-package", projectId, packageId],
    queryFn: () => api.getRfiPackage(projectId, packageId!),
    enabled: !!packageId,
    refetchInterval: (q) => {
      const s = q.state.data?.status ?? "queued";
      return s === "queued" || s === "running" ? 1500 : false;
    },
  });
  const status = pkg.data?.status ?? (create.isPending ? "queued" : create.data?.status);
  const busy = create.isPending || status === "queued" || status === "running";
  const error = create.error ?? pkg.error ?? (status === "failed" ? new Error(pkg.data?.error ?? "rendering failed") : null);

  if (status === "ready" && pkg.data?.downloadUrl) {
    return (
      <span className="inline-flex flex-wrap items-center gap-2">
        <Button size={size} variant="default" asChild>
          <a href={pkg.data.downloadUrl} target="_blank" rel="noreferrer">
            <FileDownIcon />
            Open marked-up PDF{pkg.data.pages ? ` (${pkg.data.pages} pages)` : ""}
          </a>
        </Button>
        {pkg.data.notes.length > 0 && (
          <span className="text-muted-foreground text-xs" title={pkg.data.notes.join("\n")}>
            {pkg.data.notes.length} note{pkg.data.notes.length === 1 ? "" : "s"}
          </span>
        )}
      </span>
    );
  }
  return (
    <span className="inline-flex flex-wrap items-center gap-2">
      <Button
        size={size}
        variant={variant}
        disabled={busy}
        onClick={() => create.mutate()}
        title={title ?? "Cover form plus the drawing sheets with clouds and callouts, as editable PDF markup"}
      >
        {busy ? <Spinner /> : error ? <RotateCcwIcon /> : <PencilRulerIcon />}
        {busy ? "Marking up sheets…" : error ? "Try again" : label}
      </Button>
      {error && <span className="text-destructive text-xs">{(error as Error).message}</span>}
    </span>
  );
}
