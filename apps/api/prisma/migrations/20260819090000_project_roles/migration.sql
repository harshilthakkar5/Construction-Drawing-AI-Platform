-- Disciplines the project is being read FOR (the creator's role focus). Used to
-- steer summary emphasis; it never filters what is extracted or indexed.
ALTER TABLE "projects" ADD COLUMN "roles" TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[];
