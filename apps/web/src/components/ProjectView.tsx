import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "../api";
import { useAppStore } from "../store";
import { ChatPanel } from "./ChatPanel";
import { CombinedViewer } from "./CombinedViewer";
import { DocumentsPanel } from "./DocumentsPanel";
import { DragDivider } from "./DragDivider";
import { PortionsPanel } from "./PortionsPanel";
import { RegionBanner } from "./RegionBanner";
import { SummaryPanel } from "./SummaryPanel";

/**
 * FR-17 three-pane layout: sidebar (summary + portions + documents) | chat |
 * combined viewer. Both dividers are draggable and the chat pane can be hidden
 * outright, so the viewer can take the full width for a wide drawing. Widths
 * persist per browser.
 */
const SIDEBAR_KEY = "cdip-sidebar-width";
const CHAT_KEY = "cdip-chat-width";
const CHAT_HIDDEN_KEY = "cdip-chat-hidden";

function stored(key: string, fallback: number) {
  const value = Number(localStorage.getItem(key));
  return Number.isFinite(value) && value > 0 ? value : fallback;
}

export function ProjectView({ projectId }: { projectId: string }) {
  const openProject = useAppStore((s) => s.openProject);
  const project = useQuery({
    queryKey: ["projects", projectId],
    queryFn: async () => (await api.listProjects()).find((p) => p.id === projectId),
  });

  const [sidebarWidth, setSidebarWidth] = useState(() => stored(SIDEBAR_KEY, 288));
  const [chatWidth, setChatWidth] = useState(() => stored(CHAT_KEY, 384));
  const [chatHidden, setChatHidden] = useState(
    () => localStorage.getItem(CHAT_HIDDEN_KEY) === "1",
  );

  useEffect(() => localStorage.setItem(SIDEBAR_KEY, String(sidebarWidth)), [sidebarWidth]);
  useEffect(() => localStorage.setItem(CHAT_KEY, String(chatWidth)), [chatWidth]);
  useEffect(
    () => localStorage.setItem(CHAT_HIDDEN_KEY, chatHidden ? "1" : "0"),
    [chatHidden],
  );

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center gap-3 border-b border-hairline bg-surface px-4 py-2.5">
        <button
          className="flex items-center gap-1 rounded-md px-2 py-1 text-sm font-medium text-ink-soft transition hover:bg-page hover:text-ink"
          onClick={() => openProject(null)}
        >
          <span aria-hidden>←</span> Projects
        </button>
        <span className="h-5 w-px bg-hairline" aria-hidden />
        <h2 className="truncate font-semibold text-ink">{project.data?.name ?? "…"}</h2>
        {project.data?.description && (
          <span className="truncate text-sm text-ink-muted">{project.data.description}</span>
        )}
        <button
          className="ml-auto flex shrink-0 items-center gap-1.5 rounded-md border border-hairline px-2.5 py-1.5 text-xs font-medium text-ink-soft transition hover:bg-page hover:text-ink"
          onClick={() => setChatHidden((hidden) => !hidden)}
          title={chatHidden ? "Show the chat pane" : "Hide the chat pane and widen the viewer"}
        >
          {chatHidden ? "Show chat" : "Hide chat"}
          <svg
            width="14"
            height="14"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.7"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden
          >
            <path d="M21 12a8 8 0 0 1-8 8H7l-4 3v-6a8 8 0 0 1 8-8h2a8 8 0 0 1 8 3Z" />
            {!chatHidden && <path d="m3 3 18 18" />}
          </svg>
        </button>
      </header>
      <div className="flex min-h-0 flex-1">
        {/* One scroll column, not three clipped boxes: a long summary used to
            be cut off mid-sentence while the documents area sat empty. Each
            section's heading sticks to the top so you keep your bearings. */}
        <aside
          className="shrink-0 overflow-y-auto border-r border-hairline bg-surface"
          style={{ width: sidebarWidth }}
        >
          <SummaryPanel projectId={projectId} />
          {/* Categorization starts here: no region, no disciplines. */}
          <RegionBanner projectId={projectId} />
          <PortionsPanel projectId={projectId} />
          <DocumentsPanel projectId={projectId} />
        </aside>
        <DragDivider
          width={sidebarWidth}
          onResize={setSidebarWidth}
          min={220}
          max={560}
          title="Drag to resize the sidebar"
        />

        {!chatHidden && (
          <>
            <section
              className="shrink-0 border-r border-hairline bg-surface"
              style={{ width: chatWidth }}
            >
              <ChatPanel projectId={projectId} />
            </section>
            <DragDivider
              width={chatWidth}
              onResize={setChatWidth}
              min={280}
              max={800}
              title="Drag to resize the chat pane / viewer"
            />
          </>
        )}

        <section className="min-w-0 flex-1">
          <CombinedViewer projectId={projectId} />
        </section>
      </div>
    </div>
  );
}
