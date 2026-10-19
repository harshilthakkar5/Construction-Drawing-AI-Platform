/**
 * Turns the client's "Concrete Scope Element Library" workbook into
 * packages/shared/data/rfi-library.json — the question library the app ships.
 * Run by a developer when the client sends a new version; users never upload
 * the workbook. Commit the JSON it writes.
 *
 *   npx tsx apps/api/src/rfiLibraryImport.ts path/to/library.xlsx
 *
 * Red-filled rows are elements the client keeps for later: imported with
 * `active: false`, and their questions with them. Ids are the client's own
 * (CE-04-01, CE-04-01-Q03, U-02), so a re-import updates by id.
 *
 * LINKS is ours, not the client's: which of the original 17 questions and
 * which code checks answer each element or question. It lives here so a
 * re-import keeps it; a link to an id the new workbook dropped fails the import.
 */
import { writeFileSync } from "node:fs";
import { LIBRARY_URL } from "./rfiLibrary.js";
import { fileURLToPath } from "node:url";
import ExcelJS from "exceljs";
import type { RfiCheckType, RfiLibraryFile, RfiReviewCheckId } from "@cdip/shared";

type Link = { catalogue?: RfiReviewCheckId[]; codeChecks?: RfiCheckType[] };

/** id → { catalogue: original question ids, codeChecks: RFI_CHECK_LABELS keys }. */
export const LINKS: Record<string, Link> = {
  // Element-level: a chosen question with no link of its own is asked through these.
  "CE-01-01": { catalogue: ["G01"] },
  "CE-01-02": { catalogue: ["G02"] },
  "CE-03-05": { catalogue: ["F04"] },
  "CE-04-02": { catalogue: ["F01"] },
  "CE-04-03": { catalogue: ["F01"] },
  "CE-04-05": { catalogue: ["F01", "F02"] },
  "CE-04-07": { catalogue: ["B01", "B02", "B03"] },
  "CE-04-09": { catalogue: ["C02"] },
  "CE-04-10": { catalogue: ["C01"] },
  "CE-06-02": { catalogue: ["FL03"] },
  "CE-07-02": { catalogue: ["C01"] },
  "CE-07-06": { catalogue: ["C02"] },
  "CE-08-04": { catalogue: ["B01", "B02", "B03"] },
  "CE-08-05": { catalogue: ["B01", "B02", "B03"] },
  "CE-12-04": { catalogue: ["O01"] },
  "CE-01-01-Q01": { catalogue: ["G01"], codeChecks: ["grid_mismatch"] },
  "CE-01-01-Q02": { catalogue: ["G01"], codeChecks: ["grid_spacing"] },
  "CE-01-01-Q08": { codeChecks: ["grid_mismatch", "grid_spacing"] },
  "CE-01-02-Q01": { catalogue: ["G02"] },
  "CE-01-02-Q03": { catalogue: ["G02"] },
  "CE-03-01": { catalogue: ["F04"] },
  "CE-03-03": { catalogue: ["F04"] },
  "CE-03-04": { catalogue: ["F04"] },
  "CE-03-05-Q01": { codeChecks: ["unscheduled_mark", "unmarked_element"] },
  "CE-04-01": { catalogue: ["F01"] },
  "CE-04-01-Q01": { codeChecks: ["unscheduled_mark", "unmarked_element"] },
  "CE-04-04": { catalogue: ["F02"] },
  "CE-04-06": { catalogue: ["F03"] },
  "CE-04-07-Q01": { codeChecks: ["unscheduled_mark"] },
  "CE-04-10-Q01": { codeChecks: ["unscheduled_mark"] },
  "CE-06-01": { catalogue: ["FL02"] },
  "CE-06-06": { catalogue: ["FL04"] },
  "CE-07-01": { catalogue: ["C01"] },
  "CE-07-01-Q01": { codeChecks: ["unscheduled_mark", "unsized_column"] },
  "CE-07-01-Q02": { codeChecks: ["column_mismatch"] },
  "CE-07-01-Q03": { catalogue: ["C03"], codeChecks: ["column_mismatch"] },
  "CE-07-03": { catalogue: ["C02"] },
  "CE-07-05": { catalogue: ["O01"] },
  "CE-08-01": { catalogue: ["FL02"] },
  "CE-08-03-Q01": { codeChecks: ["unscheduled_mark"] },
  "CE-08-03-Q02": { catalogue: ["B02"] },
  "CE-08-03-Q07": { catalogue: ["B01"] },
  "CE-08-03-Q08": { catalogue: ["B03"] },
  "CE-08-09-Q01": { catalogue: ["FL01"] },
  "CE-08-10": { catalogue: ["O01"] },
  "CE-08-11": { catalogue: ["FL03"] },
  "CE-08-13": { catalogue: ["FL04"] },
  "CE-08-14": { catalogue: ["FL04"] },
  "CE-12-05": { catalogue: ["O01"] },
  "U-01": { codeChecks: ["unscheduled_mark", "unmarked_element"] },
  "U-07": { codeChecks: ["dangling_reference"] },
};

