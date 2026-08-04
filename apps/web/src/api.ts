import type {
  AuthResponseDto,
  ChatResponseDto,
  ChunkLocationDto,
  DashboardDto,
  DocumentDto,
  InitiateUploadResponse,
  ManifestEntryDto,
  PortionDto,
  ProjectDto,
  SummaryDto,
  SummaryStatusDto,
  SupportTicketDto,
  UserDto,
} from "@cdip/shared";

export const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:4000";

const TOKEN_KEY = "cdip-token";

export const authToken = {
  get: () => localStorage.getItem(TOKEN_KEY),
  set: (token: string) => localStorage.setItem(TOKEN_KEY, token),
  clear: () => localStorage.removeItem(TOKEN_KEY),
};

/** Thrown on 401 so the app can drop back to the login screen. */
export class UnauthorizedError extends Error {}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const token = authToken.get();
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: {
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...init?.headers,
    },
  });
  if (res.status === 401) {
    authToken.clear();
    throw new UnauthorizedError("session expired — sign in again");
  }
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    // The API answers errors as JSON; surface its own wording (and the `fix`
    // hint it sends for recoverable setup problems) instead of a raw dump.
    let detail = body;
    try {
      const parsed = JSON.parse(body) as { error?: string; fix?: string };
      detail = [parsed.error, parsed.fix].filter(Boolean).join(" — ") || body;
    } catch {
      // not JSON — keep the raw text
    }
    throw new Error(`${init?.method ?? "GET"} ${path} → ${res.status} ${detail}`);
  }
  return res.status === 204 ? (undefined as T) : res.json();
}

/** Media <img> elements can't send Authorization headers; these GET endpoints
 * accept the session token as a query parameter and redirect to a presigned
 * URL (presigned-URL-only media access). */
const withToken = (url: string) => {
  const token = authToken.get();
  return token ? `${url}${url.includes("?") ? "&" : "?"}token=${encodeURIComponent(token)}` : url;
};

export const api = {
  register: (body: {
    email: string;
    password: string;
    firstName: string;
    lastName?: string;
    company?: string;
  }) => request<AuthResponseDto>("/auth/register", { method: "POST", body: JSON.stringify(body) }),
  login: (body: { email: string; password: string }) =>
    request<AuthResponseDto>("/auth/login", { method: "POST", body: JSON.stringify(body) }),
  logout: () => request<void>("/auth/logout", { method: "POST", body: JSON.stringify({}) }),
  me: () => request<UserDto>("/auth/me"),
  /** Account page: update the signed-in user's own profile. */
  updateProfile: (body: {
    firstName?: string;
    lastName?: string;
    company?: string | null;
    email?: string;
  }) => request<UserDto>("/auth/me", { method: "PATCH", body: JSON.stringify(body) }),
  changePassword: (body: { currentPassword: string; newPassword: string }) =>
    request<void>("/auth/password", { method: "POST", body: JSON.stringify(body) }),

  /** Everything the dashboard shows, in one call. `days` sets the activity
   * window the API aggregates (7 / 14 / 30 / 90). */
  dashboard: (days?: number) =>
    request<DashboardDto>(`/dashboard${days ? `?days=${days}` : ""}`),

  submitSupport: (body: { name: string; email: string; subject: string; message: string }) =>
    request<{ id: string; createdAt: string }>("/support", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  listSupportTickets: () => request<SupportTicketDto[]>("/support"),

  listProjects: () => request<ProjectDto[]>("/projects"),
  createProject: (body: { name: string; description?: string }) =>
    request<ProjectDto>("/projects", { method: "POST", body: JSON.stringify(body) }),
  updateProject: (id: string, body: { name?: string; description?: string | null }) =>
    request<ProjectDto>(`/projects/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteProject: (id: string) => request<void>(`/projects/${id}`, { method: "DELETE" }),

  listDocuments: (projectId: string) =>
    request<DocumentDto[]>(`/projects/${projectId}/documents`),
  initiateUpload: (
    projectId: string,
    body: { filename: string; size: number; replacesDocumentId?: string },
  ) =>
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

  /** Re-run processing for a stuck/failed document (resumes where it stopped). */
  reprocessDocument: (projectId: string, documentId: string) =>
    request<{ documentId: string; status: string }>(
      `/projects/${projectId}/documents/${documentId}/reprocess`,
      { method: "POST", body: JSON.stringify({}) },
    ),
  /** Delete a document everywhere (DB, storage, vectors). */
  deleteDocument: (projectId: string, documentId: string) =>
    request<void>(`/projects/${projectId}/documents/${documentId}`, { method: "DELETE" }),

  listPortions: (projectId: string) => request<PortionDto[]>(`/projects/${projectId}/portions`),

  listSummaries: (projectId: string) => request<SummaryDto[]>(`/projects/${projectId}/summaries`),

  /** Why is there no summary? Reports what each tier produced, plus a hint. */
  summaryStatus: (projectId: string) =>
    request<SummaryStatusDto>(`/projects/${projectId}/summaries/status`),

  /** Re-run the summarize job without re-uploading anything. */
  rebuildSummaries: (projectId: string) =>
    request<{ queued: boolean; jobId: string }>(`/projects/${projectId}/summaries/rebuild`, {
      method: "POST",
    }),

  /** FR-19: resolve a summary item's chunk to page + bbox for highlighting. */
  chunkLocation: (projectId: string, chunkId: string) =>
    request<ChunkLocationDto>(`/projects/${projectId}/chunks/${chunkId}/location`),

  ask: (projectId: string, body: { question: string; sessionId?: string; portionId?: string }) =>
    request<ChatResponseDto>(`/projects/${projectId}/chat`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  manifest: (projectId: string) => request<ManifestEntryDto[]>(`/projects/${projectId}/manifest`),
  documentFileUrl: (projectId: string, documentId: string) =>
    withToken(`${API_URL}/projects/${projectId}/documents/${documentId}/file`),
  pageThumbUrl: (projectId: string, combined: number) =>
    withToken(`${API_URL}/projects/${projectId}/pages/${combined}/thumb`),
  /** Full-resolution rendered page PNG (the viewer's main display). */
  pageImageUrl: (projectId: string, combined: number) =>
    withToken(`${API_URL}/projects/${projectId}/pages/${combined}/image`),
};
