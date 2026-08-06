import { describe, expect, it } from "vitest";
import {
  DEFAULT_PREVIEW_SAMPLE,
  isPlausibleBox,
  regionBoxSchema,
  regionPreviewSchema,
  regionSaveSchema,
} from "./region.js";

/**
 * A bad region is expensive to discover: it is accepted, scraped against every
 * page of every document, and only shows up as "everything is unclassified"
 * once the job finishes. These rules are the cheap place to catch it.
 */
const titleBlock = { relX: 0.82, relY: 0.9, relW: 0.16, relH: 0.07 };

describe("regionBoxSchema", () => {
  it("accepts a bottom-right title-block box", () => {
    expect(regionBoxSchema.parse(titleBlock)).toEqual(titleBlock);
  });

  it("accepts a box covering the whole page", () => {
    expect(() => regionBoxSchema.parse({ relX: 0, relY: 0, relW: 1, relH: 1 })).not.toThrow();
  });

  it("rejects a box running off the right or bottom edge", () => {
    expect(() => regionBoxSchema.parse({ relX: 0.9, relY: 0.1, relW: 0.2, relH: 0.1 })).toThrow(
      /falls off the page/,
    );
    expect(() => regionBoxSchema.parse({ relX: 0.1, relY: 0.95, relW: 0.1, relH: 0.2 })).toThrow(
      /falls off the page/,
    );
  });

  it("rejects zero-area and negative boxes", () => {
    expect(() => regionBoxSchema.parse({ relX: 0.5, relY: 0.5, relW: 0, relH: 0.1 })).toThrow();
    expect(() => regionBoxSchema.parse({ relX: 0.5, relY: 0.5, relW: 0.1, relH: -0.1 })).toThrow();
  });

  it("rejects coordinates outside the page", () => {
    expect(() => regionBoxSchema.parse({ relX: -0.01, relY: 0.5, relW: 0.1, relH: 0.1 })).toThrow();
    expect(() => regionBoxSchema.parse({ relX: 1.5, relY: 0.5, relW: 0.1, relH: 0.1 })).toThrow();
  });

  it("tolerates float error at the page edge", () => {
    // 0.8 + 0.2 lands a hair over 1 in binary floating point.
    expect(() =>
      regionBoxSchema.parse({ relX: 0.8, relY: 0.7, relW: 0.2, relH: 0.3 }),
    ).not.toThrow();
  });

  it("requires every coordinate", () => {
    expect(() => regionBoxSchema.parse({ relX: 0.1, relY: 0.1, relW: 0.1 })).toThrow();
  });
});

describe("isPlausibleBox", () => {
  it("accepts a real title-block box", () => {
    expect(isPlausibleBox(titleBlock)).toBe(true);
  });

  it("rejects a stray click that technically validates", () => {
    expect(isPlausibleBox({ relX: 0.5, relY: 0.5, relW: 0.0001, relH: 0.0001 })).toBe(false);
  });
});

describe("regionSaveSchema", () => {
  it("carries the page the box was drawn on", () => {
    const parsed = regionSaveSchema.parse({
      ...titleBlock,
      sampleDocumentId: "3f1a1c1e-0000-4000-8000-000000000000",
      samplePageNumber: 7,
    });
    expect(parsed.samplePageNumber).toBe(7);
  });

  it("makes the sample page optional", () => {
    expect(() => regionSaveSchema.parse(titleBlock)).not.toThrow();
  });

  it("rejects a non-uuid sample document", () => {
    expect(() => regionSaveSchema.parse({ ...titleBlock, sampleDocumentId: "doc-1" })).toThrow();
  });
});

describe("regionPreviewSchema", () => {
  it("defaults are applied by the route, not the schema", () => {
    expect(regionPreviewSchema.parse(titleBlock)).not.toHaveProperty("sampleSize");
    expect(DEFAULT_PREVIEW_SAMPLE).toBeGreaterThan(0);
  });

  it("caps the sample size so a preview stays cheap", () => {
    expect(() => regionPreviewSchema.parse({ ...titleBlock, sampleSize: 20 })).not.toThrow();
    expect(() => regionPreviewSchema.parse({ ...titleBlock, sampleSize: 100 })).toThrow();
    expect(() => regionPreviewSchema.parse({ ...titleBlock, sampleSize: 0 })).toThrow();
  });
});
