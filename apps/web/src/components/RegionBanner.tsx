import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { ScanLineIcon, SquareDashedIcon } from "lucide-react";
import { api } from "@/api";
import { RegionSelector } from "@/components/RegionSelector";
import { Button } from "@/components/ui/button";

/**
 * The gate on categorization: until the user draws a title-block box, pages
 * have no discipline and the project has no categories. These are the project
 * header's quick actions — define/edit the box, re-run it, and see how the
 * scrape is going.
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
    <div className="flex min-w-0 shrink-0 flex-col items-end gap-1.5">
      <span className="text-muted-foreground text-xs font-medium">Quick actions</span>
      {!region.data && !region.isLoading && (
        <>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setEditing(true)}
            disabled={!hasProcessedDocument}
            title={
              hasProcessedDocument
                ? "Pick a page and drag a box over its sheet number"
                : "Upload a PDF and let it finish processing first"
            }
          >
            <SquareDashedIcon />
            Define region
          </Button>
          <p className="text-muted-foreground max-w-xs text-right text-[11px]">
            Draw a box over one sheet&rsquo;s number; it is applied to every page to sort the
            drawings into disciplines.
          </p>
        </>
      )}

      {region.data && (
        <>
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" onClick={() => setEditing(true)}>
              <SquareDashedIcon />
              Edit region
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => rescrape.mutate()}
              disabled={rescrape.isPending || status === "running" || status === "pending"}
              title="Re-read the sheet numbers with the same box"
            >
              <ScanLineIcon />
              Re-scan
            </Button>
          </div>

          <p className="text-muted-foreground text-right text-[11px]">
              Title-block region v{region.data.version}
              {(status === "running" || status === "pending") &&
                ` · scanning sheets… ${region.data.scrapedPages}/${region.data.totalPages || "?"}`}
              {status === "completed" && (
                <>
                  {" · "}
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
                </>
              )}
            </p>
          {status === "failed" && (
            <p className="text-destructive text-[11px]">
              Scan failed: {region.data.lastError ?? "unknown error"}
            </p>
          )}
        </>
      )}

      {editing && <RegionSelector projectId={projectId} onClose={() => setEditing(false)} />}
    </div>
  );
}
