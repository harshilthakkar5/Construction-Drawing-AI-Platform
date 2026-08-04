import { useRef } from "react";

/**
 * Vertical drag line between two panes (FR-17 layout is user-resizable: the
 * viewer needs to grow for wide drawings, the chat for long answers).
 *
 * Pointer capture keeps the drag alive when the cursor outruns the 4px handle,
 * so a fast drag doesn't drop halfway. The caller owns the width in px.
 */
export function DragDivider({
  width,
  onResize,
  min = 220,
  max = 900,
  /** Resizing a pane that sits to the RIGHT of the handle inverts the delta. */
  invert = false,
  title = "Drag to resize",
}: {
  width: number;
  onResize: (width: number) => void;
  min?: number;
  max?: number;
  invert?: boolean;
  title?: string;
}) {
  const start = useRef<{ x: number; width: number } | null>(null);

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      title={title}
      className="group relative w-1 shrink-0 cursor-col-resize bg-page transition-colors hover:bg-brand-300 active:bg-brand-500"
      onPointerDown={(e) => {
        start.current = { x: e.clientX, width };
        e.currentTarget.setPointerCapture(e.pointerId);
      }}
      onPointerMove={(e) => {
        if (!start.current) return;
        const delta = (e.clientX - start.current.x) * (invert ? -1 : 1);
        onResize(Math.min(max, Math.max(min, start.current.width + delta)));
      }}
      onPointerUp={(e) => {
        start.current = null;
        e.currentTarget.releasePointerCapture(e.pointerId);
      }}
      onDoubleClick={() => onResize(min)}
    >
      {/* Widens the grab area without taking layout space. */}
      <span className="absolute inset-y-0 -left-1 -right-1" />
    </div>
  );
}
