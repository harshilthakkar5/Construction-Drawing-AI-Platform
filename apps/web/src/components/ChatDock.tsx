import type { ProcessingStateDto } from "@cdip/shared";
import { MessageSquareIcon, XIcon } from "lucide-react";
import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties } from "react";
import { ChatPanel } from "@/components/ChatPanel";
import { useProcessing } from "@/components/Processing";
import { Spinner } from "@/components/shared";
import { Progress } from "@/components/ui/progress";
import { cn } from "@/lib/utils";
import { useAppStore } from "@/store";

const OPEN_KEY = "cdip-chat-open";
// v2: v1 saved the corner on every load, so it would hide the new default from everyone.
const POS_KEY = "cdip-chat-pos-v2";
const BUTTON = 56;
const MARGIN = 8;

/** The button's offset from the bottom-right corner, kept on screen. */
type Pos = { right: number; bottom: number };

const clamp = (n: number, lo: number, hi: number) => Math.min(Math.max(n, lo), Math.max(lo, hi));

export function clampPos(p: Pos, vw: number, vh: number): Pos {
  return { right: clamp(p.right, MARGIN, vw - BUTTON - MARGIN), bottom: clamp(p.bottom, MARGIN, vh - BUTTON - MARGIN) };
}

/** Where the person dragged it, or null when they never have. */
function readPos(): Pos | null {
  try {
    const p = JSON.parse(localStorage.getItem(POS_KEY) ?? "null");
    if (Number.isFinite(p?.right) && Number.isFinite(p?.bottom)) return p;
  } catch {
    // blocked storage: use the default place
  }
  return null;
}

/** Default place: the bottom-left corner of the work column, clear of the drawing. */
function defaultPos(vw: number, vh: number): Pos {
  const left = document.querySelector('[data-tour="summary"]')?.getBoundingClientRect().left ?? 24;
  return clampPos({ right: vw - left - BUTTON, bottom: 20 }, vw, vh);
}

/** Where a popup opens beside the button: on the side and above or below it, whichever has room. */
export function anchorStyle(p: Pos, vw: number, vh: number, gap = 16): CSSProperties {
  const style: CSSProperties = {};
  if (p.right > vw / 2) style.left = vw - p.right - BUTTON;
  else style.right = p.right;
  if (p.bottom > vh / 2) {
    style.top = vh - p.bottom + gap;
    style.maxHeight = vh - (style.top as number) - MARGIN;
  } else {
    style.bottom = p.bottom + BUTTON + gap;
    style.maxHeight = vh - (style.bottom as number) - MARGIN;
  }
  return style;
}
// v2: v1 latched as soon as nothing was left to process — including "nothing chosen yet".
const READY_KEY = (projectId: string) => `cdip-chat-ready-v2:${projectId}`;

function readFlag(key: string) {
  try {
    return localStorage.getItem(key) === "1";
  } catch {
    return false;
  }
}

function writeFlag(key: string, on: boolean) {
  try {
    localStorage.setItem(key, on ? "1" : "0");
  } catch {
    // Private window or blocked storage: the chat still works, it just forgets.
  }
}

/** Pages done and to do across the disciplines someone chose to process. */
export function chosenProgress(state: ProcessingStateDto | undefined) {
  const chosen = state?.disciplines.filter((d) => d.chosen) ?? [];
  const total = chosen.reduce((sum, d) => sum + d.pages, 0);
  const done = chosen.reduce((sum, d) => sum + Math.min(d.processed, d.pages), 0);
  return { busy: chosen.some((d) => d.processed < d.pages), done, total };
}

/**
 * Chat waits for the project's first full processing run, then stays open:
 * a "Process now" pressed later must not take chat away again. Latched per
 * browser once processing is seen finished.
 */
export function useChatReady(projectId: string) {
  const state = useProcessing(projectId);
  const progress = chosenProgress(state.data);
  const [latched, setLatched] = useState(() => readFlag(READY_KEY(projectId)));
  useEffect(() => {
    // Only a finished run counts: with nothing chosen yet, or a chosen discipline that turned
    // out empty ("Unsorted" once sorting ends), nothing has been processed for chat to read.
    if (!state.data || progress.busy || progress.total === 0 || latched) return;
    writeFlag(READY_KEY(projectId), true);
    setLatched(true);
  }, [state.data, progress.busy, progress.total, latched, projectId]);
  return { ...progress, locked: progress.busy && !latched };
}

/**
 * The project chat as a floating button and window, like a website chatbot.
 * The window is hidden with CSS rather than unmounted, so a thread survives
 * closing it. Both sizes have an expand button; on a phone, expanded is full screen.
 */
