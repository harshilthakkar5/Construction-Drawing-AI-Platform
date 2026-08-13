import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import type { RegionBox } from "@cdip/shared";
import { api } from "@/api";
import { Button } from "@/components/ui/button";

/**
 * Draw the title-block box once, apply it to every page of the project.
 *
 * The box is reported as ratios (0-1) of the displayed page, so the same
 * rectangle re-applies server-side to every page regardless of its point size
 * or rotation (workers/src/region.py maps it back through the page's rotation
 * matrix).
 *
 * It draws on the page PNG the worker already rendered rather than loading the
 * PDF through pdf.js: the PNG comes from the same rotation-aware page box, so
 * the ratios are identical, and it is already in object storage — the editor
 * opens instantly even on a 1 GB drawing set.
 */

/** Ignore accidental clicks and micro-drags. */
const MIN_DRAG_PX = 8;

interface Box {
  x: number;
  y: number;
  w: number;
  h: number;
}

export function RegionSelector({
  projectId,
  onClose,
}: {
  projectId: string;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const wrapRef = useRef<HTMLDivElement>(null);
  const imageRef = useRef<HTMLImageElement>(null);

  const manifest = useQuery({
    queryKey: ["manifest", projectId],
    queryFn: () => api.manifest(projectId),
  });
  const existing = useQuery({
    queryKey: ["region", projectId],
    queryFn: () => api.getRegion(projectId),
  });

  const pages = manifest.data?.filter((entry) => entry.hasImage) ?? [];
  const [page, setPage] = useState<number | null>(null);
  useEffect(() => {
    const first = pages[0];
    if (page !== null || !first) return;
    // Reopen on the page the box was drawn on, when we know it.
    const sample = existing.data?.samplePageNumber;
    const match = sample
      ? pages.find(
          (p) => p.pageNumber === sample && p.documentId === existing.data?.sampleDocumentId,
        )
      : undefined;
    setPage(match?.combinedPageNumber ?? first.combinedPageNumber);
  }, [page, pages, existing.data]);

  const [drawing, setDrawing] = useState(false);
  const [start, setStart] = useState<{ x: number; y: number } | null>(null);
  const [box, setBox] = useState<Box | null>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });

  // Show the stored box when the editor opens, scaled to the rendered image.
  useEffect(() => {
    const region = existing.data;
    if (!region || size.width === 0 || box !== null) return;
    setBox({
      x: region.relX * size.width,
      y: region.relY * size.height,
      w: region.relW * size.width,
      h: region.relH * size.height,
    });
    // Only seeds the initial box; redrawing replaces it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [existing.data, size]);

  function measure() {
    const image = imageRef.current;
    if (image && image.clientWidth > 0) {
      setSize({ width: image.clientWidth, height: image.clientHeight });
    }
  }

  useEffect(() => {
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, []);

  function coords(event: React.MouseEvent) {
    const rect = imageRef.current?.getBoundingClientRect();
    if (!rect) return { x: 0, y: 0 };
    return {
      x: Math.min(Math.max(event.clientX - rect.left, 0), rect.width),
      y: Math.min(Math.max(event.clientY - rect.top, 0), rect.height),
    };
  }

  function onMouseDown(event: React.MouseEvent) {
    const position = coords(event);
    setDrawing(true);
    setStart(position);
    setBox({ x: position.x, y: position.y, w: 0, h: 0 });
  }

  function onMouseMove(event: React.MouseEvent) {
    if (!drawing || !start) return;
    const position = coords(event);
    setBox({
      x: Math.min(start.x, position.x),
      y: Math.min(start.y, position.y),
      w: Math.abs(position.x - start.x),
      h: Math.abs(position.y - start.y),
    });
  }

  function onMouseUp() {
    if (!drawing) return;
    setDrawing(false);
    if (box && (box.w < MIN_DRAG_PX || box.h < MIN_DRAG_PX)) setBox(null);
  }

  const ratios: RegionBox | null =
    box && size.width > 0 && box.w >= MIN_DRAG_PX && box.h >= MIN_DRAG_PX
      ? {
          relX: Number((box.x / size.width).toFixed(4)),
          relY: Number((box.y / size.height).toFixed(4)),
          relW: Number((box.w / size.width).toFixed(4)),
          relH: Number((box.h / size.height).toFixed(4)),
        }
      : null;

  // --- preview (dry run on a handful of sample pages) ---

  const [previewId, setPreviewId] = useState<string | null>(null);
  const startPreview = useMutation({
    mutationFn: () => api.previewRegion(projectId, ratios as RegionBox),
    onSuccess: (result) => setPreviewId(result.previewId),
  });
  const preview = useQuery({
    queryKey: ["region-preview", projectId, previewId],
    queryFn: () => api.regionPreview(projectId, previewId as string),
    enabled: previewId !== null,
    refetchInterval: (query) => (query.state.data ? false : 1500),
  });

  const save = useMutation({
    mutationFn: () => {
      const entry = pages.find((p) => p.combinedPageNumber === page);
      return api.saveRegion(projectId, {
        ...(ratios as RegionBox),
        sampleDocumentId: entry?.documentId,
        samplePageNumber: entry?.pageNumber,
      });
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["region", projectId] });
      void queryClient.invalidateQueries({ queryKey: ["portions", projectId] });
      onClose();
    },
  });

  const current = pages.find((p) => p.combinedPageNumber === page);

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-black/60 p-4">
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-lg bg-card shadow-xl">
        <header className="flex shrink-0 items-center gap-3 border-b px-4 py-3">
          <div className="min-w-0">
            <h2 className="font-semibold">Title-block region</h2>
            <p className="truncate text-xs text-muted-foreground">
              Drag a box over the sheet number. It is applied to every page of every PDF in
              this project to read each sheet&rsquo;s discipline.
            </p>
          </div>
          <div className="ml-auto flex shrink-0 items-center gap-2">
            <Button variant="outline" size="sm" onClick={onClose}>
              Cancel
            </Button>
            <Button
              size="sm"
              disabled={!ratios || save.isPending}
              onClick={() => save.mutate()}
              title={ratios ? "Save and re-scrape the project" : "Draw a box first"}
            >
              {save.isPending ? "Saving…" : "Save & scrape project"}
            </Button>
          </div>
        </header>

        <div className="flex shrink-0 items-center gap-2 border-b px-4 py-2 text-xs text-muted-foreground">
          <Button
            variant="outline"
            size="sm"
            className="h-7 px-2 text-xs"
            disabled={!current || pages.indexOf(current) <= 0}
            onClick={() => {
              const index = current ? pages.indexOf(current) : 0;
              const previous = pages[index - 1];
              if (previous) {
                setPage(previous.combinedPageNumber);
                setBox(null);
              }
            }}
          >
            ← Prev
          </Button>
          <span className="tabular-nums">
            Page {page ?? "…"} of {pages.length || "…"}
            {current && <span className="ml-2 text-muted-foreground">{current.filename}</span>}
          </span>
          <Button
            variant="outline"
            size="sm"
            className="h-7 px-2 text-xs"
            disabled={!current || pages.indexOf(current) >= pages.length - 1}
            onClick={() => {
              const index = current ? pages.indexOf(current) : 0;
              const next = pages[index + 1];
              if (next) {
                setPage(next.combinedPageNumber);
                setBox(null);
              }
            }}
          >
            Next →
          </Button>

          <Button
            variant="outline"
            size="sm"
            className="ml-auto h-7 px-2 text-xs"
            disabled={!ratios || startPreview.isPending}
            onClick={() => {
              setPreviewId(null);
              startPreview.mutate();
            }}
            title="Scrape this box on a few sample pages to check it"
          >
            {startPreview.isPending ? "Checking…" : "Preview on 5 sheets"}
          </Button>
          {ratios && (
            <span className="tabular-nums text-muted-foreground">
              {(ratios.relW * 100).toFixed(1)}% × {(ratios.relH * 100).toFixed(1)}%
            </span>
          )}
        </div>

        <div className="flex min-h-0 flex-1">
          <div className="min-w-0 flex-1 overflow-auto bg-muted p-4">
            {page !== null && (
              <div
                ref={wrapRef}
                className="relative inline-block cursor-crosshair select-none"
                onMouseDown={onMouseDown}
                onMouseMove={onMouseMove}
                onMouseUp={onMouseUp}
                onMouseLeave={() => drawing && setDrawing(false)}
              >
                <img
                  ref={imageRef}
                  src={api.pageImageUrl(projectId, page)}
                  alt={`Combined page ${page}`}
                  className="max-w-full"
                  draggable={false}
                  onLoad={measure}
                />
                {box && (
                  <div
                    className="pointer-events-none absolute border-2 border-primary bg-primary/20"
                    style={{ left: box.x, top: box.y, width: box.w, height: box.h }}
                  />
                )}
              </div>
            )}
            {pages.length === 0 && !manifest.isLoading && (
              <p className="text-sm text-muted-foreground">
                No rendered pages yet — upload a PDF and let it finish processing first.
              </p>
            )}
          </div>

          {previewId && (
            <aside className="w-80 shrink-0 overflow-y-auto border-l p-3">
              <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                What this box scrapes
              </h3>
              {!preview.data && (
                <p className="text-xs text-muted-foreground">Scraping sample pages…</p>
              )}
              {preview.data && (
                <>
                  <p className="mb-2 text-xs text-muted-foreground">
                    {preview.data.foundCount} of{" "}
                    {preview.data.foundCount + preview.data.notFoundCount} sample pages
                    returned text.
                    {preview.data.notFoundCount > 0 &&
                      " Widen the box if a sheet number is missing."}
                  </p>
                  <ul className="space-y-1">
                    {preview.data.rows.map((row) => (
                      <li
                        key={row.combinedPageNumber}
                        className="rounded-md border px-2 py-1.5 text-xs"
                      >
                        <div className="flex items-center gap-2">
                          <span className="tabular-nums text-muted-foreground">
                            p.{row.combinedPageNumber}
                          </span>
                          <span className="ml-auto rounded bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground">
                            {row.method}
                          </span>
                        </div>
                        <p
                          className={
                            row.text ? "mt-1 break-words" : "mt-1 text-muted-foreground"
                          }
                        >
                          {row.text || "nothing in the box"}
                        </p>
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </aside>
          )}
        </div>

        {save.isError && (
          <p className="shrink-0 border-t px-4 py-2 text-xs text-destructive">
            {(save.error as Error).message}
          </p>
        )}
      </div>
    </div>
  );
}
