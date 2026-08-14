import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CalendarIcon,
  FileTextIcon,
  MoreVerticalIcon,
  PencilIcon,
  PlusIcon,
  SearchIcon,
  Trash2Icon,
} from "lucide-react";
import { useMemo, useState } from "react";
import type { DashboardProjectRow } from "@cdip/shared";
import { api } from "@/api";
import { ConfirmDialog, Notice, PageHeader, PageLoading, Spinner, TextArea, TextField } from "@/components/shared";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { compactNumber, StatusPill } from "@/pages/DashboardPage";
import { useAppStore } from "@/store";

/**
 * Project gallery: search, status filter, sort, and a card per project with
 * its first-page thumbnail. Rows come from /dashboard (it already carries the
 * document/page/status rollup) so the grid needs no per-project fan-out.
 */
type SortKey = "recent" | "name" | "pages";

const SORTS: { key: SortKey; label: string }[] = [
  { key: "recent", label: "Recent" },
  { key: "name", label: "Name" },
  { key: "pages", label: "Pages" },
];

const STATUS_FILTERS = ["all", "completed", "processing", "failed", "empty"] as const;
type StatusFilter = (typeof STATUS_FILTERS)[number];

const STATUS_LABEL = (s: StatusFilter) =>
  s === "all" ? "All statuses" : s === "empty" ? "No documents" : s.charAt(0).toUpperCase() + s.slice(1);