export function ChatDock({ projectId, fullScreen }: { projectId: string; fullScreen: boolean }) {
  const ready = useChatReady(projectId);
  const [open, setOpen] = useState(() => readFlag(OPEN_KEY));
  const [large, setLarge] = useState(false);
  const [justReady, setJustReady] = useState(false);
  useEffect(() => writeFlag(OPEN_KEY, open), [open]);

  // The button can be dragged anywhere; a drag is not a click.
  const [view, setView] = useState(() => ({ w: window.innerWidth, h: window.innerHeight }));
  const [saved, setSaved] = useState(readPos);
  const [pos, setPos] = useState<Pos>(() =>
    saved ? clampPos(saved, window.innerWidth, window.innerHeight) : { right: 24, bottom: 20 },
  );
  // The work column is on screen by now; measured before paint, so the button never jumps.
  useLayoutEffect(() => {
    if (!saved) setPos(defaultPos(window.innerWidth, window.innerHeight));
  }, [saved]);
  useEffect(() => {
    const onResize = () => {
      setView({ w: window.innerWidth, h: window.innerHeight });
      setPos((p) => (saved ? clampPos(p, window.innerWidth, window.innerHeight) : defaultPos(window.innerWidth, window.innerHeight)));
    };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [saved]);
  const drag = useRef<{ x: number; y: number; from: Pos; moved: boolean } | null>(null);
  const dragged = useRef(false);
  const anchor = anchorStyle(pos, view.w, view.h);
  const vertical = { top: anchor.top, bottom: anchor.bottom, maxHeight: anchor.maxHeight };

  // Say so once when chat unlocks while the person is here.
  const wasLocked = useRef(ready.locked);
  useEffect(() => {
    if (wasLocked.current && !ready.locked) setJustReady(true);
    wasLocked.current = ready.locked;
  }, [ready.locked]);
  useEffect(() => {
    if (!justReady) return;
    const timer = setTimeout(() => setJustReady(false), 10_000);
    return () => clearTimeout(timer);
  }, [justReady]);

  // On a phone the chat covers the viewer, so a citation clicked there steps aside.
  const jumpToPage = useAppStore((s) => s.jumpToPage);
  useEffect(() => {
    if (jumpToPage !== null && fullScreen) setOpen(false);
  }, [jumpToPage, fullScreen]);

  const percent = ready.total ? Math.round((ready.done / ready.total) * 100) : 0;
  const pagesLeft = `${ready.done} of ${ready.total} pages`;

  return (
    <>
      {!ready.locked && (
        <div
          role="dialog"
          aria-label="Project chat"
          className={cn(
            "bg-card fixed z-40 flex flex-col overflow-hidden border shadow-2xl",
            fullScreen
              ? large
                ? "inset-0"
                : "inset-x-3 h-[min(70vh,640px)] rounded-xl"
              : large
                ? "h-[min(85vh,900px)] w-[min(760px,calc(100vw-3rem))] rounded-xl"
                : "h-[min(640px,calc(100vh-8rem))] w-[min(400px,calc(100vw-3rem))] rounded-xl",
            !open && "hidden",
          )}
          style={fullScreen ? (large ? undefined : vertical) : anchor}
        >
          <ChatPanel
            projectId={projectId}
            expanded={large}
            onToggleExpand={() => setLarge((l) => !l)}
            onHide={() => setOpen(false)}
          />
        </div>
      )}

      {ready.locked && open && (
        <div
          role="status"
          className="bg-card fixed z-40 w-72 overflow-y-auto rounded-xl border p-4 shadow-xl"
          style={anchor}
        >
          <div className="flex items-start gap-2">
            <p className="min-w-0 flex-1 text-sm font-medium">
              Chat will be ready when processing finishes
            </p>
            <button
              type="button"
              aria-label="Close"
              className="text-muted-foreground hover:text-foreground"
              onClick={() => setOpen(false)}
            >
              <XIcon className="size-4" />
            </button>
          </div>
          <Progress value={percent} className="mt-3" />
          <p className="text-muted-foreground mt-1.5 text-xs tabular-nums">
            {pagesLeft} processed. You can keep working meanwhile.
          </p>
        </div>
      )}

      {justReady && !open && (
        <button
          type="button"
          className="bg-success text-success-foreground fixed z-40 rounded-full px-3 py-1.5 text-xs font-medium shadow-lg"
          style={anchorStyle(pos, view.w, view.h, 8)}
          onClick={() => {
            setJustReady(false);
            setOpen(true);
          }}
        >
          Processing finished: chat is ready
        </button>
      )}

      {!(fullScreen && large && open && !ready.locked) && (
        <button
          type="button"
          data-tour="chat-toggle"
          aria-label={ready.locked ? `Chat, ready when processing finishes (${pagesLeft})` : open ? "Close chat" : "Open chat"}
          aria-expanded={open}
          title={ready.locked ? `Chat unlocks when processing finishes (${pagesLeft}). Drag to move.` : "Ask about the drawings. Drag to move."}
          style={{ right: pos.right, bottom: pos.bottom }}
          onPointerDown={(e) => {
            drag.current = { x: e.clientX, y: e.clientY, from: pos, moved: false };
            e.currentTarget.setPointerCapture(e.pointerId);
          }}
          onPointerMove={(e) => {
            const d = drag.current;
            if (!d) return;
            const dx = e.clientX - d.x;
            const dy = e.clientY - d.y;
            if (!d.moved && Math.hypot(dx, dy) < 5) return;
            d.moved = true;
            setPos(clampPos({ right: d.from.right - dx, bottom: d.from.bottom - dy }, view.w, view.h));
          }}
          onPointerUp={() => {
            dragged.current = drag.current?.moved ?? false;
            drag.current = null;
            if (!dragged.current) return;
            try {
              localStorage.setItem(POS_KEY, JSON.stringify(pos));
            } catch {
              // blocked storage: the button just returns to its default place next time
            }
            setSaved(pos);
          }}
          onPointerCancel={() => {
            drag.current = null;
          }}
          onClick={() => {
            if (dragged.current) {
              dragged.current = false;
              return;
            }
            setJustReady(false);
            setOpen((o) => !o);
          }}
          className={cn(
            "fixed z-40 grid size-14 cursor-grab touch-none place-items-center rounded-full shadow-lg transition-transform select-none active:cursor-grabbing",
            ready.locked
              ? "bg-muted text-muted-foreground"
              : "bg-primary text-primary-foreground hover:scale-105",
          )}
        >
          {open && !ready.locked ? <XIcon className="size-6" /> : <MessageSquareIcon className="size-6" />}
          {ready.locked && (
            <span className="bg-card absolute -top-0.5 -right-0.5 grid size-5 place-items-center rounded-full border">
              <Spinner className="size-3" />
            </span>
          )}
        </button>
      )}
    </>
  );
}
