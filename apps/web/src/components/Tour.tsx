import { useCallback, useEffect, useLayoutEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * A first-run walkthrough: dim the app, cut a hole around one control, explain
 * it, Next / Back / Skip. Steps point at elements by `data-tour` attribute so
 * the components themselves carry no tour logic beyond the marker.
 *
 * A step whose target is missing (a panel that only exists once there is data)
 * is skipped rather than pointing at nothing.
 */
export interface TourStep {
  /** CSS selector for the element to spotlight. */
  target: string;
  title: string;
  body: string;
}

interface Rect {
  top: number;
  left: number;
  width: number;
  height: number;
}

const PADDING = 8;
const CARD_WIDTH = 320;
const CARD_HEIGHT = 210; // enough for the longest step copy at this width
const CARD_GAP = 12;

/**
 * Put the card beside the target, never on top of it: below if it fits, else
 * above, else to whichever side has room. A tall panel (the whole work column)
 * only ever has room to its side.
 */
function placeCard(rect: Rect): { top: number; left: number } {
  const { innerWidth: vw, innerHeight: vh } = window;
  const clamp = (value: number, max: number) => Math.min(Math.max(12, value), Math.max(12, max));

  const below = rect.top + rect.height + CARD_GAP;
  if (below + CARD_HEIGHT <= vh - 12) {
    return { top: below, left: clamp(rect.left, vw - CARD_WIDTH - 12) };
  }
  const above = rect.top - CARD_GAP - CARD_HEIGHT;
  if (above >= 12) {
    return { top: above, left: clamp(rect.left, vw - CARD_WIDTH - 12) };
  }
  const right = rect.left + rect.width + CARD_GAP;
  if (right + CARD_WIDTH <= vw - 12) {
    return { top: clamp(rect.top, vh - CARD_HEIGHT - 12), left: right };
  }
  const leftOf = rect.left - CARD_GAP - CARD_WIDTH;
  if (leftOf >= 12) {
    return { top: clamp(rect.top, vh - CARD_HEIGHT - 12), left: leftOf };
  }
  // Nowhere to hide: centre it and let the ring show through around the edges.
  return { top: (vh - CARD_HEIGHT) / 2, left: (vw - CARD_WIDTH) / 2 };
}

const seenKey = (key: string) => `cdip-tour:${key}`;

export const tourSeen = (key: string) => localStorage.getItem(seenKey(key)) === "1";
export const markTourSeen = (key: string) => localStorage.setItem(seenKey(key), "1");

function measure(target: string): Rect | null {
  const element = document.querySelector(target);
  if (!element) return null;
  const rect = element.getBoundingClientRect();
  if (rect.width === 0 && rect.height === 0) return null;
  return {
    top: rect.top - PADDING,
    left: rect.left - PADDING,
    width: rect.width + PADDING * 2,
    height: rect.height + PADDING * 2,
  };
}

export function Tour({
  steps,
  storageKey,
  onClose,
}: {
  steps: TourStep[];
  /** Marked as seen when the tour is finished or skipped. */
  storageKey: string;
  onClose: () => void;
}) {
  const [index, setIndex] = useState(0);
  const [rect, setRect] = useState<Rect | null>(null);

  const finish = useCallback(() => {
    markTourSeen(storageKey);
    onClose();
  }, [storageKey, onClose]);

  // Re-measure on every step, and while the layout moves under us.
  useLayoutEffect(() => {
    const update = () => setRect(measure(steps[index]?.target ?? ""));
    update();
    window.addEventListener("resize", update);
    window.addEventListener("scroll", update, true);
    const timer = setInterval(update, 500); // panels settle as data lands
    return () => {
      window.removeEventListener("resize", update);
      window.removeEventListener("scroll", update, true);
      clearInterval(timer);
    };
  }, [index, steps]);

  // A missing target means the step has nothing to point at — move along.
  useEffect(() => {
    if (rect !== null) return;
    const timer = setTimeout(() => {
      if (measure(steps[index]?.target ?? "")) return;
      if (index < steps.length - 1) setIndex((i) => i + 1);
      else finish();
    }, 700);
    return () => clearTimeout(timer);
  }, [rect, index, steps, finish]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") finish();
      if (event.key === "ArrowRight" && index < steps.length - 1) setIndex((i) => i + 1);
      if (event.key === "ArrowLeft" && index > 0) setIndex((i) => i - 1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [index, steps.length, finish]);

  const step = steps[index];
  if (!step || !rect) return null;

  const { top, left } = placeCard(rect);
  // Four panels around the target rather than one big shadow: the cut-out
  // stays exact whatever the target's size, and clicks anywhere dim closes.
  const dim = "bg-foreground/60 absolute";

  return (
    <div className="fixed inset-0 z-[60]" role="dialog" aria-modal="true" aria-label="Product tour">
      <div className={dim} style={{ top: 0, left: 0, right: 0, height: rect.top }} onClick={finish} />
      <div
        className={dim}
        style={{ top: rect.top, left: 0, width: rect.left, height: rect.height }}
        onClick={finish}
      />
      <div
        className={dim}
        style={{ top: rect.top, left: rect.left + rect.width, right: 0, height: rect.height }}
        onClick={finish}
      />
      <div
        className={dim}
        style={{ top: rect.top + rect.height, left: 0, right: 0, bottom: 0 }}
        onClick={finish}
      />
      <div
        className="pointer-events-none absolute rounded-lg ring-2 ring-white/80 transition-all duration-200"
        style={{ top: rect.top, left: rect.left, width: rect.width, height: rect.height }}
      />

      <div
        className={cn(
          "bg-popover text-popover-foreground absolute w-80 rounded-xl border p-4 shadow-xl",
          "animate-in fade-in-0 zoom-in-95",
        )}
        style={{ top, left }}
        onClick={(event) => event.stopPropagation()}
      >
        <p className="text-muted-foreground text-xs font-medium">
          Step {index + 1} of {steps.length}
        </p>
        <h3 className="mt-1 font-semibold">{step.title}</h3>
        <p className="text-muted-foreground mt-1 text-sm leading-relaxed">{step.body}</p>

        <div className="mt-4 flex items-center gap-2">
          <Button variant="ghost" size="sm" className="text-muted-foreground" onClick={finish}>
            Skip
          </Button>
          <div className="ml-auto flex items-center gap-2">
            {index > 0 && (
              <Button variant="outline" size="sm" onClick={() => setIndex((i) => i - 1)}>
                Back
              </Button>
            )}
            <Button
              size="sm"
              onClick={() => (index === steps.length - 1 ? finish() : setIndex((i) => i + 1))}
            >
              {index === steps.length - 1 ? "Got it" : "Next"}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}

/** The workspace walkthrough — one step per thing a new user has to find. */
export const WORKSPACE_TOUR: TourStep[] = [
  {
    target: '[data-tour="summary"]',
    title: "Summary and categories",
    body: "The project summary sits here, with every statement citing the sheet it came from — click one to jump the viewer to that page and highlight the source.",
  },
  {
    target: '[data-tour="categories"]',
    title: "Filter by discipline",
    body: "Each category lists the sheets it actually covers. Click one to show only those pages in the viewer and swap the summary to that discipline; 'Show all pages' puts everything back.",
  },
  {
    target: '[data-tour="generate-summary"]',
    title: "Summaries are yours to start",
    body: "Nothing is summarized automatically — it costs tokens, so each category has its own button and tells you the estimated cost before it spends anything.",
  },
  {
    target: '[data-tour="quick-actions"]',
    title: "The title-block region",
    body: "One box over a sheet number is what sorts the set into disciplines. Edit it or re-scan here if a category looks wrong.",
  },
  {
    target: '[data-tour="viewer-toolbar"]',
    title: "The combined set",
    body: "Every PDF in the project, page after page, without merging the files. Zoom, fit, or jump straight to a page number.",
  },
  {
    target: '[data-tour="chat-toggle"]',
    title: "Ask about the drawings",
    body: "Chat answers only from this project's content and cites the pages it used. Hide it to give the viewer the full width.",
  },
];
