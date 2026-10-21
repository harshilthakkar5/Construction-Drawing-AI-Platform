-- Named RFIs ("Create RFI"): sheets plus library elements and questions; each
-- element is planned as one targeted review linked back by setId.
CREATE TABLE "rfi_sets" (
    "id" TEXT NOT NULL,
    "projectId" TEXT NOT NULL,
    "name" TEXT NOT NULL,
    "createdById" TEXT,
    "sheets" JSONB NOT NULL,
    "items" JSONB NOT NULL,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,
    CONSTRAINT "rfi_sets_pkey" PRIMARY KEY ("id")
);
CREATE INDEX "rfi_sets_projectId_createdAt_idx" ON "rfi_sets"("projectId", "createdAt");
ALTER TABLE "rfi_sets" ADD CONSTRAINT "rfi_sets_projectId_fkey" FOREIGN KEY ("projectId") REFERENCES "projects"("id") ON DELETE CASCADE ON UPDATE CASCADE;

ALTER TABLE "rfi_review_runs" ADD COLUMN "setId" TEXT;
ALTER TABLE "rfi_review_runs" ADD CONSTRAINT "rfi_review_runs_setId_fkey" FOREIGN KEY ("setId") REFERENCES "rfi_sets"("id") ON DELETE SET NULL ON UPDATE CASCADE;
CREATE INDEX "rfi_review_runs_setId_idx" ON "rfi_review_runs"("setId");

-- Elements a project adds to the library under one of its packages.
CREATE TABLE "rfi_custom_elements" (
    "id" TEXT NOT NULL,
    "projectId" TEXT NOT NULL,
    "packageId" TEXT NOT NULL,
    "name" TEXT NOT NULL,
    "createdById" TEXT,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "rfi_custom_elements_pkey" PRIMARY KEY ("id")
);
CREATE INDEX "rfi_custom_elements_projectId_idx" ON "rfi_custom_elements"("projectId");
ALTER TABLE "rfi_custom_elements" ADD CONSTRAINT "rfi_custom_elements_projectId_fkey" FOREIGN KEY ("projectId") REFERENCES "projects"("id") ON DELETE CASCADE ON UPDATE CASCADE;
