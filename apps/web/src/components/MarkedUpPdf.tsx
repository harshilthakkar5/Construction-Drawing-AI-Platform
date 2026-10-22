import { useMutation, useQuery } from "@tanstack/react-query";
import { DownloadIcon, FileSearchIcon, PencilRulerIcon, RotateCcwIcon } from "lucide-react";
import { useState } from "react";
import type { RfiPackageDto, RfiPackageItemRef } from "@cdip/shared";
import { api } from "@/api";
import { Spinner } from "@/components/shared";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";

/**
 * "Marked-up PDF": the RFI the way the team sends it — a cover form, then
 * the drawing sheets with red clouds round the problem, a callout with the
 * question and a leader line. The worker renders it from the original PDFs
 * (a few seconds a sheet); this asks, waits, and then opens a preview with
 * the download in it — a package is read before it is sent.
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
  const [previewing, setPreviewing] = useState(false);
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
        <Button size={size} variant="default" onClick={() => setPreviewing(true)}>
          <FileSearchIcon />
          Open marked-up PDF{pkg.data.pages ? ` (${pkg.data.pages} pages)` : ""}
        </Button>
        {previewing && <MarkedUpPdfPreview pkg={pkg.data} onClose={() => setPreviewing(false)} />}
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

/**
 * The package in the browser's own PDF viewer, with Download beside Close.
 * A Radix Dialog, not a hand-made overlay: it opens from inside other dialogs
 * (a Create RFI's findings, an RFI's details), and Radix stacks a nested
 * dialog as a child layer. The old overlay was portalled outside the parent
 * dialog, so a click, a scroll or the iframe taking focus counted as "outside"
 * and closed the parent, taking the preview with it.
 */
function MarkedUpPdfPreview({ pkg, onClose }: { pkg: RfiPackageDto; onClose: () => void }) {
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent
        showCloseButton={false}
        className="flex h-[calc(100dvh-2rem)] max-w-[calc(100vw-2rem)] flex-col gap-0 overflow-hidden p-0 sm:max-w-[calc(100vw-2rem)]"
      >
        <header className="flex shrink-0 items-center gap-3 border-b px-4 py-3">
          <div className="min-w-0">
            <DialogTitle>Marked-up RFI</DialogTitle>
            <DialogDescription className="truncate text-xs">
              {pkg.pages ? `${pkg.pages} pages · ` : ""}cover form, then the drawing sheets with clouds and callouts.
              The marks are PDF annotations you can edit in Bluebeam after downloading.
            </DialogDescription>
          </div>
          <div className="ml-auto flex shrink-0 items-center gap-2">
            <Button variant="outline" size="sm" onClick={onClose}>
              Close
            </Button>
            {pkg.downloadUrl && (
              <Button size="sm" asChild>
                <a href={pkg.downloadUrl} download>
                  <DownloadIcon />
                  Download PDF
                </a>
              </Button>
            )}
          </div>
        </header>
        {pkg.notes.length > 0 && (
          <div className="shrink-0 border-b px-4 py-2 text-xs text-muted-foreground">
            {pkg.notes.map((n) => (
              <p key={n}>{n}</p>
            ))}
          </div>
        )}
        {pkg.previewUrl ? (
          <iframe title="Marked-up RFI" src={pkg.previewUrl} className="min-h-0 w-full flex-1 bg-muted" />
        ) : (
          <p className="p-6 text-sm text-muted-foreground">The preview is not available; download the PDF instead.</p>
        )}
      </DialogContent>
    </Dialog>
  );
}
