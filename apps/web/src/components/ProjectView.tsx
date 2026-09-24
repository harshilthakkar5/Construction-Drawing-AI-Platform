import { useQuery } from "@tanstack/react-query";
import {
  CircleHelpIcon,
  FilesIcon,
  FileTextIcon,
  LayersIcon,
  LayoutGridIcon,
  ListChecksIcon,
  MessageCircleQuestionIcon,
  MessageSquareIcon,
  PanelsTopLeftIcon,
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
import { FullViewButton, PageLoading } from "@/components/shared";
import { PortionsPanel } from "@/components/PortionsPanel";
import { RfiPanel } from "@/components/RfiPanel";
import { RegionBanner } from "@/components/RegionBanner";
import { SummaryPanel } from "@/components/SummaryPanel";
import { Tour, tourSeen, WORKSPACE_TOUR } from "@/components/Tour";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useMediaQuery } from "@/hooks/use-media-query";
import { cn } from "@/lib/utils";
import { StatusPill } from "@/pages/DashboardPage";
import { useAppStore } from "@/store";

/**
 * FR-17 project workspace: a project header, then the left work column
 * (summary / categories / documents / RFIs, tabbed) beside the combined
 * viewer. Chat is the third pane, shown or hidden from the Chat button in the
 * viewer's toolbar, so a wide drawing can have the whole width when nobody is
 * asking questions.
 *
 * Any pane can go FULL VIEW (its maximize button) and take the whole
 * workspace. The other panes are hidden with CSS rather than unmounted, so a
 * chat thread, a viewer's scroll position and an open RFI all survive the
 * round trip. Below the `lg` breakpoint three panes do not fit side by side,
 * so the workspace shows one at a time behind a switcher — the same
 * one-pane-visible state, chosen by width instead of a button.
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

type Pane = "work" | "chat" | "viewer";

const PANES: { id: Pane; label: string; icon: typeof FilesIcon }[] = [
  { id: "work", label: "Work", icon: PanelsTopLeftIcon },
  { id: "chat", label: "Chat", icon: MessageSquareIcon },
  { id: "viewer", label: "Drawings", icon: FileTextIcon },
];

/** One-pane-at-a-time switcher for narrow screens. */
function PaneSwitcher({ value, onChange }: { value: Pane; onChange: (pane: Pane) => void }) {
  return (
    <div
      role="tablist"
      aria-label="Workspace pane"
      className="bg-muted text-muted-foreground grid shrink-0 grid-cols-3 gap-1 rounded-lg p-1"
    >
      {PANES.map(({ id, label, icon: Icon }) => (
        <button
          key={id}
          type="button"
          role="tab"
          aria-selected={value === id}
          onClick={() => onChange(id)}
          className={cn(
            "flex items-center justify-center gap-1.5 rounded-md px-2 py-1.5 text-sm font-medium transition",
            value === id ? "bg-background text-foreground shadow-sm" : "hover:text-foreground",
          )}
        >
          <Icon className="size-4" />
          {label}
        </button>
      ))}
    </div>
  );
}

/** Roles shown before the list is trimmed — a full set is 15 chips. */
const ROLES_SHOWN = 3;

/**
 * The disciplines this project was created for. Labelled, because a bare row
 * of chips beside the project name reads as tags of unknown meaning — and
 * capped, because picking every role flooded the header.
 */
