import { api } from "./api";

/**
 * Direct-to-storage multipart upload (large-file hard rules): bytes go
 * straight from the browser to MinIO/Spaces via presigned part URLs; the API
 * never proxies file content.
 *
 * Resumable: the uploadId is remembered per file fingerprint in localStorage;
 * on retry we list already-uploaded parts and skip them. Completion is
 * server-side, so ETags never need to be read cross-origin.
 */

const URL_BATCH = 5;
const PART_RETRIES = 3;

interface PendingUpload {
  documentId: string;
  uploadId: string;
  partSize: number;
}

const fingerprint = (projectId: string, file: File) =>
  `cdip-upload:${projectId}:${file.name}:${file.size}:${file.lastModified}`;

export async function uploadPdf(
  projectId: string,
  file: File,
  onProgress: (fraction: number) => void,
): Promise<string> {
  const storageKey = fingerprint(projectId, file);
  let pending: PendingUpload | null = null;
  const stored = localStorage.getItem(storageKey);
  let donePartNumbers = new Set<number>();

  if (stored) {
    try {
      const candidate: PendingUpload = JSON.parse(stored);
      const { parts } = await api.uploadedParts(projectId, candidate.documentId, candidate.uploadId);
      donePartNumbers = new Set(parts.map((p) => p.partNumber));
      pending = candidate;
    } catch {
      localStorage.removeItem(storageKey); // expired/aborted upload — start over
    }
  }

  if (!pending) {
    const init = await api.initiateUpload(projectId, { filename: file.name, size: file.size });
    pending = { documentId: init.documentId, uploadId: init.uploadId, partSize: init.partSize };
    localStorage.setItem(storageKey, JSON.stringify(pending));
  }

  const { documentId, uploadId, partSize } = pending;
  const partCount = Math.max(1, Math.ceil(file.size / partSize));
  const todo = Array.from({ length: partCount }, (_, i) => i + 1).filter(
    (n) => !donePartNumbers.has(n),
  );
  let uploadedBytes = (partCount - todo.length) * partSize;

  for (let i = 0; i < todo.length; i += URL_BATCH) {
    const batch = todo.slice(i, i + URL_BATCH);
    const { urls } = await api.partUrls(projectId, documentId, uploadId, batch);
    await Promise.all(
      batch.map(async (partNumber) => {
        const start = (partNumber - 1) * partSize;
        const blob = file.slice(start, Math.min(start + partSize, file.size));
        await putWithRetry(urls[partNumber]!, blob);
        uploadedBytes += blob.size;
        onProgress(Math.min(uploadedBytes / file.size, 1));
      }),
    );
  }

  await api.completeUpload(projectId, documentId, uploadId);
  localStorage.removeItem(storageKey);
  onProgress(1);
  return documentId;
}

async function putWithRetry(url: string, blob: Blob): Promise<void> {
  let lastError: unknown;
  for (let attempt = 1; attempt <= PART_RETRIES; attempt++) {
    try {
      const res = await fetch(url, { method: "PUT", body: blob });
      if (res.ok) return;
      lastError = new Error(`part upload failed: HTTP ${res.status}`);
    } catch (err) {
      lastError = err;
    }
    await new Promise((r) => setTimeout(r, 1000 * 2 ** (attempt - 1)));
  }
  throw lastError;
}
