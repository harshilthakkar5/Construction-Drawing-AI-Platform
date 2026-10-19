import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import type { RfiLibraryDto, RfiLibraryElementDto, RfiLibraryFile, RfiLibraryQuestionDto } from "@cdip/shared";

/**
 * The question library the app ships (packages/shared/data/rfi-library.json,
 * written by rfiLibraryImport.ts from the client's workbook), plus what each
 * project saved on top of it. Users never upload the workbook.
 *
 * Same path from src/ (tsx) and dist/ (node): both sit three levels below the
 * repository root, and the API image copies packages/shared whole.
 */
export const LIBRARY_URL = new URL("../../../packages/shared/data/rfi-library.json", import.meta.url);

let cached: { file: RfiLibraryFile; version: string } | null = null;

export function library(): { file: RfiLibraryFile; version: string } {
  if (!cached) {
    const raw = readFileSync(LIBRARY_URL, "utf8");
    cached = { file: JSON.parse(raw) as RfiLibraryFile, version: createHash("sha256").update(raw).digest("hex").slice(0, 12) };
  }
  return cached;
}

/** A question a project saved: under an element, or under any question of it. */
export interface CustomQuestionRow {
  id: string;
  elementId: string;
  parentId: string | null;
  category: string | null;
  text: string;
}

const universalId = (elementId: string, uid: string) => `${elementId}-${uid}`;

/** Every question id of an ACTIVE element — library, universal and the
 * project's own — so a follow-up can only hang under one of its element. */
export function questionIdsOf(file: RfiLibraryFile, elementId: string, customs: CustomQuestionRow[]): Set<string> | null {
  const element = file.elements.find((e) => e.id === elementId && e.active);
  if (!element) return null;
  return new Set([
    ...file.questions.filter((q) => q.elementId === elementId && q.active).map((q) => q.id),
    ...file.universal.map((u) => universalId(elementId, u.id)),
    ...customs.filter((c) => c.elementId === elementId).map((c) => c.id),
  ]);
}

/** The project's view: active packages → elements (review order) → questions,
 * each with its follow-ups. A custom question whose parent is gone is shown
 * at the top of its element rather than lost. */
export function buildLibrary(file: RfiLibraryFile, version: string, customs: CustomQuestionRow[]): RfiLibraryDto {
  const elements = new Map<string, RfiLibraryElementDto>();
  const nodes = new Map<string, RfiLibraryQuestionDto>();
  const tops = new Map<string, RfiLibraryQuestionDto[]>();
  const pending: { elementId: string; parentId: string | null; node: RfiLibraryQuestionDto }[] = [];
  const place = (elementId: string, parentId: string | null, node: RfiLibraryQuestionDto) => {
    nodes.set(node.id, node);
    pending.push({ elementId, parentId, node });
  };

  for (const e of file.elements) {
    if (!e.active) continue;
    elements.set(e.id, {
      id: e.id, name: e.name, variants: e.variants, compareAcross: e.compareAcross,
      attributes: e.attributes, triggers: e.triggers, catalogue: e.catalogue, questions: [],
    });
    tops.set(e.id, []);
  }
  for (const q of file.questions) {
    if (!q.active || !elements.has(q.elementId)) continue;
    place(q.elementId, q.parentId, {
      id: q.id, source: "library", category: q.category, text: q.text, whereToLook: q.whereToLook,
      catalogue: q.catalogue, codeChecks: q.codeChecks, children: [],
    });
  }
  for (const [elementId, e] of elements) {
    for (const u of file.universal) {
      place(elementId, null, {
        id: universalId(elementId, u.id), source: "universal", category: u.category,
        text: u.template.replaceAll("{element}", e.name), whereToLook: e.compareAcross,
        catalogue: [], codeChecks: u.codeChecks, children: [],
      });
    }
  }
  for (const c of customs) {
    if (!elements.has(c.elementId)) continue;
    place(c.elementId, c.parentId, {
      id: c.id, source: "custom", category: c.category, text: c.text, whereToLook: [],
      catalogue: [], codeChecks: [], children: [],
    });
  }
  for (const { elementId, parentId, node } of pending) {
    const parent = parentId ? nodes.get(parentId) : undefined;
    (parent ? parent.children : tops.get(elementId)!).push(node);
  }
  for (const [id, e] of elements) e.questions = tops.get(id)!;

  return {
    version,
    packages: file.packages
      .filter((p) => p.active)
      .map((p) => ({
        id: p.id,
        name: p.name,
        elements: file.elements.filter((e) => e.packageId === p.id && elements.has(e.id)).map((e) => elements.get(e.id)!),
      })),
  };
}
