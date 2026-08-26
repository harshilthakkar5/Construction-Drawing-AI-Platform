import { recordUsage } from "./usage.js";

/**
 * Provider-neutral query embedding: Voyage, Cohere or Gemini, chosen by
 * EMBEDDING_PROVIDER.
 *
 * The mirror of workers/src/embedllm.py, and it must stay one: the worker
 * embeds the documents and this process embeds the question that is compared
 * against them. A cosine distance between two different embedding spaces is
 * noise, so a provider or model set here that disagrees with the worker's does
 * not degrade retrieval — it destroys it, silently and with no error to read.
 * Both sides read the same env vars for exactly that reason.
 *
 *   EMBEDDING_PROVIDER = voyage (default) | cohere | gemini
 *   EMBEDDING_MODEL      overrides the per-provider default
 *   EMBEDDING_DIM        vector width — must match the Qdrant collection
 *
 * One question at a time is all this side ever embeds, so there is no
 * batching here; the batching and caching that keep indexing affordable live
 * on the worker, where the volume is.
 */
export type EmbeddingProvider = "voyage" | "cohere" | "gemini";

const PROVIDERS: readonly EmbeddingProvider[] = ["voyage", "cohere", "gemini"] as const;

/** Mirrors embedllm.DEFAULT_MODELS — keep the two in step. */
export const DEFAULT_EMBEDDING_MODELS: Record<EmbeddingProvider, string> = {
  voyage: "voyage-3",
  cohere: "embed-v4.0",
  gemini: "gemini-embedding-001",
};

const KEY_ENV: Record<EmbeddingProvider, string> = {
  voyage: "VOYAGE_API_KEY",
  cohere: "COHERE_API_KEY",
  gemini: "GEMINI_API_KEY",
};

const DEFAULT_BASE_URLS: Record<EmbeddingProvider, string> = {
  voyage: "https://api.voyageai.com",
  cohere: "https://api.cohere.com",
  gemini: "https://generativelanguage.googleapis.com",
};

/** The query half of each provider's document/query distinction. */
const QUERY_INPUT_TYPE: Record<EmbeddingProvider, string> = {
  voyage: "query",
  cohere: "search_query",
  gemini: "RETRIEVAL_QUERY",
};

/** Read per call, never captured at import, so config changes take effect. */
export function embeddingProvider(): EmbeddingProvider {
  const name = (process.env.EMBEDDING_PROVIDER ?? "voyage").trim().toLowerCase();
  return (PROVIDERS as readonly string[]).includes(name)
    ? (name as EmbeddingProvider)
    : "voyage";
}

export function embeddingModel(): string {
  return process.env.EMBEDDING_MODEL || DEFAULT_EMBEDDING_MODELS[embeddingProvider()];
}

export function embeddingDimensions(): number {
  const raw = Number(process.env.EMBEDDING_DIM);
  return Number.isFinite(raw) && raw > 0 ? Math.floor(raw) : 1024;
}

export function embeddingKeyEnv(): string {
  return KEY_ENV[embeddingProvider()];
}

export const embeddingsAvailable = () => Boolean(process.env[embeddingKeyEnv()]);

/**
 * Whether to ask for a specific vector width. Mirrors embedllm.sends_dimension:
 * voyage-3 rejects the parameter outright, the Cohere and Gemini models take
 * it on every current version.
 */
function sendsDimension(provider: EmbeddingProvider): boolean {
  const override = process.env.EMBED_SEND_DIMENSION;
  if (override) return !["false", "0", "no", "off"].includes(override.trim().toLowerCase());
  return provider !== "voyage";
}

function baseUrl(provider: EmbeddingProvider): string {
  return process.env[`${provider.toUpperCase()}_BASE_URL`] || DEFAULT_BASE_URLS[provider];
}

interface ProviderCall {
  url: string;
  headers: Record<string, string>;
  body: Record<string, unknown>;
  /** Pulls the vector and the provider's own token count out of the reply. */
  read: (json: unknown) => { vector: number[]; tokens: number };
}

function buildCall(provider: EmbeddingProvider, text: string, model: string): ProviderCall {
  const key = process.env[KEY_ENV[provider]] ?? "";
  const inputType = QUERY_INPUT_TYPE[provider];
  const dimensions = embeddingDimensions();

  if (provider === "cohere") {
    return {
      url: `${baseUrl(provider)}/v2/embed`,
      headers: { Authorization: `Bearer ${key}` },
      body: {
        texts: [text],
        model,
        input_type: inputType,
        embedding_types: ["float"],
        truncate: "END",
        ...(sendsDimension(provider) ? { output_dimension: dimensions } : {}),
      },
      read: (json) => {
        const data = json as {
          embeddings: { float?: number[][] } | number[][];
          meta?: { billed_units?: { input_tokens?: number } };
        };
        const floats = Array.isArray(data.embeddings)
          ? data.embeddings
          : (data.embeddings.float ?? []);
        return {
          vector: floats[0] ?? [],
          tokens: data.meta?.billed_units?.input_tokens ?? 0,
        };
      },
    };
  }

  if (provider === "gemini") {
    return {
      url: `${baseUrl(provider)}/v1beta/models/${model}:embedContent`,
      headers: { "x-goog-api-key": key },
      body: {
        model: `models/${model}`,
        content: { parts: [{ text }] },
        taskType: inputType,
        ...(sendsDimension(provider) ? { outputDimensionality: dimensions } : {}),
      },
      // The Developer API reports no token usage for embeddings; 0 here means
      // recordUsage falls back to the chars/4 estimate below, the same one the
      // worker records for this provider.
      read: (json) => ({
        vector: (json as { embedding: { values?: number[] } }).embedding?.values ?? [],
        tokens: 0,
      }),
    };
  }

  return {
    url: `${baseUrl(provider)}/v1/embeddings`,
    headers: { Authorization: `Bearer ${key}` },
    body: {
      input: [text],
      model,
      input_type: inputType,
      ...(sendsDimension(provider) ? { output_dimension: dimensions } : {}),
    },
    read: (json) => {
      const data = json as {
        data: { embedding: number[] }[];
        usage?: { total_tokens?: number };
      };
      return { vector: data.data[0]?.embedding ?? [], tokens: data.usage?.total_tokens ?? 0 };
    },
  };
}

/**
 * L2-normalize. Gemini returns unit vectors only at its native 3072 width;
 * every Matryoshka truncation below that comes back unnormalized, and the
 * worker normalizes the document side, so the query side must match.
 */
function normalize(vector: number[]): number[] {
  const norm = Math.sqrt(vector.reduce((sum, value) => sum + value * value, 0));
  return norm ? vector.map((value) => value / norm) : vector;
}

/** Embed a chat question. The worker embeds the documents. */
export async function embedQuery(text: string, projectId?: string): Promise<number[]> {
  const provider = embeddingProvider();
  const model = embeddingModel();
  const call = buildCall(provider, text, model);

  const res = await fetch(call.url, {
    method: "POST",
    headers: { ...call.headers, "Content-Type": "application/json" },
    body: JSON.stringify(call.body),
  });
  if (!res.ok) {
    throw new Error(`${provider} embeddings failed: ${res.status} ${await res.text()}`);
  }

  const { vector, tokens } = call.read(await res.json());
  if (vector.length === 0) {
    throw new Error(`${provider} returned no embedding for the question`);
  }

  await recordUsage(projectId ?? null, "embedding", model, {
    // ~4 characters per token when the provider reports nothing of its own.
    inputTokens: tokens || Math.max(1, Math.floor(text.length / 4)),
  });

  return provider === "gemini" ? normalize(vector) : vector;
}
