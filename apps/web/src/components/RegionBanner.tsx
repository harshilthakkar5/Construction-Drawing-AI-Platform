import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { ScanLineIcon, SquareDashedIcon } from "lucide-react";
import { api } from "@/api";
import { RegionSelector } from "@/components/RegionSelector";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

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
    <Card className="shrink-0 gap-3 py-4">
      <CardHeader>
        <CardTitle className="text-sm">Quick actions</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        {!region.data && !region.isLoading && (
          <>
            <p className="text-muted-foreground text-xs">
              <span className="text-foreground font-medium">No title-block region yet.</span> Draw
              a box over one sheet&rsquo;s number and it is applied to every page to sort the
              drawings into disciplines.
            </p>
            <Button
              variant="outline"
              className="w-full"
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
          </>
        )}

        {region.data && (
          <>
            <div className="grid grid-cols-2 gap-2">
              <Button variant="outline" onClick={() => setEditing(true)}>
                <SquareDashedIcon />
                Edit region
              </Button>
              <Button
                variant="outline"
                onClick={() => rescrape.mutate()}
                disabled={rescrape.isPending || status === "running" || status === "pending"}
                title="Re-read the sheet numbers with the same box"
              >
                <ScanLineIcon />
                Re-scan
              </Button>
            </div>

            <p className="text-muted-foreground text-[11px]">
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
      </CardContent>

      {editing && <RegionSelector projectId={projectId} onClose={() => setEditing(false)} />}
    </Card>
  );
}
