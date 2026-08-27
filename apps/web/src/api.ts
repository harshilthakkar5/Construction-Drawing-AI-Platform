import type {
  AuthResponseDto,
  ChatHistoryWindow,
  ChatMessageDto,
  ChatResponseDto,
  ChatSessionDto,
  ChunkLocationDto,
  DashboardDto,
  DocumentDto,
  InitiateUploadResponse,
  ManifestEntryDto,
  PortionDto,
  ProjectDto,
  RegionBox,
  RegionPreviewDto,
  SheetRegionDto,
  SummaryDto,
  SummaryEstimateDto,
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

/**
 * Endpoints where a 401 means "those credentials are wrong", NOT "your session
 * ended". Treating them the same told someone who mistyped their password that
 * their session had expired, and cleared the token of whoever was already
 * signed in in another tab.
 */
const CREDENTIAL_PATHS = [
  "/auth/login",
  "/auth/register",
  "/auth/forgot-password",
  "/auth/reset-password",
];

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
  if (res.status === 401 && !CREDENTIAL_PATHS.some((p) => path.startsWith(p))) {
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

  /** Ask for a reset link. Always succeeds — the API never reveals whether the
   * address is registered, so the UI must not either. */
  forgotPassword: (email: string) =>
    request<{ sent: boolean; message: string }>("/auth/forgot-password", {
      method: "POST",
      body: JSON.stringify({ email }),
    }),

  /** Is a reset link still good? Checked before showing the form. */
  checkResetToken: (token: string) =>
    request<{ valid: boolean }>("/auth/reset-password/check", {
      method: "POST",
      body: JSON.stringify({ token }),
    }),

  /** Redeem a reset link; signs the user straight in. */
  resetPassword: (token: string, password: string) =>
    request<AuthResponseDto>("/auth/reset-password", {
      method: "POST",
      body: JSON.stringify({ token, password }),
    }),
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
  createProject: (body: { name: string; description?: string; roles?: string[] }) =>
    request<ProjectDto>("/projects", { method: "POST", body: JSON.stringify(body) }),
  updateProject: (
    id: string,
    body: { name?: string; description?: string | null; roles?: string[] },
  ) =>
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

  // --- Title-block region: one box per project, applied to every page ---

  /** null when the user hasn't drawn a box yet (the API answers 404). */
  getRegion: async (projectId: string) => {
    try {
      return await request<SheetRegionDto>(`/projects/${projectId}/region`);
    } catch (err) {
      if (err instanceof Error && err.message.includes("→ 404")) return null;
      throw err;
    }
  },

  /** Save the box and kick off a whole-project re-scrape. */
  saveRegion: (
    projectId: string,
    body: RegionBox & { sampleDocumentId?: string; samplePageNumber?: number },
  ) =>
    request<SheetRegionDto & { jobId: string }>(`/projects/${projectId}/region`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),

  /** Re-run the scrape without moving the box. */
  rescrapeRegion: (projectId: string) =>
    request<SheetRegionDto & { jobId: string }>(`/projects/${projectId}/region/rescrape`, {
      method: "POST",
    }),

  deleteRegion: (projectId: string) =>
    request<void>(`/projects/${projectId}/region`, { method: "DELETE" }),

  /** Dry run on a few sample pages; returns the job id to poll. */
  previewRegion: (projectId: string, body: RegionBox & { sampleSize?: number }) =>
    request<{ jobId: string }>(`/projects/${projectId}/region/preview`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  /** null while the job is still running; throws if the job failed, so the
   * caller shows the error instead of polling forever. */
  regionPreview: async (projectId: string, jobId: string) => {
    const result = await request<
      RegionPreviewDto | { status: "pending" } | { status: "failed"; error: string }
    >(`/projects/${projectId}/region/preview/${jobId}`);
    if ("status" in result) {
      if (result.status === "failed") throw new Error(result.error);
      return null;
    }
    return result;
  },

  listPortions: (projectId: string) => request<PortionDto[]>(`/projects/${projectId}/portions`),

  /** What will this discipline's summary cost? Backs the confirm dialog. */
  summaryEstimate: (projectId: string, portionId: string) =>
    request<SummaryEstimateDto>(
      `/projects/${projectId}/portions/${portionId}/summarize/estimate`,
    ),

  /** What will the project rollup cost? */
  projectSummaryEstimate: (projectId: string) =>
    request<SummaryEstimateDto>(`/projects/${projectId}/summaries/project/estimate`),

  /** FR-10/12 on demand: summarize ONE discipline, because the user asked. */
  summarizePortion: (projectId: string, portionId: string) =>
    request<{ queued: boolean; jobId: string }>(
      `/projects/${projectId}/portions/${portionId}/summarize`,
      { method: "POST" },
    ),

  /** Roll the existing per-discipline summaries up into the project summary. */
  generateProjectSummary: (projectId: string) =>
    request<{ queued: boolean; jobId: string; portionsUsed: number }>(
      `/projects/${projectId}/summaries/project`,
      { method: "POST" },
    ),

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

  /** Past conversations for the history picker, newest activity first. */
  chatSessions: (
    projectId: string,
    params: { window: ChatHistoryWindow; from?: string; to?: string },
  ) => {
    const query = new URLSearchParams({ window: params.window });
    if (params.from) query.set("from", params.from);
    if (params.to) query.set("to", params.to);
    return request<ChatSessionDto[]>(`/projects/${projectId}/chat/sessions?${query}`);
  },

  /** Replay one past conversation (FR-23). */
  chatMessages: (projectId: string, sessionId: string) =>
    request<ChatMessageDto[]>(`/projects/${projectId}/chat/${sessionId}/messages`),

  manifest: (projectId: string) => request<ManifestEntryDto[]>(`/projects/${projectId}/manifest`),
  documentFileUrl: (projectId: string, documentId: string) =>
    withToken(`${API_URL}/projects/${projectId}/documents/${documentId}/file`),
  pageThumbUrl: (projectId: string, combined: number) =>
    withToken(`${API_URL}/projects/${projectId}/pages/${combined}/thumb`),
  /** Full-resolution rendered page PNG (the viewer's main display). */
  pageImageUrl: (projectId: string, combined: number) =>
    withToken(`${API_URL}/projects/${projectId}/pages/${combined}/image`),
};