const text = (cell: ExcelJS.Cell): string => {
  const v = cell.value;
  if (v == null) return "";
  if (typeof v === "object" && "richText" in v) return v.richText.map((r) => r.text).join("").trim();
  if (typeof v === "object" && "result" in v) return String(v.result ?? "").trim();
  if (typeof v === "object" && "text" in v) return String(v.text).trim();
  return String(v).trim();
};
/** The client marks "not for now" with a solid red fill. */
const isRed = (row: ExcelJS.Row): boolean => {
  const fill = row.getCell(1).fill;
  const argb = fill?.type === "pattern" ? (fill.fgColor?.argb ?? "") : "";
  return /^(FF)?FF0000$/i.test(argb);
};
const list = (s: string): string[] => s.split(",").map((x) => x.trim()).filter(Boolean);

function rows(sheet: ExcelJS.Worksheet): ExcelJS.Row[] {
  const out: ExcelJS.Row[] = [];
  sheet.eachRow((row, n) => {
    if (n > 1 && text(row.getCell(1))) out.push(row);
  });
  return out;
}

export async function readLibrary(path: string, links: Record<string, Link> = LINKS): Promise<RfiLibraryFile> {
  const book = new ExcelJS.Workbook();
  await book.xlsx.readFile(path);
  const need = (name: string): ExcelJS.Worksheet => {
    const s = book.getWorksheet(name);
    if (!s) throw new Error(`the workbook has no "${name}" sheet`);
    return s;
  };

  const packages = new Map<string, RfiLibraryFile["packages"][number]>();
  const elements = rows(need("Element Library")).map((r) => {
    const [, pkgId, pkgName] = /^(\d+)\s+(.*)$/.exec(text(r.getCell(3))) ?? [];
    if (!pkgId || !pkgName) throw new Error(`row ${r.number}: package "${text(r.getCell(3))}" has no number`);
    const id = text(r.getCell(1));
    const active = !isRed(r);
    const pkg = packages.get(pkgId) ?? { id: pkgId, name: pkgName, active: false };
    pkg.active ||= active;
    packages.set(pkgId, pkg);
    return {
      id,
      seq: Number(text(r.getCell(2))),
      packageId: pkgId,
      name: text(r.getCell(4)),
      variants: text(r.getCell(5)),
      compareAcross: list(text(r.getCell(6))),
      attributes: text(r.getCell(7)),
      triggers: text(r.getCell(8)),
      active,
      catalogue: links[id]?.catalogue ?? [],
    };
  });
  const byId = new Map(elements.map((e) => [e.id, e]));

  const questions = rows(need("Question Bank")).map((r) => {
    const id = text(r.getCell(1));
    const element = byId.get(text(r.getCell(2)));
    if (!element) throw new Error(`question ${id}: unknown element "${text(r.getCell(2))}"`);
    return {
      id,
      elementId: element.id,
      parentId: null,
      category: text(r.getCell(5)),
      text: text(r.getCell(6)),
      whereToLook: list(text(r.getCell(7))),
      // A question is off with its element; the client marks elements only.
      active: element.active && !isRed(r),
      catalogue: links[id]?.catalogue ?? [],
      codeChecks: links[id]?.codeChecks ?? [],
    };
  });

  const universal = rows(need("Universal Questions"))
    .filter((r) => /^U-\d+$/.test(text(r.getCell(1))))
    .map((r) => ({
      id: text(r.getCell(1)),
      category: text(r.getCell(2)),
      template: text(r.getCell(3)),
      codeChecks: links[text(r.getCell(1))]?.codeChecks ?? [],
    }));

  const ids = new Set([...byId.keys(), ...questions.map((q) => q.id), ...universal.map((u) => u.id)]);
  const stale = Object.keys(links).filter((id) => !ids.has(id));
  if (stale.length) throw new Error(`LINKS names ids the workbook no longer has: ${stale.join(", ")}`);
  const dupes = questions.map((q) => q.id).filter((id, i, all) => all.indexOf(id) !== i);
  if (dupes.length) throw new Error(`duplicate question ids: ${dupes.join(", ")}`);

  return {
    source: path.split(/[\\/]/).pop() ?? path,
    packages: [...packages.values()].sort((a, b) => a.id.localeCompare(b.id)),
    elements: elements.sort((a, b) => a.seq - b.seq),
    questions,
    universal,
  };
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const path = process.argv[2];
  if (!path) {
    console.error("usage: npx tsx apps/api/src/rfiLibraryImport.ts <workbook.xlsx>");
    process.exit(2);
  }
  const lib = await readLibrary(path);
  const out = fileURLToPath(LIBRARY_URL);
  writeFileSync(out, JSON.stringify(lib, null, 1) + "\n");
  const on = (xs: { active: boolean }[]) => xs.filter((x) => x.active).length;
  console.log(`wrote ${out}: ${lib.packages.length} packages, ${lib.elements.length} elements (${on(lib.elements)} active), ` +
    `${lib.questions.length} questions (${on(lib.questions)} active), ${lib.universal.length} universal`);
}
