import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api";
import { useAppStore } from "../store";

export function ProjectList() {
  const queryClient = useQueryClient();
  const openProject = useAppStore((s) => s.openProject);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");

  const projects = useQuery({ queryKey: ["projects"], queryFn: api.listProjects });
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["projects"] });

  const create = useMutation({
    mutationFn: () =>
      api.createProject({ name: name.trim(), description: description.trim() || undefined }),
    onSuccess: () => {
      setName("");
      setDescription("");
      invalidate();
    },
  });
  const rename = useMutation({
    mutationFn: (p: { id: string; name: string }) => api.updateProject(p.id, { name: p.name }),
    onSuccess: invalidate,
  });
  const remove = useMutation({
    mutationFn: (id: string) => api.deleteProject(id),
    onSuccess: invalidate,
  });

  return (
    <div className="mx-auto max-w-3xl p-6">
      <h2 className="text-xl font-semibold">Projects</h2>

      <form
        className="mt-4 flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (name.trim()) create.mutate();
        }}
      >
        <input
          className="flex-1 rounded border border-gray-300 px-3 py-2 text-sm"
          placeholder="Project name"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <input
          className="flex-1 rounded border border-gray-300 px-3 py-2 text-sm"
          placeholder="Description (optional)"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />
        <button
          className="rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
          disabled={!name.trim() || create.isPending}
        >
          Create
        </button>
      </form>
      {create.isError && (
        <p className="mt-2 text-sm text-red-600">{(create.error as Error).message}</p>
      )}

      <ul className="mt-6 divide-y divide-gray-200 rounded border border-gray-200">
        {projects.data?.map((p) => (
          <li key={p.id} className="flex items-center gap-3 p-3">
            <button
              className="flex-1 text-left"
              onClick={() => openProject(p.id)}
              title="Open project"
            >
              <span className="font-medium text-blue-700 hover:underline">{p.name}</span>
              {p.description && <span className="ml-2 text-sm text-gray-500">{p.description}</span>}
              <span className="ml-2 text-xs text-gray-400">
                {new Date(p.createdAt).toLocaleDateString()}
              </span>
            </button>
            <button
              className="text-sm text-gray-500 hover:text-gray-800"
              onClick={() => {
                const next = prompt("Rename project", p.name);
                if (next?.trim() && next !== p.name) rename.mutate({ id: p.id, name: next.trim() });
              }}
            >
              Rename
            </button>
            <button
              className="text-sm text-red-500 hover:text-red-700"
              onClick={() => {
                if (confirm(`Delete project "${p.name}" and all its documents?`))
                  remove.mutate(p.id);
              }}
            >
              Delete
            </button>
          </li>
        ))}
        {projects.data?.length === 0 && (
          <li className="p-4 text-sm text-gray-500">No projects yet — create one above.</li>
        )}
      </ul>
      {projects.isError && (
        <p className="mt-2 text-sm text-red-600">{(projects.error as Error).message}</p>
      )}
    </div>
  );
}
