const VOYAGE_BASE_URL = process.env.VOYAGE_BASE_URL ?? "https://api.voyageai.com";
const VOYAGE_MODEL = process.env.VOYAGE_MODEL ?? "voyage-3";

export const voyageAvailable = () => Boolean(process.env.VOYAGE_API_KEY);

/** Embed a chat question (input_type "query"; the worker embeds documents). */
export async function embedQuery(text: string): Promise<number[]> {
  const res = await fetch(`${VOYAGE_BASE_URL}/v1/embeddings`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${process.env.VOYAGE_API_KEY}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ input: [text], model: VOYAGE_MODEL, input_type: "query" }),
  });
  if (!res.ok) throw new Error(`Voyage embeddings failed: ${res.status} ${await res.text()}`);
  const data = (await res.json()) as { data: { embedding: number[] }[] };
  return data.data[0]!.embedding;
}
