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
  // Not needed to boot the scaffold; required once AI features land.
  ANTHROPIC_API_KEY: z.string().optional(),
  VOYAGE_API_KEY: z.string().optional(),
});

export const env = envSchema.parse(process.env);
