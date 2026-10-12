import { roleLabel, type Discipline, type RfiScanDisciplineDto, type RfiScanScopeDto } from "@cdip/shared";
import { prisma } from "./db.js";
import { normalizeScope, scopeInput } from "./rfiScanScope.js";

/**
 * The disciplines a project has sheets of, for the scan's two dropdowns —
 * only pages RFI analysis may read (current revisions, processed, not an RFI
 * document). `plans` stays null until a scan has read what each sheet is.
 */
export async function projectDisciplines(projectId: string): Promise<RfiScanDisciplineDto[]> {
  const rows = await prisma.$queryRaw<{ discipline: string; sheets: bigint; plans: bigint; read: bigint }[]>`
    SELECT p.discipline,
           count(*) AS sheets,
           count(*) FILTER (WHERE p."sheetKind" IN ('plan', 'enlarged_plan')) AS plans,
           count(p."sheetKind") AS read
      FROM pages p JOIN documents d ON d.id = p."documentId"
     WHERE d."projectId" = ${projectId} AND d."supersededAt" IS NULL
       AND d.status = 'completed' AND d."includeInRfiAnalysis" = true
       AND p.discipline IS NOT NULL
     GROUP BY p.discipline
     ORDER BY count(*) DESC, p.discipline`;
  return rows.map((r) => ({
    discipline: r.discipline as Discipline,
    label: roleLabel(r.discipline),
    sheets: Number(r.sheets),
    plans: Number(r.read) > 0 ? Number(r.plans) : null,
  }));
}

/** A request's scope, checked against this project's sheets; null for none
 * (everything). */
export async function scopeFromRequest(
  projectId: string,
  raw: unknown,
): Promise<{ scope: RfiScanScopeDto | null } | { error: string }> {
  if (raw === undefined || raw === null) return { scope: null };
  const parsed = scopeInput.safeParse(raw);
  if (!parsed.success) return { error: parsed.error.issues[0]?.message ?? "the scope is not valid" };
  const sheetsBy = Object.fromEntries((await projectDisciplines(projectId)).map((d) => [d.discipline, d.sheets]));
  return normalizeScope(parsed.data, sheetsBy);
}
