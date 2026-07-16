import { describe, expect, it } from "vitest";
import {
  buildSources,
  extractCitedChunkIds,
  sourceLabel,
  type ChunkSourceRecord,
} from "./citations.js";

const ID_A = "11111111-aaaa-4bbb-8ccc-000000000001";
const ID_B = "22222222-aaaa-4bbb-8ccc-000000000002";
const ID_UNKNOWN = "99999999-aaaa-4bbb-8ccc-000000000009";

const record = (chunkId: string, combined: number, filename = "S201.pdf"): ChunkSourceRecord => ({
  chunkId,
  documentId: "doc-1",
  filename,
  pageNumber: combined,
  combinedPageNumber: combined,
  bbox: { x: 10, y: 20, width: 100, height: 50 },
});

const records = new Map([
  [ID_A, record(ID_A, 17)],
  [ID_B, record(ID_B, 4, "A-101.pdf")],
]);

describe("extractCitedChunkIds", () => {
  it("extracts ids in order of first appearance", () => {
    const answer = `Footings bear on soil [chunk:${ID_B}]. Columns are W10 [chunk:${ID_A}].`;
    expect(extractCitedChunkIds(answer)).toEqual([ID_B, ID_A]);
  });

  it("dedupes repeated citations", () => {
    const answer = `A [chunk:${ID_A}] and again [chunk:${ID_A}].`;
    expect(extractCitedChunkIds(answer)).toEqual([ID_A]);
  });

  it("is case-insensitive on hex and returns lowercase", () => {
    expect(extractCitedChunkIds(`x [chunk:${ID_A.toUpperCase()}]`)).toEqual([ID_A]);
  });

  it("ignores malformed markers", () => {
    expect(extractCitedChunkIds("no [chunk:123] or [chunk:] or [source:abc] here")).toEqual([]);
    expect(extractCitedChunkIds("")).toEqual([]);
  });
});

describe("buildSources", () => {
  it("maps chunk ids to document/page/bbox — the verification chain", () => {
    const { sources } = buildSources(`Claim [chunk:${ID_A}].`, records);
    expect(sources).toHaveLength(1);
    expect(sources[0]).toMatchObject({
      chunkId: ID_A,
      documentId: "doc-1",
      combinedPageNumber: 17,
      bbox: { x: 10, y: 20, width: 100, height: 50 },
    });
  });

  it("numbers sources by first appearance and rewrites markers to [n]", () => {
    const answer = `First [chunk:${ID_B}], second [chunk:${ID_A}], first again [chunk:${ID_B}].`;
    const { text, sources } = buildSources(answer, records);
    expect(text).toBe("First [1], second [2], first again [1].");
    expect(sources.map((s) => [s.index, s.chunkId])).toEqual([
      [1, ID_B],
      [2, ID_A],
    ]);
  });

  it("strips citations of unknown chunk ids and keeps prose clean", () => {
    const answer = `Real [chunk:${ID_A}]. Hallucinated [chunk:${ID_UNKNOWN}].`;
    const { text, sources } = buildSources(answer, records);
    expect(text).toBe("Real [1]. Hallucinated.");
    expect(sources).toHaveLength(1);
  });

  it("returns untouched text when nothing is cited", () => {
    const { text, sources } = buildSources("No citations here.", records);
    expect(text).toBe("No citations here.");
    expect(sources).toEqual([]);
  });

  it("builds FR-21-style clickable labels", () => {
    expect(sourceLabel(record(ID_A, 17))).toBe("S201.pdf — page 17");
    const { sources } = buildSources(`x [chunk:${ID_B}]`, records);
    expect(sources[0]!.label).toBe("A-101.pdf — page 4");
  });
});
