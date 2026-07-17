import { useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import type { ManifestEntryDto } from "@cdip/shared";
import { api } from "../api";
import { useAppStore, type Highlight } from "../store";

pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.min.mjs",
  import.meta.url,
).toString();

const PAGE_WIDTH = 850;

/**
 * FR-6/FR-17/FR-20: renders the VIRTUAL combined set. The manifest maps each
 * combined page number to (document, page); source PDFs are never merged.
 * Pages lazy-load via a shared IntersectionObserver; a source document's PDF
 * is only fetched once one of its pages approaches the viewport. Thumbnails
 * come from the worker-generated JPGs.
 */
export function CombinedViewer({ projectId }: { projectId: string }) {
  const jumpToPage = useAppStore((s) => s.jumpToPage);
  const highlight = useAppStore((s) => s.highlight);
  const requestJump = useAppStore((s) => s.requestJump);
  const scrollRef = useRef<HTMLDivElement>(null);
  const observerRef = useRef<IntersectionObserver | null>(null);
  const [nearViewport, setNearViewport] = useState<Set<number>>(new Set());
  const [openedDocs, setOpenedDocs] = useState<Set<string>>(new Set());
  const [jumpInput, setJumpInput] = useState("");

  const manifest = useQuery({
    queryKey: ["manifest", projectId],
    queryFn: () => api.manifest(projectId),
    refetchInterval: (query) =>
      !query.state.data?.length || query.state.data.some((e) => !e.hasImage) ? 3000 : false,
  });
  const entries = useMemo(() => manifest.data ?? [], [manifest.data]);

  // Contiguous runs of the same document, in combined order.
  const groups = useMemo(() => {
    const out: { documentId: string; filename: string; entries: ManifestEntryDto[] }[] = [];
    for (const entry of entries) {
      const last = out[out.length - 1];
      if (last && last.documentId === entry.documentId) last.entries.push(entry);
      else out.push({ documentId: entry.documentId, filename: entry.filename, entries: [entry] });
    }
    return out;
  }, [entries]);

  useEffect(() => {
    const observer = new IntersectionObserver(
      (observed) => {
        setNearViewport((prev) => {
          const next = new Set(prev);
          for (const o of observed) {
            const combined = Number((o.target as HTMLElement).dataset.combined);
            if (o.isIntersecting) next.add(combined);
            else next.delete(combined);
          }
          return next;
        });
      },
      { root: scrollRef.current, rootMargin: "1200px 0px" },
    );
    observerRef.current = observer;
    return () => observer.disconnect();
  }, []);

  const observe = useCallback((el: HTMLElement | null) => {
    if (el) observerRef.current?.observe(el);
  }, []);

  // Once any page of a document nears the viewport, keep its PDF open.
  useEffect(() => {
    const needed = new Set<string>();
    for (const g of groups) {
      if (g.entries.some((e) => nearViewport.has(e.combinedPageNumber))) needed.add(g.documentId);
    }
    setOpenedDocs((prev) => {
      const merged = new Set(prev);
      let changed = false;
      for (const id of needed) if (!merged.has(id)) (merged.add(id), (changed = true));
      return changed ? merged : prev;
    });
  }, [groups, nearViewport]);

  // FR-16/FR-18 programmatic jump. Slots above the target change height as
  // thumbnails and PDF canvases lazy-load, so re-assert the scroll position a
  // few times until the layout settles.
  useEffect(() => {
    if (jumpToPage == null) return;
    const scroll = () =>
      document
        .getElementById(`combined-page-${jumpToPage}`)
        ?.scrollIntoView({ block: "start", behavior: "auto" });
    scroll();
    const timers = [300, 800, 1500].map((ms) => setTimeout(scroll, ms));
    const done = setTimeout(() => requestJump(null), 1600);
    return () => {
      timers.forEach(clearTimeout);
      clearTimeout(done);
    };
  }, [jumpToPage, requestJump]);

  const total = entries.length;

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-3 border-b border-gray-200 px-3 py-2 text-sm">
        <span className="font-medium">Combined set</span>
        <span className="text-gray-500">{total} pages</span>
        <form
          className="ml-auto flex items-center gap-1"
          onSubmit={(e) => {
            e.preventDefault();
            const n = Number(jumpInput);
            if (Number.isInteger(n) && n >= 1 && n <= total) requestJump(n);
          }}
        >
          <label className="text-gray-500">Go to page</label>
          <input
            className="w-20 rounded border border-gray-300 px-2 py-1"
            value={jumpInput}
            onChange={(e) => setJumpInput(e.target.value)}
            placeholder={total ? `1–${total}` : "–"}
          />
        </form>
      </div>

      <div className="flex min-h-0 flex-1">
        {/* Thumbnail rail (FR-20) */}
        <div className="w-28 shrink-0 overflow-y-auto border-r border-gray-200 p-2">
          {entries.map((e) => (
            <button
              key={e.combinedPageNumber}
              className="mb-2 block w-full rounded border border-gray-200 bg-white p-1 text-left hover:border-blue-400"
              onClick={() => requestJump(e.combinedPageNumber)}
              title={`${e.filename} — page ${e.pageNumber}`}
            >
              {e.hasImage ? (
                <img
                  src={api.pageThumbUrl(projectId, e.combinedPageNumber)}
                  alt={`Page ${e.combinedPageNumber}`}
                  loading="lazy"
                  className="w-full"
                />
              ) : (
                <div className="flex h-16 items-center justify-center bg-gray-100 text-xs text-gray-400">
                  …
                </div>
              )}
              <div className="mt-0.5 text-center text-xs text-gray-500">{e.combinedPageNumber}</div>
            </button>
          ))}
        </div>

        {/* Main pages */}
        <div ref={scrollRef} className="flex-1 overflow-y-auto bg-gray-100 p-4">
          {manifest.isLoading && <p className="text-sm text-gray-500">Loading manifest…</p>}
          {!manifest.isLoading && total === 0 && (
            <p className="text-sm text-gray-500">
              No pages yet — upload a PDF and wait for processing.
            </p>
          )}
          {groups.map((group) => {
            const slots = group.entries.map((entry) => (
              <div
                key={entry.combinedPageNumber}
                id={`combined-page-${entry.combinedPageNumber}`}
                data-combined={entry.combinedPageNumber}
                ref={observe}
                className="mx-auto mb-4 bg-white shadow"
                style={{ width: PAGE_WIDTH, minHeight: PAGE_WIDTH * 0.6 }}
              >
                <div className="relative">
                  {openedDocs.has(group.documentId) &&
                  nearViewport.has(entry.combinedPageNumber) ? (
                    <Page
                      pageNumber={entry.pageNumber}
                      width={PAGE_WIDTH}
                      renderTextLayer={false}
                      renderAnnotationLayer={false}
                      loading={<SlotPlaceholder entry={entry} projectId={projectId} />}
                    />
                  ) : (
                    <SlotPlaceholder entry={entry} projectId={projectId} />
                  )}
                  {highlight?.combinedPageNumber === entry.combinedPageNumber && (
                    <HighlightOverlay highlight={highlight} entry={entry} />
                  )}
                </div>
                <div className="border-t border-gray-100 px-2 py-1 text-xs text-gray-400">
                  {entry.filename} · page {entry.pageNumber} · combined {entry.combinedPageNumber}
                </div>
              </div>
            ));

            return openedDocs.has(group.documentId) ? (
              <Document
                key={`${group.documentId}-${group.entries[0]?.combinedPageNumber}`}
                file={api.documentFileUrl(projectId, group.documentId)}
                loading={<div className="py-8 text-center text-sm text-gray-400">Loading PDF…</div>}
                error={
                  <div className="py-8 text-center text-sm text-red-500">Failed to load PDF</div>
                }
              >
                {slots}
              </Document>
            ) : (
              <div key={`${group.documentId}-${group.entries[0]?.combinedPageNumber}`}>{slots}</div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

/**
 * FR-19: the bounding-box highlight. Chunk bboxes are stored in PDF points
 * with a top-left origin (PyMuPDF's coordinate space, matching the rendered
 * page image), so scaling to percentages of the page size positions the box
 * correctly at any render width. Pages processed before Phase 5 have no
 * stored size — the jump still works, the highlight is just skipped.
 */
function HighlightOverlay({
  highlight,
  entry,
}: {
  highlight: Highlight;
  entry: ManifestEntryDto;
}) {
  if (!entry.pageWidth || !entry.pageHeight) return null;
  const clamp = (v: number) => Math.min(100, Math.max(0, v));
  const { bbox } = highlight;
  return (
    <div
      className="pointer-events-none absolute animate-pulse rounded-sm border-2 border-amber-500 bg-amber-300/30"
      style={{
        left: `${clamp((bbox.x / entry.pageWidth) * 100)}%`,
        top: `${clamp((bbox.y / entry.pageHeight) * 100)}%`,
        width: `${clamp((bbox.width / entry.pageWidth) * 100)}%`,
        height: `${clamp((bbox.height / entry.pageHeight) * 100)}%`,
      }}
      title="Cited region"
    />
  );
}

function SlotPlaceholder({
  entry,
  projectId,
}: {
  entry: ManifestEntryDto;
  projectId: string;
}) {
  return entry.hasImage ? (
    <img
      src={api.pageThumbUrl(projectId, entry.combinedPageNumber)}
      alt={`Page ${entry.combinedPageNumber} placeholder`}
      className="w-full opacity-60"
      loading="lazy"
    />
  ) : (
    <div className="flex h-64 items-center justify-center text-sm text-gray-400">
      page {entry.combinedPageNumber} — processing…
    </div>
  );
}
