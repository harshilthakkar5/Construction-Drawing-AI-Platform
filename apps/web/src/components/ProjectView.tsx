import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { ArrowLeftIcon, MessageSquareIcon, MessageSquareOffIcon } from "lucide-react";
import { api } from "@/api";
import { ChatPanel } from "@/components/ChatPanel";
import { CombinedViewer } from "@/components/CombinedViewer";
import { DocumentsPanel } from "@/components/DocumentsPanel";
import { DragDivider } from "@/components/DragDivider";
import { PageLoading } from "@/components/shared";
import { PortionsPanel } from "@/components/PortionsPanel";
import { RegionBanner } from "@/components/RegionBanner";
import { SummaryPanel } from "@/components/SummaryPanel";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { useAppStore } from "@/store";

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

  // Opening a project used to render the full three-pane layout instantly with
  // every pane empty, which reads as "broken" rather than "loading".
  if (project.isLoading) {
    return <PageLoading label="Opening project…" />;
  }

  return (
    <div className="flex h-full flex-col">
      <header className="bg-card flex shrink-0 items-center gap-3 border-b px-4 py-2.5">
        <Button variant="ghost" size="sm" onClick={() => openProject(null)}>
          <ArrowLeftIcon />
          Projects
        </Button>
        <Separator orientation="vertical" className="h-5" />
        <h2 className="truncate font-semibold">{project.data?.name ?? "…"}</h2>
        {project.data?.description && (
          <span className="text-muted-foreground truncate text-sm">{project.data.description}</span>
        )}
        <Button
          variant="outline"
          size="sm"
          className="ml-auto shrink-0 text-xs"
          onClick={() => setChatHidden((hidden) => !hidden)}
          title={chatHidden ? "Show the chat pane" : "Hide the chat pane and widen the viewer"}
        >
          {chatHidden ? <MessageSquareIcon /> : <MessageSquareOffIcon />}
          {chatHidden ? "Show chat" : "Hide chat"}
        </Button>
      </header>
      <div className="flex min-h-0 flex-1">
        {/* One scroll column, not three clipped boxes: a long summary used to
            be cut off mid-sentence while the documents area sat empty. Each
            section's heading sticks to the top so you keep your bearings. */}
        <aside
          className="bg-card shrink-0 overflow-y-auto border-r"
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
              className="bg-card shrink-0 border-r"
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
