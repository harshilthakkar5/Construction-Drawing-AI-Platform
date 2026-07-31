import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "../api";
import { useAppStore } from "../store";
import { ChatPanel } from "./ChatPanel";
import { CombinedViewer } from "./CombinedViewer";
import { DocumentsPanel } from "./DocumentsPanel";
import { DragDivider } from "./DragDivider";
import { PortionsPanel } from "./PortionsPanel";
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
      <div className="flex items-center gap-3 border-b border-gray-200 px-4 py-2">
        <button className="text-sm text-blue-600 hover:underline" onClick={() => openProject(null)}>
          ← Projects
        </button>
        <h2 className="font-semibold">{project.data?.name ?? "…"}</h2>
        {project.data?.description && (
          <span className="truncate text-sm text-gray-500">{project.data.description}</span>
        )}
        <button
          className="ml-auto shrink-0 rounded border border-gray-300 px-2 py-1 text-xs text-gray-600 hover:bg-gray-50"
          onClick={() => setChatHidden((hidden) => !hidden)}
          title={chatHidden ? "Show the chat pane" : "Hide the chat pane and widen the viewer"}
        >
          {chatHidden ? "Show chat" : "Hide chat"}
        </button>
      </div>
      <div className="flex min-h-0 flex-1">
        {/* Each section scrolls on its own so a long summary never buries the
            portions list or the upload / document controls below it. */}
        <aside
          className="flex shrink-0 flex-col border-r border-gray-200"
          style={{ width: sidebarWidth }}
        >
          <div className="max-h-[40%] shrink-0 overflow-y-auto">
            <SummaryPanel projectId={projectId} />
          </div>
          <div className="shrink-0 overflow-y-auto">
            <PortionsPanel projectId={projectId} />
          </div>
          <div className="min-h-0 flex-1">
            <DocumentsPanel projectId={projectId} />
          </div>
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
              className="shrink-0 border-r border-gray-200 bg-gray-50"
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
