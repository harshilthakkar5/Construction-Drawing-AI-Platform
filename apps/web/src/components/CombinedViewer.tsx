import { useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ManifestEntryDto } from "@cdip/shared";
import { api } from "../api";
import { useAppStore, type Highlight } from "../store";
import { PageLoading } from "./ui";

const PAGE_WIDTH = 850;
const ZOOM_MIN = 0.4;
const ZOOM_MAX = 4;
const ZOOM_STEP = 0.25;
const ZOOM_KEY = "cdip-viewer-zoom";

/**
 * FR-6/FR-17/FR-20: renders the VIRTUAL combined set. The manifest maps each
 * combined page number to (document, page); source PDFs are never merged.
 *
 * The main display is the worker-rendered full-resolution page PNG (served via
 * a presigned URL). Plain <img> loads work cross-origin without CORS and don't
 * depend on fetching the whole source PDF, so this stays sharp and reliable on
 * DigitalOcean Spaces. Pages lazy-load via a shared IntersectionObserver; the
 * low-res thumbnail shows as a placeholder until the full image arrives.
 */
export function CombinedViewer({ projectId }: { projectId: string }) {
  const jumpToPage = useAppStore((s) => s.jumpToPage);
  const highlight = useAppStore((s) => s.highlight);
  const requestJump = useAppStore((s) => s.requestJump);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  // The scroll container doubles as the IntersectionObserver root, and the
  // observer can only be built once it exists — hence state, not just a ref.
  const [scrollEl, setScrollEl] = useState<HTMLDivElement | null>(null);
  const setScrollNode = useCallback((el: HTMLDivElement | null) => {
    scrollRef.current = el;
    setScrollEl(el);
  }, []);
  const observerRef = useRef<IntersectionObserver | null>(null);
  /** Mounted page elements by combined page number — see `registerNode`. */
  const nodesRef = useRef(new Map<number, HTMLElement>());
  const [nearViewport, setNearViewport] = useState<Set<number>>(new Set());
  const [jumpInput, setJumpInput] = useState("");
  const [zoom, setZoom] = useState(() => {
    const saved = Number(localStorage.getItem(ZOOM_KEY));
    return saved >= ZOOM_MIN && saved <= ZOOM_MAX ? saved : 1;
  });

  const applyZoom = useCallback((next: number) => {
    const clamped = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, Math.round(next * 100) / 100));
    setZoom(clamped);
    localStorage.setItem(ZOOM_KEY, String(clamped));
  }, []);

  // First visit: fit the page to the pane instead of opening at 100%, where an
  // 850px page overflows a narrow viewer and the drawing is half off-screen.
  // Once the user has zoomed (their choice is stored), never override it.
  const fitted = useRef(localStorage.getItem(ZOOM_KEY) !== null);
  useEffect(() => {
    if (fitted.current) return;
    const width = scrollRef.current?.clientWidth;
    if (!width) return;
    fitted.current = true;
    applyZoom((width - 32) / PAGE_WIDTH); // 32 = the p-4 padding
  }, [applyZoom]);

  // Ctrl/⌘ + wheel zooms instead of scrolling, the usual PDF-viewer gesture.
  useEffect(() => {
    const node = scrollRef.current;
    if (!node) return;
    const onWheel = (e: WheelEvent) => {
      if (!e.ctrlKey && !e.metaKey) return;
      e.preventDefault();
      applyZoom(zoom * (e.deltaY < 0 ? 1.1 : 1 / 1.1));
    };
    node.addEventListener("wheel", onWheel, { passive: false });
    return () => node.removeEventListener("wheel", onWheel);
  }, [zoom, applyZoom, scrollEl]);

  const manifest = useQuery({
    queryKey: ["manifest", projectId],
    queryFn: () => api.manifest(projectId),
    refetchInterval: (query) =>
      !query.state.data?.length || query.state.data.some((e) => !e.hasImage) ? 3000 : false,
  });
  const entries = useMemo(() => manifest.data ?? [], [manifest.data]);

  /**
   * Track each page element so the observer can pick it up whichever order
   * they arrive in.
   *
   * React attaches refs during commit, BEFORE effects run. On a first visit the
   * manifest is still loading, so the pages mount after the observer exists and
   * observing from here works. On a RE-visit the manifest comes straight from
   * the query cache, so every page element is attached before the observer is
   * built — the old code observed against a null ref, nothing was ever marked
   * near the viewport, and the viewer sat on blurred thumbnails forever.
   */
  const registerNode = useCallback((combined: number, el: HTMLElement | null) => {
    const nodes = nodesRef.current;
    const previous = nodes.get(combined);
    if (previous && previous !== el) observerRef.current?.unobserve(previous);
    if (el) {
      nodes.set(combined, el);
      observerRef.current?.observe(el);
    } else {
      nodes.delete(combined);
    }
  }, []);

  useEffect(() => {
    if (!scrollEl) return;
    const observer = new IntersectionObserver(
      (observed) => {
        setNearViewport((prev) => {
          const next = new Set(prev);
          for (const o of observed) {
            const combined = Number((o.target as HTMLElement).dataset.combined);
            if (o.isIntersecting) next.add(combined);
            else next.delete(combined);
          }
          // Same membership: return the old set so React skips the re-render.
          if (next.size === prev.size && [...next].every((n) => prev.has(n))) return prev;
          return next;
        });
      },
      { root: scrollEl, rootMargin: "1500px 0px" },
    );
    observerRef.current = observer;
    // Catch up on anything mounted before this ran (the re-visit case).
    for (const el of nodesRef.current.values()) observer.observe(el);
    return () => {
      observer.disconnect();
      observerRef.current = null;
    };
  }, [scrollEl]);

  // FR-16/FR-18 programmatic jump. Slots above the target change height as
  // images lazy-load, so re-assert the scroll position a few times until the
  // layout settles.
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
      <div className="flex items-center gap-3 border-b border-hairline bg-surface px-3 py-2 text-sm">
        <span className="text-xs font-semibold uppercase tracking-wide text-ink-muted">
          Combined set
        </span>
        <span className="text-ink-muted">{total} pages</span>

        <div className="ml-auto flex items-center gap-1" title="Ctrl/⌘ + scroll also zooms">
          <button
            className="h-7 w-7 rounded-md border border-hairline text-ink-soft transition hover:bg-page hover:text-ink disabled:opacity-40"
            onClick={() => applyZoom(zoom - ZOOM_STEP)}
            disabled={zoom <= ZOOM_MIN}
            aria-label="Zoom out"
          >
            −
          </button>
          <button
            className="w-14 rounded-md px-1 py-1 text-xs font-medium tabular-nums text-ink-soft transition hover:bg-page"
            onClick={() => applyZoom(1)}
            title="Reset to 100%"
          >
            {Math.round(zoom * 100)}%
          </button>
          <button
            className="h-7 w-7 rounded-md border border-hairline text-ink-soft transition hover:bg-page hover:text-ink disabled:opacity-40"
            onClick={() => applyZoom(zoom + ZOOM_STEP)}
            disabled={zoom >= ZOOM_MAX}
            aria-label="Zoom in"
          >
            +
          </button>
          <button
            className="rounded-md border border-hairline px-2 py-1 text-xs font-medium text-ink-soft transition hover:bg-page hover:text-ink"
            onClick={() => {
              const available = (scrollRef.current?.clientWidth ?? PAGE_WIDTH) - 32; // p-4
              applyZoom(available / PAGE_WIDTH);
            }}
            title="Fit the page to the viewer width"
          >
            Fit
          </button>
        </div>

        <form
          className="flex items-center gap-1"
          onSubmit={(e) => {
            e.preventDefault();
            const n = Number(jumpInput);
            if (Number.isInteger(n) && n >= 1 && n <= total) requestJump(n);
          }}
        >
          <label className="text-ink-muted">Go to page</label>
          <input
            className="w-20 rounded-md border border-hairline bg-surface px-2 py-1 text-ink outline-none focus:border-brand-500"
            value={jumpInput}
            onChange={(e) => setJumpInput(e.target.value)}
            placeholder={total ? `1–${total}` : "–"}
          />
        </form>
      </div>

      <div className="flex min-h-0 flex-1">
        {/* Thumbnail rail (FR-20) */}
        <div className="w-28 shrink-0 overflow-y-auto border-r border-hairline bg-surface p-2">
          {entries.map((e) => (
            <button
              key={e.combinedPageNumber}
              className="mb-2 block w-full rounded-md border border-hairline bg-surface p-1 text-left transition hover:border-brand-500 hover:shadow-sm"
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
                <div className="flex h-16 items-center justify-center bg-page text-xs text-ink-muted">
                  …
                </div>
              )}
              <div className="mt-0.5 text-center text-xs text-ink-muted">{e.combinedPageNumber}</div>
            </button>
          ))}
        </div>

        {/* Main pages */}
        {/* overflow-auto (not -y): a zoomed page is wider than the pane. */}
        <div ref={setScrollNode} className="flex-1 overflow-auto bg-page p-4">
          {manifest.isLoading && <PageLoading label="Loading pages…" />}
          {!manifest.isLoading && total === 0 && (
            <p className="text-sm text-ink-muted">
              No pages yet — upload a PDF and wait for processing.
            </p>
          )}
          {entries.map((entry) => (
            <div
              key={entry.combinedPageNumber}
              id={`combined-page-${entry.combinedPageNumber}`}
              data-combined={entry.combinedPageNumber}
              ref={(el) => registerNode(entry.combinedPageNumber, el)}
              className="mx-auto mb-4 overflow-hidden rounded-lg border border-hairline bg-surface shadow-sm"
              style={{ width: PAGE_WIDTH * zoom, minHeight: PAGE_WIDTH * zoom * 0.6 }}
            >
              <div className="relative">
                <PageImage
                  entry={entry}
                  projectId={projectId}
                  near={nearViewport.has(entry.combinedPageNumber)}
                />
                {highlight?.combinedPageNumber === entry.combinedPageNumber && (
                  <HighlightOverlay highlight={highlight} entry={entry} />
                )}
              </div>
              <div className="border-t border-hairline bg-surface px-2.5 py-1.5 text-xs text-ink-muted">
                {entry.filename} · page {entry.pageNumber} · combined {entry.combinedPageNumber}
              </div>
            </div>
          ))}
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
function HighlightOverlay({ highlight, entry }: { highlight: Highlight; entry: ManifestEntryDto }) {
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

/**
 * The full-resolution page render. Once the page is near the viewport, load
 * the full PNG; until it decodes, show the (blurry-but-cheap) thumbnail as a
 * placeholder so the layout doesn't jump. Not-yet-processed pages show a
 * status line.
 */
function PageImage({
  entry,
  projectId,
  near,
}: {
  entry: ManifestEntryDto;
  projectId: string;
  near: boolean;
}) {
  const [loaded, setLoaded] = useState(false);
  const imgRef = useRef<HTMLImageElement>(null);

  // A cached image can already be decoded by the time React attaches onLoad,
  // so the event never fires and the blurred placeholder would stay up for
  // good. Check `complete` once the full image is mounted.
  useEffect(() => {
    if (near && imgRef.current?.complete) setLoaded(true);
  }, [near]);

  if (!entry.hasImage) {
    return (
      <div className="flex h-64 items-center justify-center text-sm text-ink-muted">
        page {entry.combinedPageNumber} — processing…
      </div>
    );
  }

  return (
    <>
      {!loaded && (
        <img
          src={api.pageThumbUrl(projectId, entry.combinedPageNumber)}
          alt=""
          aria-hidden
          className="w-full"
          style={{ filter: "blur(1px)" }}
        />
      )}
      {near && (
        <img
          ref={imgRef}
          src={api.pageImageUrl(projectId, entry.combinedPageNumber)}
          alt={`Page ${entry.combinedPageNumber}`}
          onLoad={() => setLoaded(true)}
          className={`w-full ${loaded ? "" : "absolute inset-0 opacity-0"}`}
        />
      )}
    </>
  );
}
