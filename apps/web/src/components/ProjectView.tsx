import { useQuery } from "@tanstack/react-query";
import {
  FilesIcon,
  FileTextIcon,
  LayersIcon,
  LayoutGridIcon,
  MessageSquareIcon,
  XIcon,
} from "lucide-react";
import { useEffect, useState } from "react";
import type { DocumentDto } from "@cdip/shared";
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
import { Card, CardContent } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { StatusPill } from "@/pages/DashboardPage";
import { useAppStore } from "@/store";

/**
 * FR-17 project workspace: a project header, then the left work column
 * (summary / categories / documents, tabbed) beside the combined viewer. Chat
 * is the third pane and is toggled from the button floating over the viewer,
 * so a wide drawing can have the whole width when nobody is asking questions.
 *
 * Both dividers are draggable and the widths persist per browser.
 */
const SIDEBAR_KEY = "cdip-sidebar-width";
const CHAT_KEY = "cdip-chat-width";
const CHAT_HIDDEN_KEY = "cdip-chat-hidden";

function stored(key: string, fallback: number) {
  const value = Number(localStorage.getItem(key));
  return Number.isFinite(value) && value > 0 ? value : fallback;
}

/** The project's own status is the rollup of its documents (FR-9). */
function rollupStatus(documents: DocumentDto[] | undefined): string {
  if (!documents || documents.length === 0) return "empty";
  if (documents.some((d) => d.status === "uploaded" || d.status === "processing")) {
    return "processing";
  }
  if (documents.some((d) => d.status === "failed")) return "failed";
  return "completed";
}

export function ProjectView({ projectId }: { projectId: string }) {
  const project = useQuery({
    queryKey: ["projects", projectId],
    queryFn: async () => (await api.listProjects()).find((p) => p.id === projectId),
  });
  const documents = useQuery({
    queryKey: ["documents", projectId],
    queryFn: () => api.listDocuments(projectId),
  });

  const [sidebarWidth, setSidebarWidth] = useState(() => stored(SIDEBAR_KEY, 400));
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

  // Opening a project used to render the full layout instantly with every pane
  // empty, which reads as "broken" rather than "loading".
  if (project.isLoading) {
    return <PageLoading label="Opening project…" />;
  }

  const live = (documents.data ?? []).filter((d) => !d.supersededAt);
  const pages = live.reduce((total, d) => total + d.pages, 0);

  return (
    <div className="flex h-full flex-col gap-4 overflow-hidden p-4">
      {/* Project header — identity and state, above both columns. */}
      <Card className="shrink-0 py-4">
        <CardContent className="flex flex-wrap items-center gap-4">
          <span className="bg-muted text-muted-foreground grid size-12 shrink-0 place-items-center rounded-xl">
            <FileTextIcon className="size-5" />
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="truncate text-xl font-semibold tracking-tight">
                {project.data?.name ?? "…"}
              </h1>
              <StatusPill status={rollupStatus(documents.data)} />
            </div>
            <p className="text-muted-foreground mt-0.5 truncate text-sm">
              {project.data?.createdAt &&
                `Created on ${new Date(project.data.createdAt).toLocaleDateString(undefined, {
                  month: "short",
                  day: "numeric",
                  year: "numeric",
                })}`}
              {documents.data && (
                <>
                  {" · "}
                  {live.length} document{live.length === 1 ? "" : "s"} ·{" "}
                  {pages.toLocaleString()} page{pages === 1 ? "" : "s"}
                </>
              )}
            </p>
          </div>
          {project.data?.description && (
            <p className="text-muted-foreground hidden max-w-md truncate text-sm lg:block">
              {project.data.description}
            </p>
          )}
        </CardContent>
      </Card>

      <div className="flex min-h-0 flex-1">
        {/* Work column: one scroll, tabs over the three panels. */}
        <aside
          className="flex min-h-0 shrink-0 flex-col gap-4 overflow-y-auto pr-3"
          style={{ width: sidebarWidth }}
        >
          <Card className="gap-0 py-4">
            <Tabs defaultValue="summary">
              <div className="px-4">
                <TabsList className="w-full">
                  <TabsTrigger value="summary">
                    <LayoutGridIcon />
                    Summary
                  </TabsTrigger>
                  <TabsTrigger value="categories">
                    <LayersIcon />
                    Categories
                  </TabsTrigger>
                  <TabsTrigger value="documents">
                    <FilesIcon />
                    Docs
                  </TabsTrigger>
                </TabsList>
              </div>
              <TabsContent value="summary" className="mt-4">
                <SummaryPanel projectId={projectId} />
              </TabsContent>
              <TabsContent value="categories" className="mt-4">
                <PortionsPanel projectId={projectId} />
              </TabsContent>
              <TabsContent value="documents" className="mt-4">
                <DocumentsPanel projectId={projectId} />
              </TabsContent>
            </Tabs>
          </Card>

          {/* Categorization starts here: no region, no disciplines. */}
          <RegionBanner projectId={projectId} />
        </aside>
        <DragDivider
          width={sidebarWidth}
          onResize={setSidebarWidth}
          min={300}
          max={620}
          title="Drag to resize the work column"
        />

        {!chatHidden && (
          <>
            <section className="min-h-0 shrink-0 pl-3" style={{ width: chatWidth }}>
              <Card className="h-full gap-0 overflow-hidden py-0">
                <ChatPanel projectId={projectId} />
              </Card>
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

        <section className="relative min-w-0 flex-1 pl-3">
          <CombinedViewer projectId={projectId} />
          {/* Chat lives behind this button so the viewer can take the width. */}
          <Button
            size="icon"
            variant={chatHidden ? "default" : "secondary"}
            className="absolute right-6 bottom-16 size-11 rounded-full shadow-lg"
            onClick={() => setChatHidden((hidden) => !hidden)}
            title={chatHidden ? "Show the chat pane" : "Hide the chat pane and widen the viewer"}
          >
            {chatHidden ? <MessageSquareIcon /> : <XIcon />}
            <span className="sr-only">{chatHidden ? "Show chat" : "Hide chat"}</span>
          </Button>
        </section>
      </div>
    </div>
  );
}
