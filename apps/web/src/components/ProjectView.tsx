import { useQuery } from "@tanstack/react-query";
import {
  CircleHelpIcon,
  FilesIcon,
  FileTextIcon,
  LayersIcon,
  LayoutGridIcon,
  ListChecksIcon,
  MessageSquareIcon,
  XIcon,
} from "lucide-react";
import { useEffect, useState } from "react";
import { roleLabel, type DocumentDto } from "@cdip/shared";
import { api } from "@/api";
import { ChatPanel } from "@/components/ChatPanel";
import { CombinedViewer } from "@/components/CombinedViewer";
import { DocumentsPanel } from "@/components/DocumentsPanel";
import { DragDivider } from "@/components/DragDivider";
import {
  ProjectSetup,
  resumeSetup,
  setupSkipped,
  skipSetup,
  useSetupProgress,
} from "@/components/ProjectSetup";
import { PageLoading } from "@/components/shared";
import { PortionsPanel } from "@/components/PortionsPanel";
import { RegionBanner } from "@/components/RegionBanner";
import { SummaryPanel } from "@/components/SummaryPanel";
import { Tour, tourSeen, WORKSPACE_TOUR } from "@/components/Tour";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
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

  // Setup runs before the workspace on a new project: upload → region →
  // categories. Progress is server state, so this survives a reload.
  const setup = useSetupProgress(projectId);
  // Latched, not derived: finishing step 3 flips `setup.complete`, and a
  // derived flag would yank the stepper away mid-click instead of letting the
  // user press "Open the workspace". null = not decided yet.
  const [setupOpen, setSetupOpen] = useState<boolean | null>(null);
  const [tourOpen, setTourOpen] = useState(false);

  useEffect(() => {
    if (setupOpen !== null || setup.loading) return;
    setSetupOpen(!setupSkipped(projectId) && !setup.complete);
  }, [setupOpen, setup.loading, setup.complete, projectId]);

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

  // The walkthrough waits for the workspace — pointing at panels that are not
  // on screen yet would spotlight nothing.
  useEffect(() => {
    if (setupOpen !== false || project.isLoading) return;
    if (tourSeen("workspace")) return;
    const timer = setTimeout(() => setTourOpen(true), 900);
    return () => clearTimeout(timer);
  }, [setupOpen, project.isLoading]);

  // Opening a project used to render the full layout instantly with every pane
  // empty, which reads as "broken" rather than "loading".
  if (project.isLoading || setupOpen === null) {
    return <PageLoading label="Opening project…" />;
  }

  if (setupOpen) {
    const leave = () => {
      skipSetup(projectId);
      setSetupOpen(false);
    };
    return <ProjectSetup projectId={projectId} onFinish={leave} onSkip={leave} />;
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
          {project.data?.roles && project.data.roles.length > 0 && (
            <div
              className="hidden max-w-xs flex-wrap gap-1 xl:flex"
              title="Roles this project was created for — summaries lead with what matters to them"
            >
              {project.data.roles.map((role) => (
                <Badge key={role} variant="outline" className="font-normal">
                  {roleLabel(role)}
                </Badge>
              ))}
            </div>
          )}
          {!setup.complete && (
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                resumeSetup(projectId);
                setSetupOpen(true);
              }}
            >
              <ListChecksIcon />
              Finish setup
            </Button>
          )}
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={() => setTourOpen(true)}
            title="Show me around"
            aria-label="Show me around"
          >
            <CircleHelpIcon />
          </Button>
          {/* Categorization is a project-level action, so it sits with the
              project rather than below the panels it feeds. */}
          <RegionBanner projectId={projectId} />
        </CardContent>
      </Card>

      <div className="flex min-h-0 flex-1">
        {/* Work column: one scroll, tabs over the three panels. */}
        <aside
          className="flex min-h-0 shrink-0 flex-col gap-4 overflow-y-auto pr-3"
          style={{ width: sidebarWidth }}
        >
          <Card className="gap-0 py-4" data-tour="summary">
            <Tabs defaultValue="summary">
              <div className="px-4">
                <TabsList className="w-full">
                  <TabsTrigger value="summary">
                    <LayoutGridIcon />
                    Summary &amp; categories
                  </TabsTrigger>
                  <TabsTrigger value="documents">
                    <FilesIcon />
                    Docs
                  </TabsTrigger>
                </TabsList>
              </div>
              {/* One tab: picking a category swaps the summary above it, so
                  splitting them made you flip back and forth to read it. */}
              <TabsContent value="summary" className="mt-4">
                <SummaryPanel projectId={projectId} />
                <Separator className="my-4" />
                <div data-tour="categories">
                  <h3 className="flex items-center gap-2 px-4 pb-2 text-sm font-semibold">
                    <LayersIcon className="text-muted-foreground size-4" />
                    Categories
                  </h3>
                  <PortionsPanel projectId={projectId} />
                </div>
              </TabsContent>
              <TabsContent value="documents" className="mt-4">
                <DocumentsPanel projectId={projectId} />
              </TabsContent>
            </Tabs>
          </Card>
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
            data-tour="chat-toggle"
            onClick={() => setChatHidden((hidden) => !hidden)}
            title={chatHidden ? "Show the chat pane" : "Hide the chat pane and widen the viewer"}
          >
            {chatHidden ? <MessageSquareIcon /> : <XIcon />}
            <span className="sr-only">{chatHidden ? "Show chat" : "Hide chat"}</span>
          </Button>
        </section>
      </div>

      {tourOpen && (
        <Tour steps={WORKSPACE_TOUR} storageKey="workspace" onClose={() => setTourOpen(false)} />
      )}
    </div>
  );
}
