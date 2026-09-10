import "dotenv/config";
import { z } from "zod";

const envSchema = z.object({
  NODE_ENV: z.enum(["development", "test", "production"]).default("development"),
  PORT: z.coerce.number().default(4000),
  DATABASE_URL: z.string().min(1),
  REDIS_URL: z.string().min(1),
  QDRANT_URL: z.string().min(1),
  /**
   * Object storage is NOT validated here, because which variables are required
   * depends on STORAGE_BACKEND: `spaces` needs the four SPACES_* values,
   * `local` needs nothing at all (it defaults to the MinIO container beside
   * this one). apps/api/src/storageConfig.ts owns that rule — and owns it
   * jointly with the worker, through a shared fixture — and throws at startup
   * naming the variable AND the backend that wants it. Declaring them required
   * here as well would make STORAGE_BACKEND=local impossible to run without
   * inventing DigitalOcean credentials to satisfy a schema.
   *
   * SPACES_ACL is the exception: it applies to whichever backend is active.
   */
  /** Canned ACL applied to uploaded objects. Leave unset (default) to keep
   * objects PRIVATE — the app serves all media via presigned URLs, so private
   * is correct and secure. Set to "public-read" ONLY if you deliberately want
   * every uploaded PDF/image/text world-readable (e.g. to serve straight off
   * the DO Spaces CDN). This bypasses the app's auth/RBAC for media. */
  SPACES_ACL: z.enum(["private", "public-read"]).optional(),
  // Not needed to boot the scaffold; required once AI features land.
  /** Canned ACL applied to uploaded objects. Leave unset (default) to keep
   * objects PRIVATE — the app serves all media via presigned URLs, so private
   * is correct and secure. Set to "public-read" ONLY if you deliberately want
   * every uploaded PDF/image/text world-readable (e.g. to serve straight off
   * the DO Spaces CDN). This bypasses the app's auth/RBAC for media. */
  // SPACES_ACL: z.enum(["private", "public-read"]).optional(), 
  ANTHROPIC_API_KEY: z.string().optional(),
  /**
   * Embeddings. Which of these three is required depends on
   * EMBEDDING_PROVIDER (voyage | cohere | gemini) — apps/api/src/embedding.ts
   * resolves it, and the chat route's 503 names the one that is missing. All
   * optional here so the app still boots with none of them configured.
   * GEMINI_API_KEY doubles as the chat/summary key when those run on Gemini.
   */
  VOYAGE_API_KEY: z.string().optional(),
  COHERE_API_KEY: z.string().optional(),
  GEMINI_API_KEY: z.string().optional(),

  /**
   * Outbound email (password resets, support tickets). Entirely optional: with
   * no SMTP_HOST the mailer logs each message — including the reset link — to
   * the server console instead of sending it, so local development works with
   * no mail server. See apps/api/src/mailer.ts.
   */
  SMTP_HOST: z.string().optional(),
  SMTP_PORT: z.coerce.number().default(587),
  /** true for implicit TLS on 465; false for STARTTLS on 587. */
  SMTP_SECURE: z
    .enum(["true", "false"])
    .default("false")
    .transform((v) => v === "true"),
  SMTP_USER: z.string().optional(),
  SMTP_PASSWORD: z.string().optional(),
  /** From header, e.g. "ArcAligned AI <no-reply@example.com>". */
  MAIL_FROM: z.string().default("ArcAligned AI <no-reply@localhost>"),
  /** Where support tickets are delivered. Unset = persisted and logged only. */
  SUPPORT_EMAIL: z.string().email().optional(),
  /** Public URL of the web app — used to build the password-reset link. */
  APP_URL: z.string().url().default("http://localhost:3000"),
});

export const env = envSchema.parse(process.env);
