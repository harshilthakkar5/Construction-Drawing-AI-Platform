import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import type { DashboardProjectRow } from "@cdip/shared";
import { api } from "../api";
import {
  Card,
  ConfirmDialog,
  GhostButton,
  Modal,
  Notice,
  PageHeader,
  PageLoading,
  PrimaryButton,
  Spinner,
  TextArea,
  TextField,
} from "../components/ui";
import { compactNumber, StatusPill } from "./DashboardPage";
import { useAppStore } from "../store";

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

export function ProjectsPage() {
  const openProject = useAppStore((s) => s.openProject);
  const queryClient = useQueryClient();
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState<(typeof STATUS_FILTERS)[number]>("all");
  const [sort, setSort] = useState<SortKey>("recent");
  const [creating, setCreating] = useState(false);
  const [menuFor, setMenuFor] = useState<string | null>(null);
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
    <div className="h-full overflow-y-auto p-6 lg:p-8" onClick={() => setMenuFor(null)}>
      <PageHeader
        title="Projects"
        action={
          <button
            className="flex items-center gap-2 rounded-lg bg-brand-700 px-4 py-2.5 text-sm font-semibold text-white hover:bg-brand-600"
            onClick={() => setCreating(true)}
          >
            Add Project <span aria-hidden>+</span>
          </button>
        }
      />

      {/* Filters: one row above the grid */}
      <div className="mb-5 flex flex-wrap items-center gap-3">
        <label className="flex min-w-[240px] flex-1 items-center gap-2 rounded-lg border border-hairline bg-surface px-3 py-2">
          <SearchIcon />
          <input
            className="min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-ink-muted"
            placeholder="Search…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </label>

        <label className="flex items-center gap-2 rounded-lg border border-hairline bg-surface px-3 py-2 text-sm">
          <FilterIcon />
          <span className="text-ink-muted">Status</span>
          <select
            className="bg-transparent font-medium text-ink outline-none"
            value={status}
            onChange={(e) => setStatus(e.target.value as (typeof STATUS_FILTERS)[number])}
          >
            {STATUS_FILTERS.map((s) => (
              <option key={s} value={s}>
                {s === "all" ? "All" : s === "empty" ? "No documents" : s}
              </option>
            ))}
          </select>
        </label>

        <label className="flex items-center gap-2 rounded-lg border border-hairline bg-surface px-3 py-2 text-sm">
          <span className="text-ink-muted">Sort by</span>
          <select
            className="bg-transparent font-medium text-ink outline-none"
            value={sort}
            onChange={(e) => setSort(e.target.value as SortKey)}
          >
            {SORTS.map((s) => (
              <option key={s.key} value={s.key}>
                {s.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      {dashboard.isLoading && <PageLoading label="Loading projects…" />}
      {dashboard.isError && (
        <Notice tone="error">Could not load projects: {(dashboard.error as Error).message}</Notice>
      )}

      {dashboard.data && rows.length === 0 && (
        <Card>
          <p className="py-10 text-center text-sm text-ink-muted">
            {dashboard.data.projects.length === 0
              ? "No projects yet — create one to start uploading drawings."
              : "No projects match those filters."}
          </p>
        </Card>
      )}

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
        {rows.map((project) => (
          <ProjectCard
            key={project.id}
            project={project}
            menuOpen={menuFor === project.id}
            onToggleMenu={(e) => {
              e.stopPropagation();
              setMenuFor((id) => (id === project.id ? null : project.id));
            }}
            onOpen={() => openProject(project.id)}
            onRename={() => {
              setMenuFor(null);
              rename.reset();
              setRenaming(project);
            }}
            onDelete={() => {
              setMenuFor(null);
              remove.reset();
              setDeleting(project);
            }}
          />
        ))}
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
                  {deleting.pages} page{deleting.pages === 1 ? "" : "s"}, including the
                  original PDFs in storage
                </li>
                <li>every summary and category generated for it</li>
                <li>its search index, so chat can no longer answer about it</li>
                <li>its chat history</li>
              </ul>
              <p className="mt-2 text-xs text-ink-muted">
                Recorded token spend is kept for reporting.
              </p>
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
    <Modal
      title="Rename project"
      onClose={() => !busy && onCancel()}
      footer={
        <>
          <GhostButton onClick={onCancel} disabled={busy}>
            Cancel
          </GhostButton>
          <button
            type="button"
            onClick={() => onSubmit(trimmed)}
            disabled={busy || !trimmed || unchanged}
            className="inline-flex items-center gap-1.5 rounded-md bg-brand-600 px-3 py-1.5 text-xs font-medium text-white transition hover:bg-brand-700 disabled:opacity-50"
          >
            {busy && <Spinner />}
            {busy ? "Saving…" : "Save"}
          </button>
        </>
      }
    >
      <div className="mt-3">
        <TextField
          label="Project name"
          value={name}
          onChange={setName}
          placeholder={project.name}
          required
        />
        {!!error && (
          <p className="mt-2 text-xs text-red-600">{(error as Error).message}</p>
        )}
      </div>
    </Modal>
  );
}

function ProjectCard({
  project,
  menuOpen,
  onToggleMenu,
  onOpen,
  onRename,
  onDelete,
}: {
  project: DashboardProjectRow;
  menuOpen: boolean;
  onToggleMenu: (e: React.MouseEvent) => void;
  onOpen: () => void;
  onRename: () => void;
  onDelete: () => void;
}) {
  return (
    <article className="group relative flex h-full flex-col overflow-hidden rounded-xl border border-hairline bg-surface transition hover:shadow-md">
      <button className="flex flex-1 flex-col text-left" onClick={onOpen}>
        <div className="relative h-40 w-full overflow-hidden bg-page">
          <Thumbnail projectId={project.id} hasPages={project.pages > 0} />
          <span className="absolute left-3 top-3 rounded-md bg-surface/95 px-2 py-0.5 text-xs font-medium text-ink shadow-sm">
            {project.documents} doc{project.documents === 1 ? "" : "s"}
          </span>
        </div>
        <div className="flex flex-1 flex-col p-4">
          <h3 className="truncate font-semibold text-ink" title={project.name}>
            {project.name}
          </h3>
          {project.description && (
            <p className="mt-0.5 truncate text-sm text-ink-muted" title={project.description}>
              {project.description}
            </p>
          )}
          <dl className="mt-3 flex-1 space-y-1.5 text-sm text-ink-soft">
            <div className="flex items-center gap-2">
              <PagesGlyph />
              <dt className="sr-only">Pages</dt>
              <dd>{compactNumber(project.pages)} pages</dd>
            </div>
            <div className="flex items-center gap-2">
              <CalendarGlyph />
              <dt className="sr-only">Created</dt>
              <dd>{new Date(project.createdAt).toLocaleDateString()}</dd>
            </div>
          </dl>
          <div className="mt-3 flex items-center justify-between">
            <StatusPill status={project.status} />
            <span className="text-xs tabular-nums text-ink-muted">
              {compactNumber(project.tokens.totalTokens)} tokens
            </span>
          </div>
        </div>
      </button>

      <div className="absolute right-3 top-3">
        <button
          className="grid h-8 w-8 place-items-center rounded-full bg-surface/95 text-ink-soft shadow-sm hover:text-ink"
          onClick={onToggleMenu}
          aria-label={`Actions for ${project.name}`}
          aria-expanded={menuOpen}
        >
          ⋯
        </button>
        {menuOpen && (
          <div
            className="absolute right-0 top-9 z-10 w-40 overflow-hidden rounded-lg border border-hairline bg-surface shadow-lg"
            onClick={(e) => e.stopPropagation()}
          >
            <button
              className="block w-full px-3 py-2 text-left text-sm text-ink-soft hover:bg-page"
              onClick={onOpen}
            >
              Open
            </button>
            <button
              className="block w-full px-3 py-2 text-left text-sm text-ink-soft hover:bg-page"
              onClick={onRename}
            >
              Rename
            </button>
            <button
              className="block w-full px-3 py-2 text-left text-sm text-red-600 hover:bg-page"
              onClick={onDelete}
            >
              Delete
            </button>
          </div>
        )}
      </div>
    </article>
  );
}

/** First combined page's thumbnail; falls back to a blueprint placeholder. */
function Thumbnail({ projectId, hasPages }: { projectId: string; hasPages: boolean }) {
  const [failed, setFailed] = useState(false);
  if (!hasPages || failed) {
    return (
      <div className="grid h-full w-full place-items-center bg-brand-900/95">
        <svg width="72" height="56" viewBox="0 0 72 56" fill="none" aria-hidden>
          <rect
            x="8"
            y="8"
            width="56"
            height="40"
            stroke="white"
            strokeOpacity="0.4"
            strokeWidth="1.5"
          />
          <path
            d="M32 8v40M8 28h24M32 34h32"
            stroke="white"
            strokeOpacity="0.25"
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
    <div
      className="fixed inset-0 z-50 grid place-items-center bg-ink/40 p-4"
      onClick={onClose}
      role="dialog"
      aria-modal
      aria-label="Create project"
    >
      <form
        className="w-full max-w-md space-y-4 rounded-xl bg-surface p-6 shadow-xl"
        onClick={(e) => e.stopPropagation()}
        onSubmit={(e) => {
          e.preventDefault();
          if (name.trim()) create.mutate();
        }}
      >
        <h2 className="text-lg font-semibold text-ink">New project</h2>
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
        <div className="flex justify-end gap-2 pt-1">
          <button
            type="button"
            className="rounded-md px-4 py-2.5 text-sm font-medium text-ink-soft hover:bg-page"
            onClick={onClose}
          >
            Cancel
          </button>
          <PrimaryButton disabled={!name.trim() || create.isPending}>
            {create.isPending ? "Creating…" : "Create project"}
          </PrimaryButton>
        </div>
      </form>
    </div>
  );
}

const g = {
  width: 16,
  height: 16,
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.7,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
  "aria-hidden": true,
};
const SearchIcon = () => (
  <svg {...g} className="text-ink-muted">
    <circle cx="11" cy="11" r="7" />
    <path d="m20 20-3.5-3.5" />
  </svg>
);
const FilterIcon = () => (
  <svg {...g} className="text-ink-muted">
    <path d="M3 5h18l-7 8v6l-4 2v-8L3 5Z" />
  </svg>
);
const PagesGlyph = () => (
  <svg {...g} className="text-ink-muted">
    <path d="M7 3h7l5 5v13H7z" />
    <path d="M14 3v5h5" />
  </svg>
);
const CalendarGlyph = () => (
  <svg {...g} className="text-ink-muted">
    <rect x="3" y="5" width="18" height="16" rx="2" />
    <path d="M3 10h18M8 3v4M16 3v4" />
  </svg>
);
