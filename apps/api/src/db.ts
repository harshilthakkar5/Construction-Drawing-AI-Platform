import { PrismaClient } from "@prisma/client";

export const prisma = new PrismaClient();

/**
 * Models added after the initial Phase 5 schema. A client generated before
 * they existed still starts fine — the delegate is simply `undefined`, and the
 * first query dies with "Cannot read properties of undefined (reading
 * 'findMany')", which says nothing about the real cause. Check for them up
 * front so the error names the fix instead.
 */
const REQUIRED_MODELS = ["usageEvent", "supportTicket"] as const;

export function missingPrismaModels(): string[] {
  const client = prisma as unknown as Record<string, unknown>;
  return REQUIRED_MODELS.filter((model) => client[model] === undefined);
}

export const PRISMA_STALE_MESSAGE =
  "The generated Prisma client is out of date. Run `npm run prisma:migrate` " +
  "(applies the migration and regenerates), or `npm run prisma:generate` if the " +
  "tables already exist.";
