import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import type { DocumentStatus } from "@cdip/shared";
import { api } from "../api";
import { uploadPdf } from "../upload";

const statusStyles: Record<DocumentStatus, string> = {
  uploaded: "bg-gray-100 text-gray-700",
  processing: "bg-amber-100 text-amber-800",
  completed: "bg-green-100 text-green-800",
  failed: "bg-red-100 text-red-800",
};

/** FR-9: per-document status, polled while anything is still in flight.
 * FR-4: "New revision" replaces a document; superseded revisions are listed
 * greyed-out for history but leave the combined set. */
export function DocumentsPanel({ projectId }: { projectId: string }) {
  const queryClient = useQueryClient();
  const fileInput = useRef<HTMLInputElement>(null);
  const [uploads, setUploads] = useState<Record<string, number | "error">>({});
  // When set, the next chosen file uploads as a new revision of this document.
  const [replaceTarget, setReplaceTarget] = useState<string | undefined>(undefined);

  const documents = useQuery({
    queryKey: ["documents", projectId],
    queryFn: () => api.listDocuments(projectId),
    refetchInterval: (query) =>
      query.state.data?.some((d) => d.status === "uploaded" || d.status === "processing")
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
    <div className="flex h-full flex-col">
      <div className="border-b border-gray-200 p-3">
        <button
          className="w-full rounded bg-blue-600 px-3 py-2 text-sm font-medium text-white"
          onClick={() => {
            setReplaceTarget(undefined);
            fileInput.current?.click();
          }}
        >
          Upload PDFs
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
            <div className="truncate text-gray-600">{name}</div>
            {progress === "error" ? (
              <div className="text-red-600">upload failed — select the file again to resume</div>
            ) : (
              <div className="mt-1 h-1.5 rounded bg-gray-200">
                <div
                  className="h-1.5 rounded bg-blue-500 transition-all"
                  style={{ width: `${Math.round(progress * 100)}%` }}
                />
              </div>
            )}
          </div>
        ))}
      </div>

      <ul className="flex-1 divide-y divide-gray-100 overflow-y-auto">
        {documents.data?.map((doc) => (
          <li key={doc.id} className={`p-3 text-sm ${doc.supersededAt ? "opacity-50" : ""}`}>
            <div className="truncate font-medium" title={doc.filename}>
              {doc.filename}
            </div>
            <div className="mt-1 flex flex-wrap items-center gap-2">
              <span className={`rounded px-1.5 py-0.5 text-xs ${statusStyles[doc.status]}`}>
                {doc.status}
              </span>
              {doc.revision > 1 && (
                <span className="rounded bg-purple-100 px-1.5 py-0.5 text-xs text-purple-800">
                  rev {doc.revision}
                </span>
              )}
              {doc.supersededAt ? (
                <span className="text-xs text-gray-500">superseded</span>
              ) : (
                <>
                  {doc.pages > 0 && (
                    <span className="text-xs text-gray-500">{doc.pages} pages</span>
                  )}
                  {doc.status === "completed" && (
                    <button
                      className="ml-auto text-xs text-blue-600 hover:underline"
                      title="Upload a newer revision of this drawing set (FR-4)"
                      onClick={() => {
                        setReplaceTarget(doc.id);
                        fileInput.current?.click();
                      }}
                    >
                      New revision
                    </button>
                  )}
                  {(doc.status === "failed" || doc.status === "processing") && (
                    <button
                      className="ml-auto text-xs text-blue-600 hover:underline"
                      title="Re-run processing — already-finished pages are skipped, so it resumes where it stopped"
                      onClick={() => {
                        void api
                          .reprocessDocument(projectId, doc.id)
                          .then(() =>
                            queryClient.invalidateQueries({ queryKey: ["documents", projectId] }),
                          )
                          .catch((err) => alert((err as Error).message));
                      }}
                    >
                      Retry
                    </button>
                  )}
                  {doc.status === "failed" && (
                    <button
                      className="text-xs text-red-600 hover:underline"
                      title="Delete this document everywhere (database, storage, search index)"
                      onClick={() => {
                        if (!confirm(`Delete "${doc.filename}"? This removes it everywhere.`)) return;
                        void api
                          .deleteDocument(projectId, doc.id)
                          .then(() => {
                            queryClient.invalidateQueries({ queryKey: ["documents", projectId] });
                            queryClient.invalidateQueries({ queryKey: ["manifest", projectId] });
                            queryClient.invalidateQueries({ queryKey: ["portions", projectId] });
                          })
                          .catch((err) => alert((err as Error).message));
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
          <li className="p-3 text-sm text-gray-500">No documents yet.</li>
        )}
      </ul>
    </div>
  );
}
