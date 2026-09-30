-- CreateTable
CREATE TABLE "rfi_packages" (
    "id" TEXT NOT NULL,
    "projectId" TEXT NOT NULL,
    "createdById" TEXT,
    "items" JSONB NOT NULL,
    "status" TEXT NOT NULL DEFAULT 'queued',
    "key" TEXT,
    "pages" INTEGER,
    "notes" JSONB,
    "error" TEXT,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "finishedAt" TIMESTAMP(3),

    CONSTRAINT "rfi_packages_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE INDEX "rfi_packages_projectId_createdAt_idx" ON "rfi_packages"("projectId", "createdAt");

-- AddForeignKey
ALTER TABLE "rfi_packages" ADD CONSTRAINT "rfi_packages_projectId_fkey" FOREIGN KEY ("projectId") REFERENCES "projects"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "rfi_packages" ADD CONSTRAINT "rfi_packages_createdById_fkey" FOREIGN KEY ("createdById") REFERENCES "users"("id") ON DELETE SET NULL ON UPDATE CASCADE;

