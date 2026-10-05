import { Router } from "express";
import { z } from "zod";
import { objectKeys } from "@cdip/shared";
import { prisma } from "../db.js";
import { objectExists, presignGetObject } from "../s3.js";

/**
 * The diagnostic export of one RFI run (RFI_DIAGNOSTICS=on on the worker):
 * every model request and reply, every image sent, the evidence, the
 * decisions the code made and the final RFI output, zipped by the worker
 * (workers/src/diagnostics.py). This route only hands out a short-lived
 * download link for a run of THIS project — membership is checked by the
 * project middleware, and the run is looked up inside the project so a run
 * id from another project is a 404, not a download.
 */
export const rfiDiagnosticsRouter = Router({ mergeParams: true });

const params = z.object({
  projectId: z.string().uuid(),
  kind: z.enum(["full-scan", "review"]),
  runId: z.string().uuid(),
});

rfiDiagnosticsRouter.get("/:kind/:runId", async (req, res) => {
  const parsed = params.safeParse(req.params);
  if (!parsed.success) return void res.status(400).json({ error: "invalid run" });
  const { projectId, kind, runId } = parsed.data;
  const run =
    kind === "full-scan"
      ? await prisma.rfiFullScan.findFirst({ where: { id: runId, projectId }, select: { id: true } })
      : await prisma.rfiReviewRun.findFirst({ where: { id: runId, projectId }, select: { id: true } });
  if (!run) return void res.status(404).json({ error: "run not found" });
  const key = objectKeys.rfiDiagnostics(projectId, kind, runId);
  if (!(await objectExists(key))) {
    return void res.status(404).json({
      error:
        "No diagnostic export was recorded for this run. Set RFI_DIAGNOSTICS=on on the worker, restart it, and run the scan or review again.",
    });
  }
  res.json({ downloadUrl: await presignGetObject(key, 3600, `rfi-diagnostics-${kind}-${runId.slice(0, 8)}.zip`) });
});
