import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { useAppStore } from "../store";
import { CombinedViewer } from "./CombinedViewer";
import { DocumentsPanel } from "./DocumentsPanel";

/**
 * FR-17 layout. Phase 1 fills the left pane with documents/upload/status and
 * the right pane with the combined viewer; summary + chat land here later.
 */
export function ProjectView({ projectId }: { projectId: string }) {
  const openProject = useAppStore((s) => s.openProject);
  const project = useQuery({
    queryKey: ["projects", projectId],
    queryFn: async () => (await api.listProjects()).find((p) => p.id === projectId),
  });

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
      </div>
      <div className="flex min-h-0 flex-1">
        <aside className="w-72 shrink-0 border-r border-gray-200">
          <DocumentsPanel projectId={projectId} />
        </aside>
        <section className="min-w-0 flex-1">
          <CombinedViewer projectId={projectId} />
        </section>
      </div>
    </div>
  );
}
