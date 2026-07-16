import { env } from "./env.js";

const COLLECTION = process.env.QDRANT_COLLECTION ?? "chunks";

/**
 * Vector search filtered by project (multitenant payload partitioning) and
 * optionally by portion (FR-22). Returns chunk IDs best-first; point ID ==
 * chunk ID, and PostgreSQL remains the source of truth for the references.
 */
export async function searchChunks(
  projectId: string,
  vector: number[],
  options: { portionId?: string; limit?: number } = {},
): Promise<string[]> {
  const must: object[] = [{ key: "project_id", match: { value: projectId } }];
  if (options.portionId) must.push({ key: "portion", match: { value: options.portionId } });

  const res = await fetch(`${env.QDRANT_URL}/collections/${COLLECTION}/points/search`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      vector,
      limit: options.limit ?? 18,
      filter: { must },
      with_payload: false,
    }),
  });
  if (res.status === 404) return []; // collection not created yet (nothing embedded)
  if (!res.ok) throw new Error(`Qdrant search failed: ${res.status} ${await res.text()}`);
  const data = (await res.json()) as { result: { id: string }[] };
  return data.result.map((p) => String(p.id));
}
