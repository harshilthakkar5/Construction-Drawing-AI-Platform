import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "@/api";
import { RegionSelector } from "@/components/RegionSelector";
import { Button } from "@/components/ui/button";

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
    <section className="border-b px-3 py-2.5">
      {!region.data && !region.isLoading && (
        <>
          <p className="text-xs text-muted-foreground">
            <span className="text-foreground font-medium">No title-block region yet.</span> Draw a box
            over one sheet&rsquo;s number and it is applied to every page to sort the drawings
            into disciplines.
          </p>
          <Button
            size="sm"
            className="mt-2"
            onClick={() => setEditing(true)}
            disabled={!hasProcessedDocument}
            title={
              hasProcessedDocument
                ? "Pick a page and drag a box over its sheet number"
                : "Upload a PDF and let it finish processing first"
            }
          >
            Define region
          </Button>
        </>
      )}

      {region.data && (
        <div className="space-y-1.5">
          <div className="flex items-center gap-2">
            <span className="text-xs font-medium">Title-block region</span>
            <span className="text-[11px] text-muted-foreground">v{region.data.version}</span>
            <Button
              variant="outline"
              size="sm"
              className="ml-auto h-6 px-2 text-[11px]"
              onClick={() => setEditing(true)}
            >
              Edit
            </Button>
            <Button
              variant="outline"
              size="sm"
              className="h-6 px-2 text-[11px]"
              onClick={() => rescrape.mutate()}
              disabled={rescrape.isPending || status === "running" || status === "pending"}
              title="Re-read the sheet numbers with the same box"
            >
              Re-scan
            </Button>
          </div>

          {(status === "running" || status === "pending") && (
            <p className="text-[11px] text-muted-foreground">
              Scanning sheets… {region.data.scrapedPages}/{region.data.totalPages || "?"}
            </p>
          )}
          {status === "completed" && (
            <p className="text-[11px] text-muted-foreground">
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
            <p className="text-[11px] text-destructive">
              Scan failed: {region.data.lastError ?? "unknown error"}
            </p>
          )}
        </div>
      )}

      {editing && <RegionSelector projectId={projectId} onClose={() => setEditing(false)} />}
    </section>
  );
}
