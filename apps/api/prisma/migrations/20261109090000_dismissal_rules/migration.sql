-- Learning from dismissals: why a finding was dismissed, and dismissals
-- remembered for an item (a mark, tag or sheet) or a mark family.
ALTER TABLE "rfi_candidates" ADD COLUMN "itemKey" TEXT;
ALTER TABLE "rfi_candidates" ADD COLUMN "dismissReason" TEXT;
ALTER TABLE "rfi_candidates" ADD COLUMN "dismissNote" TEXT;

CREATE TABLE "rfi_dismissal_rules" (
    "id" TEXT NOT NULL,
    "projectId" TEXT NOT NULL,
    "checkType" TEXT NOT NULL,
    "scope" TEXT NOT NULL,
    "key" TEXT NOT NULL,
    "reason" TEXT NOT NULL,
    "note" TEXT,
    "candidateId" TEXT,
    "createdById" TEXT,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "rfi_dismissal_rules_pkey" PRIMARY KEY ("id")
);
CREATE UNIQUE INDEX "rfi_dismissal_rules_projectId_checkType_scope_key_key" ON "rfi_dismissal_rules"("projectId", "checkType", "scope", "key");
ALTER TABLE "rfi_dismissal_rules" ADD CONSTRAINT "rfi_dismissal_rules_projectId_fkey" FOREIGN KEY ("projectId") REFERENCES "projects"("id") ON DELETE CASCADE ON UPDATE CASCADE;
