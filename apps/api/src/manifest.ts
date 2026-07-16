/**
 * Virtual merge (FR-6): PDFs are never physically combined. This module is the
 * canonical page-manifest logic mapping each (document, page) to a continuous
 * combined page number across the whole project.
 *
 * Ordering rule (must match the worker's recompute in workers/src/db.py):
 * documents sorted by (createdAt, id), pages 1..N within each document.
 */

export interface ManifestDocument {
  id: string;
  filename: string;
  pages: number;
  createdAt: Date;
}

export interface ManifestEntry {
  documentId: string;
  filename: string;
  pageNumber: number;
  combinedPageNumber: number;
}

export function orderDocuments<T extends { id: string; createdAt: Date }>(docs: T[]): T[] {
  return [...docs].sort((a, b) => {
    const dt = a.createdAt.getTime() - b.createdAt.getTime();
    if (dt !== 0) return dt;
    return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
  });
}

export function buildPageManifest(docs: ManifestDocument[]): ManifestEntry[] {
  const entries: ManifestEntry[] = [];
  let offset = 0;
  for (const doc of orderDocuments(docs)) {
    for (let page = 1; page <= doc.pages; page++) {
      entries.push({
        documentId: doc.id,
        filename: doc.filename,
        pageNumber: page,
        combinedPageNumber: offset + page,
      });
    }
    offset += doc.pages;
  }
  return entries;
}

/** Resolve a combined page number back to its (document, page). */
export function findManifestEntry(
  docs: ManifestDocument[],
  combinedPageNumber: number,
): ManifestEntry | undefined {
  if (!Number.isInteger(combinedPageNumber) || combinedPageNumber < 1) return undefined;
  let offset = 0;
  for (const doc of orderDocuments(docs)) {
    if (combinedPageNumber <= offset + doc.pages) {
      return {
        documentId: doc.id,
        filename: doc.filename,
        pageNumber: combinedPageNumber - offset,
        combinedPageNumber,
      };
    }
    offset += doc.pages;
  }
  return undefined;
}
