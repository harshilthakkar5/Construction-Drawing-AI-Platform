/**
 * Emits workers/src/generated.py from the contracts in src/index.ts.
 *
 * Three things have to agree across the TypeScript/Python boundary — queue
 * names, job payload fields, and object keys — and all three used to be typed
 * out twice with a "keep in sync" comment above them. That is a request for a
 * human to be perfect forever, and it already failed once in production.
 *
 *   npm run codegen           writes the file
 *   npm run codegen -- --check exits non-zero if it is stale (CI / vitest)
 */
import { createHash } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { JOB_FIELDS, JOB_FIELD_TYPES, OBJECT_KEY_TEMPLATES, QUEUES } from "./dist/index.js";

const here = dirname(fileURLToPath(import.meta.url));
export const OUTPUT_PATH = join(here, "..", "..", "workers", "src", "generated.py");

const snake = (name) => name.replace(/[A-Z]/g, (c) => `_${c.toLowerCase()}`);

/** `{projectId}` → `{project_id}`, and the argument list to go with it. */
function pythonTemplate(template) {
  const args = [];
  const body = template.replace(/\{(\w+)\}/g, (_match, key) => {
    const arg = snake(key);
    if (!args.includes(arg)) args.push(arg);
    return `{${arg}}`;
  });
  return { body, args };
}

function render() {
  const lines = [
    '"""Contracts shared with the Node API — GENERATED, do not edit.',
    "",
    "Written by packages/shared/codegen.mjs from packages/shared/src/index.ts,",
    "which is the single source for queue names, job payload fields and object",
    "keys. Editing this file by hand is pointless: `npm test` regenerates it and",
    "fails if the checked-in copy differs.",
    "",
    "    npm run codegen -w @cdip/shared",
    '"""',
    "",
    "from __future__ import annotations",
    "",
    "# --- Queue names ---",
    "",
  ];

  for (const [name, value] of Object.entries(QUEUES)) {
    lines.push(`${snake(name).toUpperCase()}_QUEUE = ${JSON.stringify(value)}`);
  }

  lines.push("", "# --- Object keys (Spaces/MinIO bucket layout) ---", "");
  for (const [name, template] of Object.entries(OBJECT_KEY_TEMPLATES)) {
    const { body, args } = pythonTemplate(template);
    const signature = args
      .map((a) => `${a}: ${a === "page" ? "int" : "str"}`)
      .join(", ");
    lines.push(`def ${snake(name)}_key(${signature}) -> str:`);
    lines.push(`    return f${JSON.stringify(body)}`);
    lines.push("");
  }

  lines.push("", "# --- Job payload fields ---", "");
  lines.push("# name -> (payload key, python attribute, is_optional, cast)");
  lines.push("JOB_FIELDS: dict[str, tuple[tuple[str, str, bool, str], ...]] = {");
  for (const [job, spec] of Object.entries(JOB_FIELDS)) {
    const entries = spec.fields.map((field) => {
      const optional = spec.optional.includes(field);
      const cast = JOB_FIELD_TYPES[field] ?? "str";
      return `        (${JSON.stringify(field)}, ${JSON.stringify(snake(field))}, ${
        optional ? "True" : "False"
      }, ${JSON.stringify(cast)}),`;
    });
    lines.push(`    ${JSON.stringify(job)}: (`);
    lines.push(...entries);
    lines.push("    ),");
  }
  lines.push("}");
  lines.push("");
  return lines.join("\n");
}

export function generated() {
  return render();
}

export function isStale() {
  let current = "";
  try {
    current = readFileSync(OUTPUT_PATH, "utf8");
  } catch {
    return true;
  }
  return createHash("sha256").update(current).digest("hex") !==
    createHash("sha256").update(render()).digest("hex");
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  if (process.argv.includes("--check")) {
    if (isStale()) {
      console.error(
        `[codegen] ${OUTPUT_PATH} is stale. Run: npm run codegen -w @cdip/shared`,
      );
      process.exit(1);
    }
    console.log("[codegen] generated.py is up to date");
  } else {
    writeFileSync(OUTPUT_PATH, render());
    console.log(`[codegen] wrote ${OUTPUT_PATH}`);
  }
}
