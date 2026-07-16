import cors from "cors";
import express from "express";
import { prisma } from "./db.js";
import { env } from "./env.js";
import { redis } from "./redis.js";

export function createApp() {
  const app = express();

  app.use(cors());
  app.use(express.json());

  app.get("/health", async (_req, res) => {
    const checks: Record<string, "ok" | "error"> = {
      postgres: "error",
      redis: "error",
      qdrant: "error",
    };

    await Promise.all([
      prisma.$queryRaw`SELECT 1`
        .then(() => (checks.postgres = "ok"))
        .catch(() => {}),
      redis
        .ping()
        .then(() => (checks.redis = "ok"))
        .catch(() => {}),
      fetch(`${env.QDRANT_URL}/readyz`)
        .then((r) => r.ok && (checks.qdrant = "ok"))
        .catch(() => {}),
    ]);

    const healthy = Object.values(checks).every((s) => s === "ok");
    res.status(healthy ? 200 : 503).json({ status: healthy ? "ok" : "degraded", checks });
  });

  return app;
}
