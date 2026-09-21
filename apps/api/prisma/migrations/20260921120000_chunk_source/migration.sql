-- A description's configuration has to travel WITH it.
--
-- `VLM_*` is read by the worker at ingest and nothing downstream could recover
-- it: the benchmark reads chunks, and the chunks carried no record of which
-- model wrote them or at what settings. "Was that run Claude or Gemini?" cost
-- three separate investigations, and the repair until now was `--label`, typed
-- by hand off a log line. A wrong label is worse than no label, because it
-- manufactures a measurement rather than merely lacking one.
--
-- Both columns are NULLABLE and stay that way. Every chunk written before this
-- migration has no source, a `kind="text"` chunk never has one (it is words
-- lifted off the sheet, not a model's account), and a reader must be able to
-- tell "no vision pass" from "vision pass whose settings were not recorded".
ALTER TABLE "chunks" ADD COLUMN "sourceModel" TEXT;
ALTER TABLE "chunks" ADD COLUMN "sourceSettings" JSONB;