export function ProjectsPage() {
  const openProject = useAppStore((s) => s.openProject);
  const queryClient = useQueryClient();
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState<StatusFilter>("all");
  const [sort, setSort] = useState<SortKey>("recent");
  const [creating, setCreating] = useState(false);
  // Rename and delete both open a dialog rather than a native prompt/confirm:
  // deleting a project is irreversible and wipes documents, vectors and
  // summaries, so the dialog has to say so — a browser confirm() cannot.
  const [renaming, setRenaming] = useState<DashboardProjectRow | null>(null);
  const [deleting, setDeleting] = useState<DashboardProjectRow | null>(null);

  const dashboard = useQuery({ queryKey: ["dashboard", 14], queryFn: () => api.dashboard(14) });

  const remove = useMutation({
    mutationFn: (id: string) => api.deleteProject(id),
    onSuccess: () => {
      setDeleting(null);
      void queryClient.invalidateQueries({ queryKey: ["dashboard"] }); // every range
      void queryClient.invalidateQueries({ queryKey: ["projects"] });
    },
  });
  const rename = useMutation({
    mutationFn: (p: { id: string; name: string }) => api.updateProject(p.id, { name: p.name }),
    onSuccess: () => {
      setRenaming(null);
      void queryClient.invalidateQueries({ queryKey: ["dashboard"] }); // every range
      void queryClient.invalidateQueries({ queryKey: ["projects"] });
    },
  });

  const rows = useMemo(() => {
    let list = dashboard.data?.projects ?? [];
    const q = search.trim().toLowerCase();
    if (q) {
      list = list.filter(
        (p) =>
          p.name.toLowerCase().includes(q) || (p.description ?? "").toLowerCase().includes(q),
      );
    }
    if (status !== "all") list = list.filter((p) => p.status === status);
    return [...list].sort((a, b) => {
      if (sort === "name") return a.name.localeCompare(b.name);
      if (sort === "pages") return b.pages - a.pages;
      return new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime();
    });
  }, [dashboard.data, search, status, sort]);

  return (
    <div className="@container/main h-full overflow-y-auto p-4 md:p-6">
      <div className="mx-auto flex max-w-[1600px] flex-col gap-4 md:gap-6">
        <PageHeader
          title="Projects"
          subtitle="Every drawing set you have access to."
          action={
            <Button onClick={() => setCreating(true)}>
              <PlusIcon />
              Add project
            </Button>
          }
        />

        {/* Filters: one row above the grid */}
        <div className="flex flex-wrap items-center gap-3">
          <div className="relative min-w-[240px] flex-1">
            <SearchIcon className="text-muted-foreground pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2" />
            <Input
              className="pl-9"
              placeholder="Search projects…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              aria-label="Search projects"
            />
          </div>

          <Select value={status} onValueChange={(value) => setStatus(value as StatusFilter)}>
            <SelectTrigger className="w-[170px]" aria-label="Filter by status">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {STATUS_FILTERS.map((s) => (
                <SelectItem key={s} value={s}>
                  {STATUS_LABEL(s)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          <Select value={sort} onValueChange={(value) => setSort(value as SortKey)}>
            <SelectTrigger className="w-[150px]" aria-label="Sort projects">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {SORTS.map((s) => (
                <SelectItem key={s.key} value={s.key}>
                  Sort: {s.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        {dashboard.isLoading && <PageLoading label="Loading projects…" />}
        {dashboard.isError && (
          <Notice tone="error">Could not load projects: {(dashboard.error as Error).message}</Notice>
        )}

        {dashboard.data && rows.length === 0 && (
          <Card>
            <CardContent className="text-muted-foreground py-10 text-center text-sm">
              {dashboard.data.projects.length === 0
                ? "No projects yet — create one to start uploading drawings."
                : "No projects match those filters."}
            </CardContent>
          </Card>
        )}

        <div className="grid gap-4 @2xl/main:grid-cols-2 @5xl/main:grid-cols-3 @7xl/main:grid-cols-4">
          {rows.map((project) => (
            <ProjectCard
              key={project.id}
              project={project}
              onOpen={() => openProject(project.id)}
              onRename={() => {
                rename.reset();
                setRenaming(project);
              }}
              onDelete={() => {
                remove.reset();
                setDeleting(project);
              }}
            />
          ))}
        </div>
      </div>

      {creating && <CreateProjectDialog onClose={() => setCreating(false)} />}

      {renaming && (
        <RenameProjectDialog
          project={renaming}
          busy={rename.isPending}
          error={rename.isError ? rename.error : undefined}
          onCancel={() => setRenaming(null)}
          onSubmit={(name) => rename.mutate({ id: renaming.id, name })}
        />
      )}

      {deleting && (
        <ConfirmDialog
          title={`Delete "${deleting.name}"?`}
          tone="danger"
          confirmLabel="Delete project"
          busyLabel="Deleting…"
          busy={remove.isPending}
          error={remove.isError ? remove.error : undefined}
          onCancel={() => setDeleting(null)}
          onConfirm={() => remove.mutate(deleting.id)}
          message={
            <>
              <p>This cannot be undone. It permanently removes:</p>
              <ul className="mt-2 list-disc space-y-0.5 pl-5 text-xs">
                <li>
                  {deleting.documents} document{deleting.documents === 1 ? "" : "s"} and{" "}
                  {deleting.pages} page{deleting.pages === 1 ? "" : "s"}, including the original
                  PDFs in storage
                </li>
                <li>every summary and category generated for it</li>
                <li>its search index, so chat can no longer answer about it</li>
                <li>its chat history</li>
              </ul>
              <p className="mt-2 text-xs">Recorded token spend is kept for reporting.</p>
            </>
          }
        />
      )}
    </div>
  );
}

/**
 * Rename is a dialog rather than prompt() so it can validate, show the API
 * error in place, and disable itself while saving.
 */
function RenameProjectDialog({
  project,
  busy,
  error,
  onCancel,
  onSubmit,
}: {
  project: DashboardProjectRow;
  busy: boolean;
  error?: unknown;
  onCancel: () => void;
  onSubmit: (name: string) => void;
}) {
  const [name, setName] = useState(project.name);
  const trimmed = name.trim();
  const unchanged = trimmed === project.name;

  return (
    <Dialog open onOpenChange={(open) => !open && !busy && onCancel()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Rename project</DialogTitle>
          <DialogDescription>The new name shows everywhere the project appears.</DialogDescription>
        </DialogHeader>
        <TextField
          label="Project name"
          value={name}
          onChange={setName}
          placeholder={project.name}
          required
        />
        {!!error && <p className="text-destructive text-xs">{(error as Error).message}</p>}
        <DialogFooter>
          <Button variant="outline" onClick={onCancel} disabled={busy}>
            Cancel
          </Button>
          <Button onClick={() => onSubmit(trimmed)} disabled={busy || !trimmed || unchanged}>
            {busy && <Spinner />}
            {busy ? "Saving…" : "Save"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function ProjectCard({
  project,
  onOpen,
  onRename,
  onDelete,
}: {
  project: DashboardProjectRow;
  onOpen: () => void;
  onRename: () => void;
  onDelete: () => void;
}) {
  return (
    <Card className="group relative gap-0 overflow-hidden py-0 transition-shadow hover:shadow-md">
      <button className="flex flex-1 flex-col text-left" onClick={onOpen}>
        <div className="bg-muted relative h-40 w-full overflow-hidden">
          <Thumbnail projectId={project.id} hasPages={project.pages > 0} />
          <span className="bg-background/90 absolute top-3 left-3 rounded-md px-2 py-0.5 text-xs font-medium shadow-sm">
            {project.documents} doc{project.documents === 1 ? "" : "s"}
          </span>
        </div>
        <div className="flex flex-1 flex-col p-4">
          <h3 className="truncate font-semibold" title={project.name}>
            {project.name}
          </h3>
          {project.description && (
            <p className="text-muted-foreground mt-0.5 truncate text-sm" title={project.description}>
              {project.description}
            </p>
          )}
          <dl className="text-muted-foreground mt-3 flex-1 space-y-1.5 text-sm">
            <div className="flex items-center gap-2">
              <FileTextIcon className="size-4" aria-hidden />
              <dt className="sr-only">Pages</dt>
              <dd>{compactNumber(project.pages)} pages</dd>
            </div>
            <div className="flex items-center gap-2">
              <CalendarIcon className="size-4" aria-hidden />
              <dt className="sr-only">Created</dt>
              <dd>{new Date(project.createdAt).toLocaleDateString()}</dd>
            </div>
          </dl>
          <div className="mt-3 flex items-center justify-between">
            <StatusPill status={project.status} />
            <span className="text-muted-foreground text-xs tabular-nums">
              {compactNumber(project.tokens.totalTokens)} tokens
            </span>
          </div>
        </div>
      </button>

      <div className="absolute top-3 right-3">
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant="secondary"
              size="icon-sm"
              className="bg-background/90 rounded-full shadow-sm"
              aria-label={`Actions for ${project.name}`}
            >
              <MoreVerticalIcon />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-40">
            <DropdownMenuItem onClick={onOpen}>
              <FileTextIcon />
              Open
            </DropdownMenuItem>
            <DropdownMenuItem onClick={onRename}>
              <PencilIcon />
              Rename
            </DropdownMenuItem>
            <DropdownMenuItem variant="destructive" onClick={onDelete}>
              <Trash2Icon />
              Delete
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </Card>
  );
}

/** First combined page's thumbnail; falls back to a blueprint placeholder. */
function Thumbnail({ projectId, hasPages }: { projectId: string; hasPages: boolean }) {
  const [failed, setFailed] = useState(false);
  if (!hasPages || failed) {
    return (
      <div className="bg-primary text-primary-foreground grid h-full w-full place-items-center">
        <svg width="72" height="56" viewBox="0 0 72 56" fill="none" aria-hidden>
          <rect
            x="8"
            y="8"
            width="56"
            height="40"
            stroke="currentColor"
            strokeOpacity="0.45"
            strokeWidth="1.5"
          />
          <path
            d="M32 8v40M8 28h24M32 34h32"
            stroke="currentColor"
            strokeOpacity="0.3"
            strokeWidth="1.2"
          />
        </svg>
      </div>
    );
  }
  return (
    <img
      src={api.pageThumbUrl(projectId, 1)}
      alt=""
      loading="lazy"
      onError={() => setFailed(true)}
      className="h-full w-full object-cover"
    />
  );
}

function CreateProjectDialog({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient();
  const openProject = useAppStore((s) => s.openProject);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");

  const create = useMutation({
    mutationFn: () =>
      api.createProject({ name: name.trim(), description: description.trim() || undefined }),
    onSuccess: (project) => {
      void queryClient.invalidateQueries({ queryKey: ["dashboard"] }); // every range
      void queryClient.invalidateQueries({ queryKey: ["projects"] });
      onClose();
      openProject(project.id);
    },
  });

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <form
          className="grid gap-4"
          onSubmit={(e) => {
            e.preventDefault();
            if (name.trim()) create.mutate();
          }}
        >
          <DialogHeader>
            <DialogTitle>New project</DialogTitle>
            <DialogDescription>
              A project holds one drawing set — upload its PDFs once it exists.
            </DialogDescription>
          </DialogHeader>
          <TextField
            label="Project name"
            value={name}
            onChange={setName}
            placeholder="e.g. Riverside Medical Center"
            required
          />
          <TextArea
            label="Description (optional)"
            value={description}
            onChange={setDescription}
            rows={3}
            placeholder="Anything that helps you recognise it later"
          />
          {create.isError && <Notice tone="error">{(create.error as Error).message}</Notice>}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" disabled={!name.trim() || create.isPending}>
              {create.isPending && <Spinner />}
              {create.isPending ? "Creating…" : "Create project"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
