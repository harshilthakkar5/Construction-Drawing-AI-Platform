import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "../api";
import { RegionSelector } from "./RegionSelector";

/**
 * The gate on categorization: until the user draws a title-block box, pages
 * have no discipline and the project has no categories. This banner is where
 * that box is defined, edited, and re-run — and where scrape progress shows.
 */
export function RegionBanner({ projectId }: { projectId: string }) {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);

  const region = useQuery({
    queryKey: ["region", projectId],
    queryFn: () => api.getRegion(projectId),
    // Follow a running scrape; stop once it settles.
    refetchInterval: (query) =>
      query.state.data?.scrapeStatus === "running" ||
      query.state.data?.scrapeStatus === "pending"
        ? 2000
        : false,
  });

  const documents = useQuery({
    queryKey: ["documents", projectId],
    queryFn: () => api.listDocuments(projectId),
  });
  const hasProcessedDocument = documents.data?.some((d) => d.status === "completed") ?? false;

  const rescrape = useMutation({
    mutationFn: () => api.rescrapeRegion(projectId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["region", projectId] });
    },
  });

  // A scrape rewrites every page's discipline, so the category list has to be
  // refetched when one finishes — not on every render.
  const status = region.data?.scrapeStatus;
  useEffect(() => {
    if (status === "completed") {
      void queryClient.invalidateQueries({ queryKey: ["portions", projectId] });
    }
  }, [status, projectId, queryClient]);

  return (
    <section className="border-b border-hairline px-3 py-2.5">
      {!region.data && !region.isLoading && (
        <>
          <p className="text-xs text-ink-soft">
            <span className="font-medium text-ink">No title-block region yet.</span> Draw a box
            over one sheet&rsquo;s number and it is applied to every page to sort the drawings
            into disciplines.
          </p>
          <button
            className="mt-2 rounded-md bg-brand-600 px-2.5 py-1.5 text-xs font-medium text-white transition hover:bg-brand-700 disabled:opacity-50"
            onClick={() => setEditing(true)}
            disabled={!hasProcessedDocument}
            title={
              hasProcessedDocument
                ? "Pick a page and drag a box over its sheet number"
                : "Upload a PDF and let it finish processing first"
            }
          >
            Define region
          </button>
        </>
      )}

      {region.data && (
        <div className="space-y-1.5">
          <div className="flex items-center gap-2">
            <span className="text-xs font-medium text-ink">Title-block region</span>
            <span className="text-[11px] text-ink-muted">v{region.data.version}</span>
            <button
              className="ml-auto rounded border border-hairline px-2 py-0.5 text-[11px] text-ink-soft transition hover:bg-page"
              onClick={() => setEditing(true)}
            >
              Edit
            </button>
            <button
              className="rounded border border-hairline px-2 py-0.5 text-[11px] text-ink-soft transition hover:bg-page disabled:opacity-50"
              onClick={() => rescrape.mutate()}
              disabled={rescrape.isPending || status === "running" || status === "pending"}
              title="Re-read the sheet numbers with the same box"
            >
              Re-scan
            </button>
          </div>

          {(status === "running" || status === "pending") && (
            <p className="text-[11px] text-ink-muted">
              Scanning sheets… {region.data.scrapedPages}/{region.data.totalPages || "?"}
            </p>
          )}
          {status === "completed" && (
            <p className="text-[11px] text-ink-muted">
              {region.data.scrapedPages - region.data.notFoundPages} of{" "}
              {region.data.scrapedPages} sheets read
              {region.data.notFoundPages > 0 && (
                <>
                  {" — "}
                  <button className="underline" onClick={() => setEditing(true)}>
                    {region.data.notFoundPages} came back empty, adjust the box
                  </button>
                </>
              )}
            </p>
          )}
          {status === "failed" && (
            <p className="text-[11px] text-red-600">
              Scan failed: {region.data.lastError ?? "unknown error"}
            </p>
          )}
        </div>
      )}

      {editing && <RegionSelector projectId={projectId} onClose={() => setEditing(false)} />}
    </section>
  );
}
