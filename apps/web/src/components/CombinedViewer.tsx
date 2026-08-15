import { useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ManifestEntryDto } from "@cdip/shared";
import { api } from "@/api";
import { useAppStore, type Highlight } from "@/store";
import { FilterIcon, ListRestartIcon, MinusIcon, PlusIcon } from "lucide-react";
import { PageLoading } from "@/components/shared";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Separator } from "@/components/ui/separator";
import { cn } from "@/lib/utils";

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
  const pageFilter = useAppStore((s) => s.pageFilter);
  const setPageFilter = useAppStore((s) => s.setPageFilter);
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
  /** The page the reader is looking at — drives the counter and the caption. */
  const [currentPage, setCurrentPage] = useState(1);
  /** Only set while the page box is being typed into; null shows currentPage. */
  const [jumpDraft, setJumpDraft] = useState<string | null>(null);
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
  const allEntries = useMemo(() => manifest.data ?? [], [manifest.data]);
  /** Category filter (FR-16): only the sheets carrying that discipline. */
  const entries = useMemo(
    () =>
      pageFilter
        ? allEntries.filter(
            (entry) => (entry.discipline ?? "unclassified") === pageFilter.discipline,
          )
        : allEntries,
    [allEntries, pageFilter],
  );

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

  /**
   * Whichever page sits closest to the top of the scroll box is "the page
   * you are on" — the counter and the footer caption both read it. Sampled on
   * a rAF so a fast scroll does not run this per scroll event.
   */
  useEffect(() => {
    if (!scrollEl) return;
    let frame = 0;
    const measure = () => {
      frame = 0;
      const top = scrollEl.getBoundingClientRect().top;
      let best: { page: number; distance: number } | null = null;
      for (const [page, node] of nodesRef.current) {
        const distance = Math.abs(node.getBoundingClientRect().top - top);
        if (!best || distance < best.distance) best = { page, distance };
      }
      if (best) setCurrentPage(best.page);
    };
    const onScroll = () => {
      if (!frame) frame = requestAnimationFrame(measure);
    };
    scrollEl.addEventListener("scroll", onScroll, { passive: true });
    measure();
    return () => {
      scrollEl.removeEventListener("scroll", onScroll);
      if (frame) cancelAnimationFrame(frame);
    };
  }, [scrollEl, entries]);

  // FR-16/FR-18 programmatic jump. Slots above the target change height as
  // images lazy-load, so re-assert the scroll position a few times until the
  // layout settles.
  useEffect(() => {
    if (jumpToPage == null) return;
    // A citation can point at a page the active filter hides; the jump wins.
    if (pageFilter && !entries.some((e) => e.combinedPageNumber === jumpToPage)) {
      setPageFilter(null);
      return;
    }
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
  }, [jumpToPage, requestJump, entries, pageFilter, setPageFilter]);

  const total = allEntries.length;
  const shown = entries.length;
  const currentEntry = entries.find((e) => e.combinedPageNumber === currentPage);

  return (
    <div className="bg-card @container/viewer flex h-full flex-col overflow-hidden rounded-xl border">
      <div className="flex items-center gap-x-3 border-b px-4 py-2.5 text-sm">
        {/* Left group truncates; the controls on the right never do. */}
        <div className="flex min-w-0 items-center gap-2">
          <span className="text-muted-foreground @[420px]/viewer:inline hidden shrink-0 text-xs font-semibold tracking-wide uppercase">
            Combined set
          </span>
          <span className="text-muted-foreground @[640px]/viewer:inline hidden shrink-0 text-xs">
            {pageFilter ? `${shown} of ${total} pages` : `${total} pages`}
          </span>
          {pageFilter && (
            <>
              <Badge variant="secondary" className="min-w-0 gap-1">
                <FilterIcon className="size-3 shrink-0" />
                <span className="truncate">{pageFilter.label}</span>
              </Badge>
              <Button
                variant="ghost"
                size="sm"
                className="shrink-0 text-xs"
                onClick={() => setPageFilter(null)}
                title="Stop filtering and list every page again"
              >
                <ListRestartIcon />
                <span className="@[760px]/viewer:inline hidden">Show all pages</span>
                <span className="@[760px]/viewer:hidden">All</span>
              </Button>
            </>
          )}
        </div>

        <div
          className="ml-auto flex shrink-0 items-center gap-1"
          title="Ctrl/⌘ + scroll also zooms"
        >
          <Button
            variant="outline"
            size="icon-sm"
            onClick={() => applyZoom(zoom - ZOOM_STEP)}
            disabled={zoom <= ZOOM_MIN}
            aria-label="Zoom out"
          >
            <MinusIcon />
          </Button>
          <Button
            variant="ghost"
            size="sm"
            className="w-14 px-1 text-xs tabular-nums"
            onClick={() => applyZoom(1)}
            title="Reset to 100%"
          >
            {Math.round(zoom * 100)}%
          </Button>
          <Button
            variant="outline"
            size="icon-sm"
            onClick={() => applyZoom(zoom + ZOOM_STEP)}
            disabled={zoom >= ZOOM_MAX}
            aria-label="Zoom in"
          >
            <PlusIcon />
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="text-xs"
            onClick={() => {
              const available = (scrollRef.current?.clientWidth ?? PAGE_WIDTH) - 32; // p-4
              applyZoom(available / PAGE_WIDTH);
            }}
            title="Fit the page to the viewer width"
          >
            Fit
          </Button>
          <Separator orientation="vertical" className="mx-1 h-5" />
        </div>

        <form
          className="flex shrink-0 items-center gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            const n = Number(jumpDraft);
            if (Number.isInteger(n) && n >= 1 && n <= total) requestJump(n);
            setJumpDraft(null);
          }}
        >
          <label
            className="text-muted-foreground @[820px]/viewer:inline hidden text-xs"
            htmlFor="viewer-goto"
          >
            Go to page
          </label>
          <Input
            id="viewer-goto"
            className="h-8 w-16 text-center"
            value={jumpDraft ?? String(total ? currentPage : "")}
            onChange={(e) => setJumpDraft(e.target.value)}
            onBlur={() => setJumpDraft(null)}
            placeholder={total ? "1" : "–"}
            aria-label="Current page"
          />
          <span className="text-muted-foreground text-xs tabular-nums">/ {total || "–"}</span>
        </form>
      </div>

      <div className="flex min-h-0 flex-1">
        {/* Thumbnail rail (FR-20) */}
        <div className="bg-card w-28 shrink-0 overflow-y-auto border-r p-2">
          {entries.map((e) => (
            <button
              key={e.combinedPageNumber}
              className={cn(
                "bg-card hover:border-ring mb-2 block w-full rounded-md border p-1 text-left transition hover:shadow-sm",
                e.combinedPageNumber === currentPage && "border-primary ring-ring/40 ring-2",
              )}
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
                <div className="flex h-16 items-center justify-center bg-muted text-xs text-muted-foreground">
                  …
                </div>
              )}
              <div className="mt-0.5 text-center text-xs text-muted-foreground">{e.combinedPageNumber}</div>
            </button>
          ))}
        </div>

        {/* Main pages */}
        {/* overflow-auto (not -y): a zoomed page is wider than the pane. */}
        <div ref={setScrollNode} className="flex-1 overflow-auto bg-muted p-4">
          {manifest.isLoading && <PageLoading label="Loading pages…" />}
          {!manifest.isLoading && total === 0 && (
            <p className="text-muted-foreground text-sm">
              No pages yet — upload a PDF and wait for processing.
            </p>
          )}
          {!manifest.isLoading && total > 0 && shown === 0 && (
            <div className="text-muted-foreground grid place-items-center gap-3 py-16 text-sm">
              <p>No pages are categorised as {pageFilter?.label} yet.</p>
              <Button variant="outline" size="sm" onClick={() => setPageFilter(null)}>
                <ListRestartIcon />
                Show all pages
              </Button>
            </div>
          )}
          {entries.map((entry) => (
            <div
              key={entry.combinedPageNumber}
              id={`combined-page-${entry.combinedPageNumber}`}
              data-combined={entry.combinedPageNumber}
              ref={(el) => registerNode(entry.combinedPageNumber, el)}
              className="bg-card mx-auto mb-4 overflow-hidden rounded-lg border shadow-sm"
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
            </div>
          ))}
        </div>
      </div>

      {/* What you are looking at: the virtual set never renames its sources. */}
      {currentEntry && (
        <div className="text-muted-foreground border-t px-4 py-2 text-xs">
          {currentEntry.filename} · page {currentEntry.pageNumber} · combined{" "}
          {currentEntry.combinedPageNumber}
        </div>
      )}
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
      className="pointer-events-none absolute animate-pulse rounded-sm border-2 border-warning bg-warning/30"
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
      <div className="flex h-64 items-center justify-center text-sm text-muted-foreground">
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
