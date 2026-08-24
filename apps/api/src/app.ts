import cors from "cors";
import express, { type NextFunction, type Request, type Response } from "express";
import { ZodError } from "zod";
import { Prisma } from "@prisma/client";
import { requireAuth, requireProjectMember } from "./auth.js";
import { missingPrismaModels, prisma, PRISMA_STALE_MESSAGE } from "./db.js";
import { env } from "./env.js";
import { authLimiter, floodLimiter, generalLimiter } from "./rateLimit.js";
import { redis } from "./redis.js";
import { httpMetricsMiddleware } from "./telemetry.js";
import { authRouter } from "./routes/auth.js";
import { chatRouter } from "./routes/chat.js";
import { chunksRouter } from "./routes/chunks.js";
import { dashboardRouter } from "./routes/dashboard.js";
import { documentsRouter } from "./routes/documents.js";
import { pagesRouter } from "./routes/pages.js";
import { portionsRouter } from "./routes/portions.js";
import { projectsRouter } from "./routes/projects.js";
import { queuesRouter } from "./routes/queues.js";
import { regionRouter } from "./routes/region.js";
import { summariesRouter } from "./routes/summaries.js";
import { supportRouter } from "./routes/support.js";

/** 503 + the remedy when the generated Prisma client predates a model. */
const requireGeneratedModels = (_req: Request, res: Response, next: NextFunction) => {
  const missing = missingPrismaModels();
  if (missing.length === 0) return next();
  res.status(503).json({
    error: "prisma client out of date",
    missingModels: missing,
    fix: PRISMA_STALE_MESSAGE,
  });
};

export function createApp() {
  const app = express();

  app.use(cors());
  // Behind a proxy (App Platform, nginx) req.ip is the load balancer without
  // this, which would collapse every client onto one rate-limit bucket.
  app.set("trust proxy", Number(process.env.TRUST_PROXY_HOPS ?? 1));
  // Sanitized inputs only ever carry JSON metadata — file bytes go straight to
  // object storage — so a tight body limit is safe.
  app.use(express.json({ limit: "1mb" }));
  app.use(httpMetricsMiddleware);

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

  // Strictest tier, by IP and counted on failures only: these are the
  // endpoints an attacker can reach without a session.
  app.use("/auth", authLimiter, authRouter);

  // Everything below requires a session (project-level RBAC on top of it).
  // BEFORE requireAuth: a flood of invalid tokens is rejected by requireAuth
  // and would never reach a limiter mounted after it. Measured at ~2300 req/s
  // of 401s against a single process before this was added — cheap per request,
  // but free to send and unbounded.
  app.use(floodLimiter);
  app.use(requireAuth);
  // After requireAuth, so the everyday limit keys on the USER. Keyed on IP it
  // would give a whole office one shared allowance.
  app.use(generalLimiter);
  app.use("/projects/:projectId", requireProjectMember);

  // Account-level surfaces (not project-scoped). Both read models that only
  // exist in a freshly generated client — answer with the fix rather than a
  // TypeError from deep inside the handler.
  app.use("/dashboard", requireGeneratedModels, dashboardRouter);
  app.use("/support", requireGeneratedModels, supportRouter);
  // Instance-wide queue operations (inspect / retry / purge stuck jobs).
  app.use("/queues", queuesRouter);
  app.use("/projects", projectsRouter);
  app.use("/projects/:projectId/documents", documentsRouter);
  app.use("/projects/:projectId/region", requireGeneratedModels, regionRouter);
  // chatLimiter and summaryLimiter are applied to the individual POST routes
  // inside these routers, not here: each also serves GETs (listing portions,
  // reading summaries, quoting a cost) that must stay freely browsable.
  app.use("/projects/:projectId/portions", portionsRouter);
  app.use("/projects/:projectId/chat", chatRouter);
  app.use("/projects/:projectId/summaries", summariesRouter);
  app.use("/projects/:projectId/chunks", chunksRouter);
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
