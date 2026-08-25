import { metrics, ValueType } from "@opentelemetry/api";
import type { NextFunction, Request, Response } from "express";

/**
 * OpenTelemetry metrics (Phase 5). Instruments are created against the global
 * meter provider via @opentelemetry/api, so they are inert no-ops until
 * startTelemetry() (called from index.ts, NOT from tests) registers a real
 * MeterProvider with a Prometheus reader. Prometheus scrapes
 * http://<api>:${OTEL_METRICS_PORT ?? 9464}/metrics; Grafana dashboards live
 * in monitoring/.
 */

const meter = metrics.getMeter("cdip-api");

/** API latency (FR: OTel for API latency). */
export const httpDuration = meter.createHistogram("http_server_duration_ms", {
  description: "HTTP request duration by method/route/status",
  valueType: ValueType.DOUBLE,
});

/** Vector search latency (FR: OTel for vector search). */
export const qdrantSearchDuration = meter.createHistogram("qdrant_search_duration_ms", {
  description: "Qdrant vector search duration",
  valueType: ValueType.DOUBLE,
});

/** End-to-end chat answer latency (retrieval + Claude). */
export const chatDuration = meter.createHistogram("chat_request_duration_ms", {
  description: "RAG chat request duration",
  valueType: ValueType.DOUBLE,
});

/** Redis retrieval-cache effectiveness. */
export const retrievalCacheCounter = meter.createCounter("retrieval_cache_lookups", {
  description: "Chat retrieval cache lookups by result (hit|miss)",
});

export function httpMetricsMiddleware(req: Request, res: Response, next: NextFunction) {
  const start = process.hrtime.bigint();
  res.on("finish", () => {
    const ms = Number(process.hrtime.bigint() - start) / 1e6;
    // req.route is only set for matched routes; fall back to the base path so
    // unmatched/middleware-terminated requests don't explode label cardinality.
    const route = req.route?.path
      ? `${req.baseUrl}${req.route.path}`
      : (req.baseUrl || req.path.split("/").slice(0, 2).join("/") || "/");
    httpDuration.record(ms, {
      "http.method": req.method,
      "http.route": route,
      "http.status_code": res.statusCode,
    });
  });
  next();
}

/**
 * Queue metrics (FR: OTel for queues): BullMQ job counts observed on scrape.
 * Registered here (not at module load) because it needs live Queue handles.
 */
export function observeQueues(queues: { name: string; getJobCounts: () => Promise<Record<string, number>> }[]) {
  const gauge = meter.createObservableGauge("bullmq_queue_jobs", {
    description: "BullMQ job counts by queue and state",
  });
  gauge.addCallback(async (observable) => {
    for (const queue of queues) {
      try {
        const counts = await queue.getJobCounts();
        for (const [state, count] of Object.entries(counts)) {
          observable.observe(count ?? 0, { queue: queue.name, state });
        }
      } catch {
        // Redis briefly unavailable — skip this scrape.
      }
    }
  });
}

/**
 * Boots the Prometheus exporter; call once per process from the entrypoint.
 *
 * The port is a parameter rather than read from the environment here because
 * clustered workers each need their OWN port — HTTP metrics are recorded in
 * whichever process served the request, so one shared endpoint would report a
 * single worker's traffic as though it were the whole API's.
 */
export async function startTelemetry(port = Number(process.env.OTEL_METRICS_PORT ?? 9464)) {
  const [{ MeterProvider }, { PrometheusExporter }] = await Promise.all([
    import("@opentelemetry/sdk-metrics"),
    import("@opentelemetry/exporter-prometheus"),
  ]);
  const exporter = new PrometheusExporter({ port }, () => {
    console.log(`OTel metrics (Prometheus) on http://localhost:${port}/metrics`);
  });
  const provider = new MeterProvider({ readers: [exporter] });
  metrics.setGlobalMeterProvider(provider);
}
