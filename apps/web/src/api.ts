import type {
  DocumentDto,
  InitiateUploadResponse,
  ManifestEntryDto,
  ProjectDto,
} from "@cdip/shared";

export const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:4000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    headers: init?.body ? { "Content-Type": "application/json" } : undefined,
    ...init,
  });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`${init?.method ?? "GET"} ${path} → ${res.status} ${body}`);
  }
  return res.status === 204 ? (undefined as T) : res.json();
}

export const api = {
  listProjects: () => request<ProjectDto[]>("/projects"),
  createProject: (body: { name: string; description?: string }) =>
    request<ProjectDto>("/projects", { method: "POST", body: JSON.stringify(body) }),
  updateProject: (id: string, body: { name?: string; description?: string | null }) =>
    request<ProjectDto>(`/projects/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteProject: (id: string) => request<void>(`/projects/${id}`, { method: "DELETE" }),

  listDocuments: (projectId: string) =>
    request<DocumentDto[]>(`/projects/${projectId}/documents`),
  initiateUpload: (projectId: string, body: { filename: string; size: number }) =>
    request<InitiateUploadResponse & { partCount: number }>(`/projects/${projectId}/documents`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  partUrls: (projectId: string, documentId: string, uploadId: string, partNumbers: number[]) =>
    request<{ urls: Record<number, string> }>(
      `/projects/${projectId}/documents/${documentId}/upload/${encodeURIComponent(uploadId)}/part-urls`,
      { method: "POST", body: JSON.stringify({ partNumbers }) },
    ),
  uploadedParts: (projectId: string, documentId: string, uploadId: string) =>
    request<{ parts: { partNumber: number; size: number }[] }>(
      `/projects/${projectId}/documents/${documentId}/upload/${encodeURIComponent(uploadId)}/parts`,
    ),
  completeUpload: (projectId: string, documentId: string, uploadId: string) =>
    request<{ documentId: string }>(
      `/projects/${projectId}/documents/${documentId}/upload/${encodeURIComponent(uploadId)}/complete`,
      { method: "POST", body: JSON.stringify({}) },
    ),
  abortUpload: (projectId: string, documentId: string, uploadId: string) =>
    request<void>(
      `/projects/${projectId}/documents/${documentId}/upload/${encodeURIComponent(uploadId)}/abort`,
      { method: "POST", body: JSON.stringify({}) },
    ),

  manifest: (projectId: string) => request<ManifestEntryDto[]>(`/projects/${projectId}/manifest`),
  documentFileUrl: (projectId: string, documentId: string) =>
    `${API_URL}/projects/${projectId}/documents/${documentId}/file`,
  pageThumbUrl: (projectId: string, combined: number) =>
    `${API_URL}/projects/${projectId}/pages/${combined}/thumb`,
};
