import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import type { DocumentDto, DocumentStatus } from "@cdip/shared";
import { FileTextIcon, UploadIcon } from "lucide-react";
import { api } from "@/api";
import { ConfirmDialog, Spinner } from "@/components/shared";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { STATUS_COLORS } from "@/charts/palette";
import { uploadPdf } from "@/upload";

/** Dot colours come from the same tokens as the dashboard's status donut. */
const statusDot: Record<DocumentStatus, string> = {
  uploaded: STATUS_COLORS.uploaded!,
  processing: STATUS_COLORS.processing!,
  completed: STATUS_COLORS.completed!,
  failed: STATUS_COLORS.failed!,
};

/** FR-9: per-document status, polled while anything is still in flight.
 * FR-4: "New revision" replaces a document; superseded revisions are listed
 * greyed-out for history but leave the combined set. */
export function DocumentsPanel({ projectId }: { projectId: string }) {
  const queryClient = useQueryClient();
  const fileInput = useRef<HTMLInputElement>(null);
  const [uploads, setUploads] = useState<Record<string, number | "error">>({});
  // When set, the next chosen file uploads as a new revision of this document.
  const [replaceTarget, setReplaceTarget] = useState<string | undefined>(
    undefined,
  );
  const [deleting, setDeleting] = useState<DocumentDto | null>(null);

  function refreshProject() {
    for (const key of ["documents", "manifest", "portions"]) {
      void queryClient.invalidateQueries({ queryKey: [key, projectId] });
    }
  }

  const remove = useMutation({
    mutationFn: (documentId: string) => api.deleteDocument(projectId, documentId),
    onSuccess: () => {
      setDeleting(null);
      refreshProject();
    },
  });

  // Retrying is only offered on a failed document (see below), and the button
  // reports its own progress rather than firing silently.
  const retry = useMutation({
    mutationFn: (documentId: string) => api.reprocessDocument(projectId, documentId),
    onSuccess: refreshProject,
  });

  const documents = useQuery({
    queryKey: ["documents", projectId],
    queryFn: () => api.listDocuments(projectId),
    refetchInterval: (query) =>
      query.state.data?.some(
        (d) => d.status === "uploaded" || d.status === "processing",
      )
        ? 2000
        : false,
  });

  async function onFiles(files: FileList | null, replacesDocumentId?: string) {
    if (!files) return;
    for (const file of Array.from(files)) {
      setUploads((u) => ({ ...u, [file.name]: 0 }));
      try {
        await uploadPdf(
          projectId,
          file,
          (fraction) => setUploads((u) => ({ ...u, [file.name]: fraction })),
          replacesDocumentId,
        );
        setUploads((u) => {
          const { [file.name]: _done, ...rest } = u;
          return rest;
        });
      } catch (err) {
        console.error(err);
        setUploads((u) => ({ ...u, [file.name]: "error" }));
      }
      queryClient.invalidateQueries({ queryKey: ["documents", projectId] });
      queryClient.invalidateQueries({ queryKey: ["manifest", projectId] });
      queryClient.invalidateQueries({ queryKey: ["portions", projectId] });
    }
  }

  return (
    <section>
      <div className="flex items-center justify-between gap-3 px-4">
        <h3 className="text-sm font-semibold">Documents</h3>
        <Button
          variant="outline"
          size="sm"
          className="text-xs"
          onClick={() => {
            setReplaceTarget(undefined);
            fileInput.current?.click();
          }}
        >
          <UploadIcon />
          Upload PDFs
        </Button>
      </div>
      <div className="px-4 pt-1 pb-3">
        <input
          ref={fileInput}
          type="file"
          accept="application/pdf"
          multiple
          hidden
          onChange={(e) => {
            void onFiles(e.target.files, replaceTarget);
            e.target.value = "";
            setReplaceTarget(undefined);
          }}
        />
        {Object.entries(uploads).map(([name, progress]) => (
          <div key={name} className="mt-2 text-xs">
            <div className="truncate text-muted-foreground">{name}</div>
            {progress === "error" ? (
              <div className="text-destructive">
                upload failed — select the file again to resume
              </div>
            ) : (
              <Progress className="mt-1 h-1.5" value={Math.round(progress * 100)} />
            )}
          </div>
        ))}
      </div>

      <ul className="divide-y border-t">
        {documents.data?.map((doc) => (
          <li
            key={doc.id}
            className={`px-4 py-3 text-sm ${doc.supersededAt ? "opacity-50" : ""}`}
          >
            <div className="flex items-center gap-2">
              <FileTextIcon className="text-muted-foreground size-4 shrink-0" aria-hidden />
              <span className="truncate font-medium" title={doc.filename}>
                {doc.filename}
              </span>
            </div>
            <div className="mt-1 flex flex-wrap items-center gap-2">
              <Badge variant="outline" className="gap-1.5 font-normal">
                <span
                  className="size-1.5 rounded-full"
                  style={{ background: statusDot[doc.status] }}
                  aria-hidden
                />
                {doc.status.charAt(0).toUpperCase() + doc.status.slice(1)}
              </Badge>
              {doc.revision > 1 && <Badge variant="secondary">rev {doc.revision}</Badge>}
              {doc.supersededAt ? (
                <span className="text-xs text-muted-foreground">superseded</span>
              ) : (
                <>
                  {doc.pages > 0 && (
                    <span className="text-xs text-muted-foreground">
                      {doc.pages} pages
                    </span>
                  )}
                  {doc.status === "completed" && (
                    <Button
                      variant="link"
                      className="ml-auto h-auto p-0 text-xs"
                      title="Upload a newer revision of this drawing set (FR-4)"
                      onClick={() => {
                        setReplaceTarget(doc.id);
                        fileInput.current?.click();
                      }}
                    >
                      New revision
                    </Button>
                  )}
                  {/* While a document is uploading or processing there is
                      nothing to retry — the worker is mid-run and a second job
                      would only race it. Show progress instead; Retry appears
                      only once the pipeline has actually given up. */}
                  {(doc.status === "uploaded" || doc.status === "processing") && (
                    <span className="ml-auto inline-flex items-center gap-1.5 text-xs text-muted-foreground">
                      <Spinner />
                      {doc.status === "uploaded" ? "Queued…" : "Processing…"}
                    </span>
                  )}
                  {doc.status === "failed" && (
                    <Button
                      variant="link"
                      className="ml-auto h-auto gap-1.5 p-0 text-xs"
                      title="Re-run processing — already-finished pages are skipped, so it resumes where it stopped"
                      disabled={retry.isPending}
                      onClick={() => retry.mutate(doc.id)}
                    >
                      {retry.isPending && <Spinner />}
                      {retry.isPending ? "Retrying…" : "Retry"}
                    </Button>
                  )}
                  {doc.status === "failed" && (
                    <Button
                      variant="link"
                      className="text-destructive h-auto p-0 text-xs"
                      title="Delete this document everywhere (database, storage, search index)"
                      onClick={() => {
                        remove.reset();
                        setDeleting(doc);
                      }}
                    >
                      Delete
                    </Button>
                  )}
                </>
              )}
            </div>
          </li>
        ))}
        {documents.data?.length === 0 && (
          <li className="text-muted-foreground px-4 py-3 text-sm">No documents yet.</li>
        )}
      </ul>

      {retry.isError && (
        <p className="text-destructive px-4 py-2 text-xs">
          {(retry.error as Error).message}
        </p>
      )}

      {deleting && (
        <ConfirmDialog
          title={`Delete "${deleting.filename}"?`}
          tone="danger"
          confirmLabel="Delete document"
          busyLabel="Deleting…"
          busy={remove.isPending}
          error={remove.isError ? remove.error : undefined}
          onCancel={() => setDeleting(null)}
          onConfirm={() => remove.mutate(deleting.id)}
          message={
            <>
              <p>
                This removes the PDF from storage along with its pages, chunks and
                search index. It cannot be undone.
              </p>
              <p className="mt-2 text-xs text-muted-foreground">
                Other documents in this project are renumbered afterwards.
              </p>
            </>
          }
        />
      )}
    </section>
  );
}
