/**
 * Which object store this deployment writes to.
 *
 * `STORAGE_BACKEND=local|spaces` is ONE word that swaps the whole storage
 * layer: `spaces` is DigitalOcean Spaces, `local` is the MinIO container on
 * the same machine, writing to a folder on that machine's disk. Both sets of
 * credentials live in the env file at the same time — that is what makes it a
 * switch rather than a rewrite. Flip the word, restart, done.
 *
 * WHAT THE SWITCH DOES NOT DO IS MOVE ANYTHING. Object keys are recorded in
 * Postgres (documents.spacesKey), and the two backends are separate stores, so
 * a project uploaded under one backend has no bytes under the other: its pages
 * fail to render and re-processing fails to download. Switch on an empty
 * system, or copy the bucket across first (rclone/mc mirror) — which is why
 * this is deliberately a restart-time setting and not a button in the UI.
 *
 * This module is the RULE and nothing else — no .env loading, no singleton —
 * so a test (and one day a config endpoint) can ask what a given environment
 * would produce without that answer depending on the ambient one. The
 * resolved configuration for this process is storage.ts.
 *
 * MIRRORED IN PYTHON: workers/src/storage_config.py. The API writes the
 * uploaded PDF and the worker reads it back, so the two must resolve to the
 * same bucket on the same host. Both test suites read
 * packages/shared/fixtures/storage-backend.json, so changing the rule in one
 * language fails the other's tests.
 */

export type StorageBackend = "local" | "spaces";

export interface StorageConfig {
  backend: StorageBackend;
  /** Endpoint the SERVER talks to: a container name on a compose network, or
   * the region host in production. */
  endpoint: string;
  /** Endpoint a BROWSER is sent to. It differs whenever the server reaches
   * storage by a name only the server can resolve — `http://minio:9000` is
   * the whole storage layer for the API and nothing at all for a laptop
   * across the office. Presigned URLs are signed against the host they will
   * be requested on, so this is not cosmetic: sign the wrong host and the
   * upload fails with SignatureDoesNotMatch. */
  publicEndpoint: string;
  bucket: string;
  region: string;
  key: string;
  secret: string;
}

/** Defaults chosen so `STORAGE_BACKEND=local` needs no other variable at all.
 * The endpoint is localhost, not `minio`, because that is the value that works
 * for an API started outside Docker (`npm run dev:api`); the compose files
 * override it with the container name. */
const LOCAL_DEFAULTS = {
  endpoint: "http://localhost:9000",
  bucket: "cdip-local",
  region: "us-east-1",
  key: "minioadmin",
  secret: "minioadmin",
} as const;

const SPACES_DEFAULTS = { region: "us-east-1" } as const;

export type EnvSource = Record<string, string | undefined>;

/** Trimmed, with the empty string treated as absent — a hand-edited .env
 * carries `FOO=` far more often than it carries an intentional empty value. */
function read(env: EnvSource, name: string): string | undefined {
  const raw = env[name];
  if (raw === undefined) return undefined;
  const trimmed = raw.trim();
  return trimmed === "" ? undefined : trimmed;
}

function require_(env: EnvSource, name: string, backend: StorageBackend): string {
  const value = read(env, name);
  if (value === undefined) {
    throw new Error(
      `${name} is required when STORAGE_BACKEND=${backend}. ` +
        `Set it in .env, or switch to STORAGE_BACKEND=local to use the MinIO ` +
        `container on this machine instead.`,
    );
  }
  return value;
}

/** The four values a Spaces deployment cannot work without. Region and ACL are
 * deliberately excluded: they are modifiers with defaults, and a stray
 * SPACES_REGION left in a file should not be read as "this is a Spaces
 * deployment" when nothing else about it is. */
const SPACES_REQUIRED = [
  "SPACES_ENDPOINT",
  "SPACES_BUCKET",
  "SPACES_KEY",
  "SPACES_SECRET",
] as const;

/**
 * An unset STORAGE_BACKEND means WHICHEVER SET YOU ACTUALLY FILLED IN.
 *
 * That keeps every .env written before this switch existed on the backend it
 * is already using, and leaves a bare checkout runnable against the MinIO
 * container with no configuration at all — which is how the worker has always
 * started. It is not a silent fallback either way: three of the four Spaces
 * variables still resolves to `spaces` and then fails naming the fourth,
 * rather than quietly writing production drawings to a local disk.
 */
function defaultBackend(env: EnvSource): StorageBackend {
  return SPACES_REQUIRED.some((name) => read(env, name) !== undefined) ? "spaces" : "local";
}

export function resolveStorage(env: EnvSource): StorageConfig {
  const raw = read(env, "STORAGE_BACKEND")?.toLowerCase() ?? defaultBackend(env);
  if (raw !== "local" && raw !== "spaces") {
    throw new Error(`STORAGE_BACKEND must be "local" or "spaces", got "${raw}".`);
  }
  const backend: StorageBackend = raw;

  if (backend === "local") {
    const endpoint = read(env, "LOCAL_S3_ENDPOINT") ?? LOCAL_DEFAULTS.endpoint;
    return {
      backend,
      endpoint,
      publicEndpoint: read(env, "LOCAL_S3_PUBLIC_ENDPOINT") ?? endpoint,
      bucket: read(env, "LOCAL_S3_BUCKET") ?? LOCAL_DEFAULTS.bucket,
      region: read(env, "LOCAL_S3_REGION") ?? LOCAL_DEFAULTS.region,
      key: read(env, "LOCAL_S3_KEY") ?? LOCAL_DEFAULTS.key,
      secret: read(env, "LOCAL_S3_SECRET") ?? LOCAL_DEFAULTS.secret,
    };
  }

  const endpoint = require_(env, "SPACES_ENDPOINT", backend);
  return {
    backend,
    endpoint,
    publicEndpoint: read(env, "SPACES_PUBLIC_ENDPOINT") ?? endpoint,
    bucket: require_(env, "SPACES_BUCKET", backend),
    region: read(env, "SPACES_REGION") ?? SPACES_DEFAULTS.region,
    key: require_(env, "SPACES_KEY", backend),
    secret: require_(env, "SPACES_SECRET", backend),
  };
}