function ProjectRoles({ roles }: { roles: string[] }) {
  if (roles.length === 0) return null;

  const shown = roles.slice(0, ROLES_SHOWN);
  const rest = roles.slice(ROLES_SHOWN);

  return (
    <div className="hidden min-w-0 lg:block">
      <p className="text-muted-foreground text-xs font-medium">Roles</p>

      <div
        className="mt-1.5 flex max-w-xs flex-wrap items-center gap-1.5"
        // title={`Summaries lead with what matters to ${roles
        //   .map(roleLabel)
        //   .join(", ")}`}
      >
        {shown.map((role) => (
          <Badge key={role} variant="secondary" className="font-normal">
            {roleLabel(role)}
          </Badge>
        ))}

        {rest.length > 0 && (
          <Tooltip>
            <TooltipTrigger asChild>
              <button type="button">
                <Badge
                  variant="outline"
                  className="cursor-pointer font-normal hover:bg-muted"
                >
                  +{rest.length} more
                </Badge>
              </button>
            </TooltipTrigger>

            <TooltipContent className="max-w-xs">
              <div className="flex flex-wrap gap-1.5">
                {rest.map((role) => (
                  <Badge
                    key={role}
                    variant="secondary"
                    className="font-normal"
                  >
                    {roleLabel(role)}
                  </Badge>
                ))}
              </div>
            </TooltipContent>
          </Tooltip>
        )}
      </div>
    </div>
  );
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
  // Deliberately NOT persisted: full view is a reading mode you enter for one
  // long answer or one long review, not a layout preference. Coming back to a
  // project on the three panes is the right default.
  const [focus, setFocus] = useState<Pane | null>(null);
  // Three panes need about 1024px; below that the workspace shows one.
  const wide = useMediaQuery("(min-width: 1024px)");
  const [mobilePane, setMobilePane] = useState<Pane>("work");
  const only: Pane | null = wide ? focus : mobilePane;
  const visible = (pane: Pane) => (only ? only === pane : pane !== "chat" || !chatHidden);
  const toggleFocus = (pane: Pane) => setFocus((current) => (current === pane ? null : pane));

  // A citation, a summary item or a piece of RFI evidence asks the viewer to
  // jump. If the viewer is hidden behind a full-view pane (or the phone
  // switcher), bring it back — the viewer's own effect retries its scroll for
  // 1.5s, which is time enough for it to be shown.
  const jumpToPage = useAppStore((s) => s.jumpToPage);
  const viewerHidden = !visible("viewer");
  useEffect(() => {
    if (jumpToPage === null || !viewerHidden) return;
    if (wide) setFocus(null);
    else setMobilePane("viewer");
  }, [jumpToPage, viewerHidden, wide]);

  // Escape leaves full view, the way it closes every other overlay here.
  useEffect(() => {
    if (!focus) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !e.defaultPrevented) setFocus(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [focus]);

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
        <CardContent className="flex flex-wrap items-start justify-between gap-x-8 gap-y-4">
          <div className="flex min-w-[15rem] flex-1 items-center gap-4">
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
          </div>
          <ProjectRoles roles={project.data?.roles ?? []} />

          {/* One labelled group for everything you can DO to the project:
              finishing setup, the title-block region, and the walkthrough. */}
          <RegionBanner
            projectId={projectId}
            heading="Quick actions"
            actions={
              !setup.complete && (
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
              )
            }
            trailing={
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    onClick={() => setTourOpen(true)}
                    aria-label="Show me around"
                  >
                    <CircleHelpIcon />
                  </Button>
                </TooltipTrigger>
                <TooltipContent side="bottom">Show me around replay the tour</TooltipContent>
              </Tooltip>
            }
          />
        </CardContent>
      </Card>

      {!wide && <PaneSwitcher value={mobilePane} onChange={setMobilePane} />}

      <div className="flex min-h-0 flex-1">
        {/* Work column: one scroll, tabs over the panels. A container, so the
            tab labels shorten with the COLUMN's width, which the divider
            changes independently of the window's. */}
        <aside
          className={cn(
            "@container/work flex min-h-0 flex-col gap-4 overflow-y-auto",
            only ? "w-full" : "shrink-0 pr-3",
            !visible("work") && "hidden",
          )}
          style={only ? undefined : { width: sidebarWidth }}
        >
          <Card className="gap-0 py-4" data-tour="summary">
            <Tabs defaultValue="summary">
              <div className="flex items-center gap-2 px-4">
                <TabsList className="min-w-0 flex-1">
                  <TabsTrigger value="documents" title="Documents">
                    <FilesIcon />
                    <span className="@[16rem]/work:inline hidden">Docs</span>
                  </TabsTrigger>
                  <TabsTrigger value="summary" title="Summary & categories">
                    <LayoutGridIcon />
                    <span className="@[16rem]/work:inline hidden @[26rem]/work:hidden">Summary</span>
                    <span className="@[26rem]/work:inline hidden">Summary &amp; categories</span>
                  </TabsTrigger>
                  <TabsTrigger value="rfis" title="RFIs">
                    <MessageCircleQuestionIcon />
                    <span className="@[16rem]/work:inline hidden">RFIs</span>
                  </TabsTrigger>
                </TabsList>
                {wide && (
                  <FullViewButton
                    expanded={focus === "work"}
                    label="the work column"
                    onClick={() => toggleFocus("work")}
                  />
                )}
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
              <TabsContent value="rfis" className="mt-4">
                <RfiPanel projectId={projectId} />
              </TabsContent>
            </Tabs>
          </Card>
        </aside>
        {!only && (
          <DragDivider
            width={sidebarWidth}
            onResize={setSidebarWidth}
            min={300}
            max={620}
            title="Drag to resize the work column"
          />
        )}

        <section
          className={cn(
            "min-h-0",
            only ? "w-full" : "shrink-0 pl-3",
            !visible("chat") && "hidden",
          )}
          style={only ? undefined : { width: chatWidth }}
        >
          <Card className="h-full gap-0 overflow-hidden py-0">
            <ChatPanel
              projectId={projectId}
              expanded={only === "chat"}
              onToggleExpand={wide ? () => toggleFocus("chat") : undefined}
              onHide={wide && !only ? () => setChatHidden(true) : undefined}
            />
          </Card>
        </section>
        {!only && visible("chat") && (
          <DragDivider
            width={chatWidth}
            onResize={setChatWidth}
            min={280}
            max={800}
            title="Drag to resize the chat pane / viewer"
          />
        )}

        <section
          className={cn("min-w-0 flex-1", !only && "pl-3", !visible("viewer") && "hidden")}
        >
          <CombinedViewer
            projectId={projectId}
            expanded={only === "viewer"}
            onToggleExpand={wide ? () => toggleFocus("viewer") : undefined}
            chatShown={visible("chat")}
            onToggleChat={wide && !only ? () => setChatHidden((hidden) => !hidden) : undefined}
          />
        </section>
      </div>

      {tourOpen && (
        <Tour steps={WORKSPACE_TOUR} storageKey="workspace" onClose={() => setTourOpen(false)} />
      )}
    </div>
  );
}
