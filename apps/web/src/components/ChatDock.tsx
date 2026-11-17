import type { ProcessingStateDto } from "@cdip/shared";
import { MessageSquareIcon, XIcon } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { ChatPanel } from "@/components/ChatPanel";
import { useProcessing } from "@/components/Processing";
import { Spinner } from "@/components/shared";
import { Progress } from "@/components/ui/progress";
import { cn } from "@/lib/utils";
import { useAppStore } from "@/store";

const OPEN_KEY = "cdip-chat-open";
const READY_KEY = (projectId: string) => `cdip-chat-ready:${projectId}`;

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
    if (!state.data || progress.busy || latched) return;
    writeFlag(READY_KEY(projectId), true);
    setLatched(true);
  }, [state.data, progress.busy, latched, projectId]);
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
                : "inset-x-3 bottom-24 h-[min(70vh,640px)] rounded-xl"
              : large
                ? "right-6 bottom-24 h-[min(85vh,900px)] w-[min(760px,calc(100vw-3rem))] rounded-xl"
                : "right-6 bottom-24 h-[min(640px,calc(100vh-8rem))] w-[min(400px,calc(100vw-3rem))] rounded-xl",
            !open && "hidden",
          )}
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
          className="bg-card fixed right-6 bottom-24 z-40 w-72 rounded-xl border p-4 shadow-xl"
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
          className="bg-success text-success-foreground fixed right-24 bottom-9 z-40 rounded-full px-3 py-1.5 text-xs font-medium shadow-lg"
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
          title={ready.locked ? `Chat unlocks when processing finishes (${pagesLeft})` : "Ask about the drawings"}
          onClick={() => {
            setJustReady(false);
            setOpen((o) => !o);
          }}
          className={cn(
            "fixed right-6 bottom-6 z-40 grid size-14 place-items-center rounded-full shadow-lg transition",
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
