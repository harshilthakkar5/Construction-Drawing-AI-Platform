import "dotenv/config";
import { z } from "zod";

const envSchema = z.object({
  NODE_ENV: z.enum(["development", "test", "production"]).default("development"),
  PORT: z.coerce.number().default(4000),
  DATABASE_URL: z.string().min(1),
  REDIS_URL: z.string().min(1),
  QDRANT_URL: z.string().min(1),
  SPACES_KEY: z.string().min(1),
  SPACES_SECRET: z.string().min(1),
  /** Region endpoint WITHOUT the bucket name, e.g.
   * https://blr1.digitaloceanspaces.com (prod) or http://localhost:9000 (MinIO). */
  SPACES_ENDPOINT: z.string().min(1),
  SPACES_BUCKET: z.string().min(1),
  /** Region slug used for request signing (e.g. blr1); any value works for MinIO. */
  SPACES_REGION: z.string().min(1).default("us-east-1"),
  /** Canned ACL applied to uploaded objects. Leave unset (default) to keep
   * objects PRIVATE — the app serves all media via presigned URLs, so private
   * is correct and secure. Set to "public-read" ONLY if you deliberately want
   * every uploaded PDF/image/text world-readable (e.g. to serve straight off
   * the DO Spaces CDN). This bypasses the app's auth/RBAC for media. */
  SPACES_ACL: z.enum(["private", "public-read"]).optional(),
  // Not needed to boot the scaffold; required once AI features land.
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
