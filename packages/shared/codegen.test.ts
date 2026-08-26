import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { generated, isStale, OUTPUT_PATH } from "./codegen.mjs";
import { JOB_FIELDS, objectKeys, OBJECT_KEY_TEMPLATES, QUEUES } from "./src/index.js";

/**
 * Drift detection for the one boundary this repo cannot typecheck across.
 *
 * Queue names, job fields and object keys are consumed by the Python worker.
 * Getting one wrong does not fail a build — it fails at runtime, in a job, in
 * production. So the generated file is checked in and this test fails the
 * moment it stops matching its source.
 */
describe("workers/src/generated.py", () => {
  it("is up to date with packages/shared", () => {
    expect(
      isStale(),
      "generated.py is stale — run: npm run codegen -w @cdip/shared",
    ).toBe(false);
  });

  it("carries every queue the API can publish to", () => {
    const file = readFileSync(OUTPUT_PATH, "utf8");
    for (const name of Object.values(QUEUES)) {
      expect(file).toContain(`"${name}"`);
    }
  });

  it("carries every job's payload keys", () => {
    const file = readFileSync(OUTPUT_PATH, "utf8");
    for (const spec of Object.values(JOB_FIELDS)) {
      for (const field of spec.fields) expect(file).toContain(`"${field}"`);
    }
  });

  it("emits snake_case attributes for the Python side", () => {
    expect(generated()).toContain('("spacesKey", "spaces_key", False, "str")');
  });

  it("keeps regionVersion an int, not a string", () => {
    // It is compared against the stored version to decide whether to discard a
    // job; "3" != 3 would silently re-scrape or silently skip.
    expect(generated()).toContain('("regionVersion", "region_version", False, "int")');
  });

  it("marks optional fields optional", () => {
    expect(generated()).toContain('("documentId", "document_id", True, "str")');
  });
});

describe("object keys", () => {
  it("the TS builders are derived from the templates, not a second copy", () => {
    expect(objectKeys.pageImage("p", "d", 7)).toBe("projects/p/pdfs/d/pages/7.png");
    expect(OBJECT_KEY_TEMPLATES.pageImage).toBe(
      "projects/{projectId}/pdfs/{documentId}/pages/{page}.png",
    );
  });

  it("every template's placeholders get filled", () => {
    const filled = [
      objectKeys.originalPdf("p", "d"),
      objectKeys.pageImage("p", "d", 1),
      objectKeys.pageThumb("p", "d", 1),
      objectKeys.pageText("p", "d", 1),
    ];
    for (const key of filled) expect(key).not.toMatch(/[{}]/);
  });
});
