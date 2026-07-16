import cors from "cors";
import express, { type NextFunction, type Request, type Response } from "express";
import { ZodError } from "zod";
import { Prisma } from "@prisma/client";
import { prisma } from "./db.js";
import { env } from "./env.js";
import { redis } from "./redis.js";
import { chatRouter } from "./routes/chat.js";
import { documentsRouter } from "./routes/documents.js";
import { pagesRouter } from "./routes/pages.js";
import { portionsRouter } from "./routes/portions.js";
import { projectsRouter } from "./routes/projects.js";

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

  app.use("/projects", projectsRouter);
  app.use("/projects/:projectId/documents", documentsRouter);
  app.use("/projects/:projectId/portions", portionsRouter);
  app.use("/projects/:projectId/chat", chatRouter);
  app.use("/projects/:projectId", pagesRouter);

  app.use((err: unknown, _req: Request, res: Response, _next: NextFunction) => {
    if (err instanceof ZodError) {
      return void res.status(400).json({ error: "validation failed", issues: err.issues });
    }
    if (err instanceof Prisma.PrismaClientKnownRequestError && err.code === "P2025") {
      return void res.status(404).json({ error: "not found" });
    }
    console.error(err);
    res.status(500).json({ error: "internal error" });
  });

  return app;
}
