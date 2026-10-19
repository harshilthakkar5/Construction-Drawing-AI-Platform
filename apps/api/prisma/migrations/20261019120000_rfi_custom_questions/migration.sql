-- CreateTable
CREATE TABLE "rfi_custom_questions" (
    "id" TEXT NOT NULL,
    "projectId" TEXT NOT NULL,
    "elementId" TEXT NOT NULL,
    "parentId" TEXT,
    "category" TEXT,
    "text" TEXT NOT NULL,
    "createdById" TEXT,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "rfi_custom_questions_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE INDEX "rfi_custom_questions_projectId_elementId_idx" ON "rfi_custom_questions"("projectId", "elementId");

-- AddForeignKey
ALTER TABLE "rfi_custom_questions" ADD CONSTRAINT "rfi_custom_questions_projectId_fkey" FOREIGN KEY ("projectId") REFERENCES "projects"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "rfi_custom_questions" ADD CONSTRAINT "rfi_custom_questions_createdById_fkey" FOREIGN KEY ("createdById") REFERENCES "users"("id") ON DELETE SET NULL ON UPDATE CASCADE;

