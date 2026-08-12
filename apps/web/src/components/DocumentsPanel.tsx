import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import type { DocumentDto, DocumentStatus } from "@cdip/shared";
import { api } from "../api";
import { ConfirmDialog, Spinner } from "./ui";
import { uploadPdf } from "../upload";

function PdfIcon() {
  return (
    <svg
      width="15"
      height="15"
      viewBox="0 0 24 24"
      fill="none"
      stroke="#d03b3b"
      strokeWidth="1.7"
      strokeLinejoin="round"
      className="shrink-0"
      aria-hidden
    >
      <path d="M7 3h7l5 5v13H7z" />
      <path d="M14 3v5h5" />
      <path d="M9.5 17v-3.5h1.2a1.1 1.1 0 0 1 0 2.2H9.5M14 17v-3.5h1.6" strokeLinecap="round" />
    </svg>
  );
}

/** Dot colors match the dashboard's document-status donut (charts/palette.ts). */
const statusDot: Record<DocumentStatus, string> = {
  uploaded: "#2a78d6",
  processing: "#eda100",
  completed: "#0ca30c",
  failed: "#d03b3b",
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
      <h3 className="sticky top-0 z-10 bg-surface px-3 pb-1 pt-3 text-xs font-semibold uppercase tracking-wide text-ink-muted">
        Documents
      </h3>
      <div className="px-3 pb-3 pt-1">
        <button
          className="flex w-full items-center justify-center gap-2 rounded-lg bg-brand-700 px-3 py-2.5 text-sm font-semibold text-white transition hover:bg-brand-600"
          onClick={() => {
            setReplaceTarget(undefined);
            fileInput.current?.click();
          }}
        >
          <span aria-hidden>↑</span> Upload PDFs
        </button>
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
            <div className="truncate text-ink-soft">{name}</div>
            {progress === "error" ? (
              <div className="text-red-600">
                upload failed — select the file again to resume
              </div>
            ) : (
              <div className="mt-1 h-1.5 rounded bg-page">
                <div
                  className="h-1.5 rounded bg-brand-500 transition-all"
                  style={{ width: `${Math.round(progress * 100)}%` }}
                />
              </div>
            )}
          </div>
        ))}
      </div>

      <ul className="divide-y divide-hairline border-t border-hairline">
        {documents.data?.map((doc) => (
          <li
            key={doc.id}
            className={`p-3 text-sm ${doc.supersededAt ? "opacity-50" : ""}`}
          >
            <div className="flex items-center gap-2">
              <PdfIcon />
              <span className="truncate font-medium text-ink" title={doc.filename}>
                {doc.filename}
              </span>
            </div>
            <div className="mt-1 flex flex-wrap items-center gap-2">
              <span className="inline-flex items-center gap-1.5 rounded-full bg-page px-2 py-0.5 text-xs font-medium text-ink-soft">
                <span
                  className="h-1.5 w-1.5 rounded-full"
                  style={{ background: statusDot[doc.status] }}
                  aria-hidden
                />
                {doc.status}
              </span>
              {doc.revision > 1 && (
                <span className="rounded-full bg-brand-50 px-2 py-0.5 text-xs font-medium text-brand-700">
                  rev {doc.revision}
                </span>
              )}
              {doc.supersededAt ? (
                <span className="text-xs text-ink-muted">superseded</span>
              ) : (
                <>
                  {doc.pages > 0 && (
                    <span className="text-xs text-ink-muted">
                      {doc.pages} pages
                    </span>
                  )}
                  {doc.status === "completed" && (
                    <button
                      className="ml-auto text-xs text-brand-700 hover:underline"
                      title="Upload a newer revision of this drawing set (FR-4)"
                      onClick={() => {
                        setReplaceTarget(doc.id);
                        fileInput.current?.click();
                      }}
                    >
                      New revision
                    </button>
                  )}
                  {/* While a document is uploading or processing there is
                      nothing to retry — the worker is mid-run and a second job
                      would only race it. Show progress instead; Retry appears
                      only once the pipeline has actually given up. */}
                  {(doc.status === "uploaded" || doc.status === "processing") && (
                    <span className="ml-auto inline-flex items-center gap-1.5 text-xs text-ink-muted">
                      <Spinner className="text-brand-600" />
                      {doc.status === "uploaded" ? "Queued…" : "Processing…"}
                    </span>
                  )}
                  {doc.status === "failed" && (
                    <button
                      className="ml-auto inline-flex items-center gap-1.5 text-xs text-brand-700 hover:underline disabled:opacity-60"
                      title="Re-run processing — already-finished pages are skipped, so it resumes where it stopped"
                      disabled={retry.isPending}
                      onClick={() => retry.mutate(doc.id)}
                    >
                      {retry.isPending && <Spinner />}
                      {retry.isPending ? "Retrying…" : "Retry"}
                    </button>
                  )}
                  {doc.status === "failed" && (
                    <button
                      className="text-xs text-red-600 hover:underline"
                      title="Delete this document everywhere (database, storage, search index)"
                      onClick={() => {
                        remove.reset();
                        setDeleting(doc);
                      }}
                    >
                      Delete
                    </button>
                  )}
                </>
              )}
            </div>
          </li>
        ))}
        {documents.data?.length === 0 && (
          <li className="p-3 text-sm text-ink-muted">No documents yet.</li>
        )}
      </ul>

      {retry.isError && (
        <p className="px-3 py-2 text-xs text-red-600">
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
              <p className="mt-2 text-xs text-ink-muted">
                Other documents in this project are renumbered afterwards.
              </p>
            </>
          }
        />
      )}
    </section>
  );
}
