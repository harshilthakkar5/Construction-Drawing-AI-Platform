import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { buildPageManifest, findManifestEntry, orderDocuments } from "./manifest.js";

const __dirnameShim = dirname(fileURLToPath(import.meta.url));

const doc = (id: string, pages: number, createdAt: string, filename = `${id}.pdf`) => ({
  id,
  filename,
  pages,
  createdAt: new Date(createdAt),
});

describe("orderDocuments", () => {
  it("orders by createdAt ascending", () => {
    const docs = [doc("b", 1, "2026-01-02"), doc("a", 1, "2026-01-01")];
    expect(orderDocuments(docs).map((d) => d.id)).toEqual(["a", "b"]);
  });

  it("breaks createdAt ties by id so numbering is deterministic", () => {
    const docs = [doc("z", 1, "2026-01-01"), doc("a", 1, "2026-01-01"), doc("m", 1, "2026-01-01")];
    expect(orderDocuments(docs).map((d) => d.id)).toEqual(["a", "m", "z"]);
  });

  it("does not mutate its input", () => {
    const docs = [doc("b", 1, "2026-01-02"), doc("a", 1, "2026-01-01")];
    orderDocuments(docs);
    expect(docs.map((d) => d.id)).toEqual(["b", "a"]);
  });
});

describe("buildPageManifest", () => {
  it("numbers a single document 1..N", () => {
    const entries = buildPageManifest([doc("a", 3, "2026-01-01")]);
    expect(entries.map((e) => [e.pageNumber, e.combinedPageNumber])).toEqual([
      [1, 1],
      [2, 2],
      [3, 3],
    ]);
  });

  it("numbers multiple documents continuously with no gaps or overlaps", () => {
    const entries = buildPageManifest([
      doc("b", 2, "2026-01-02"),
      doc("a", 3, "2026-01-01"),
      doc("c", 4, "2026-01-03"),
    ]);
    expect(entries).toHaveLength(9);
    expect(entries.map((e) => e.combinedPageNumber)).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9]);
    // a (3 pages) first, then b (2), then c (4)
    expect(entries[0]).toMatchObject({ documentId: "a", pageNumber: 1, combinedPageNumber: 1 });
    expect(entries[3]).toMatchObject({ documentId: "b", pageNumber: 1, combinedPageNumber: 4 });
    expect(entries[5]).toMatchObject({ documentId: "c", pageNumber: 1, combinedPageNumber: 6 });
    expect(entries[8]).toMatchObject({ documentId: "c", pageNumber: 4, combinedPageNumber: 9 });
  });

  it("skips documents with zero pages (not yet processed) without breaking continuity", () => {
    const entries = buildPageManifest([
      doc("a", 2, "2026-01-01"),
      doc("pending", 0, "2026-01-02"),
      doc("c", 2, "2026-01-03"),
    ]);
    expect(entries.map((e) => [e.documentId, e.combinedPageNumber])).toEqual([
      ["a", 1],
      ["a", 2],
      ["c", 3],
      ["c", 4],
    ]);
  });

  it("returns an empty manifest for no documents", () => {
    expect(buildPageManifest([])).toEqual([]);
  });

  it("is stable regardless of input order", () => {
    const docs = [doc("a", 3, "2026-01-01"), doc("b", 2, "2026-01-02")];
    const reversed = [...docs].reverse();
    expect(buildPageManifest(docs)).toEqual(buildPageManifest(reversed));
  });
});

describe("findManifestEntry", () => {
  const docs = [doc("a", 3, "2026-01-01"), doc("b", 2, "2026-01-02")];

  it("resolves combined numbers to the right (document, page)", () => {
    expect(findManifestEntry(docs, 1)).toMatchObject({ documentId: "a", pageNumber: 1 });
    expect(findManifestEntry(docs, 3)).toMatchObject({ documentId: "a", pageNumber: 3 });
    expect(findManifestEntry(docs, 4)).toMatchObject({ documentId: "b", pageNumber: 1 });
    expect(findManifestEntry(docs, 5)).toMatchObject({ documentId: "b", pageNumber: 2 });
  });

  it("agrees with buildPageManifest for every page", () => {
    for (const entry of buildPageManifest(docs)) {
      expect(findManifestEntry(docs, entry.combinedPageNumber)).toEqual(entry);
    }
  });

  it("returns undefined out of range and for non-positive or fractional input", () => {
    expect(findManifestEntry(docs, 0)).toBeUndefined();
    expect(findManifestEntry(docs, 6)).toBeUndefined();
    expect(findManifestEntry(docs, -1)).toBeUndefined();
    expect(findManifestEntry(docs, 2.5)).toBeUndefined();
    expect(findManifestEntry([], 1)).toBeUndefined();
  });
});

// --- The shared fixture (see packages/shared/fixtures/combined-numbering.json) ---

interface FixtureDoc {
  id: string;
  createdAt: string;
  pages: number;
  superseded?: boolean;
}

const fixture = JSON.parse(
  readFileSync(
    join(__dirnameShim, "..", "..", "..", "packages", "shared", "fixtures", "combined-numbering.json"),
    "utf8",
  ),
) as { cases: { name: string; documents: FixtureDoc[]; expected: [string, number, number][] }[] };

/**
 * The numbering rule decides which page a citation resolves to, and it exists
 * twice: here, and as the recompute SQL in workers/src/db.py (mirrored for
 * testing in workers/src/numbering.py). Both read THIS fixture, so changing
 * the rule in one language fails the other language's tests.
 */
describe("combined numbering — shared fixture", () => {
  for (const testCase of fixture.cases) {
    it(testCase.name, () => {
      // The API filters superseded documents before building the manifest,
      // exactly as routes/pages.ts does with `supersededAt: null`.
      const docs = testCase.documents
        .filter((d) => !d.superseded)
        .map((d) => ({
          id: d.id,
          filename: `${d.id}.pdf`,
          pages: d.pages,
          createdAt: new Date(d.createdAt),
        }));
      const actual = buildPageManifest(docs).map(
        (e) => [e.documentId, e.pageNumber, e.combinedPageNumber] as const,
      );
      expect(actual).toEqual(testCase.expected);
    });
  }

  it("loaded a real fixture, so the cases above are not vacuous", () => {
    expect(fixture.cases.length).toBeGreaterThanOrEqual(5);
  });
});
