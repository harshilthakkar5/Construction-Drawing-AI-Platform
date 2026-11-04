-- Live processing progress and a classified failure reason per document.
ALTER TABLE "documents" ADD COLUMN "progress" JSONB, ADD COLUMN "problem" JSONB;
