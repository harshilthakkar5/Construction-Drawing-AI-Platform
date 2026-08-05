import { describe, expect, it } from "vitest";
import { normalizeSpacesEndpoint } from "./spacesEndpoint.js";

/**
 * Regression: pasting the bucket URL from the DigitalOcean console as
 * SPACES_ENDPOINT made every write land under a duplicated key prefix and
 * project deletion fail with `NoSuchKey` from ListObjectsV2.
 */
describe("normalizeSpacesEndpoint", () => {
  it("strips the bucket from a bucket-scoped Spaces endpoint", () => {
    expect(
      normalizeSpacesEndpoint("https://lemonx.blr1.digitaloceanspaces.com", "lemonx"),
    ).toEqual({ endpoint: "https://blr1.digitaloceanspaces.com", corrected: true });
  });

  it("leaves a region endpoint alone", () => {
    expect(
      normalizeSpacesEndpoint("https://blr1.digitaloceanspaces.com", "lemonx"),
    ).toEqual({ endpoint: "https://blr1.digitaloceanspaces.com", corrected: false });
  });

  it("leaves the local MinIO endpoint alone", () => {
    expect(normalizeSpacesEndpoint("http://localhost:9000", "cdip-local")).toEqual({
      endpoint: "http://localhost:9000",
      corrected: false,
    });
  });

  it("does not strip a host that merely starts with the bucket's letters", () => {
    // "lemonxtra" is a different label — only an exact `<bucket>.` prefix counts.
    expect(
      normalizeSpacesEndpoint("https://lemonxtra.blr1.digitaloceanspaces.com", "lemonx"),
    ).toEqual({ endpoint: "https://lemonxtra.blr1.digitaloceanspaces.com", corrected: false });
  });

  it("passes through a non-URL unchanged rather than throwing", () => {
    expect(normalizeSpacesEndpoint("not a url", "bucket")).toEqual({
      endpoint: "not a url",
      corrected: false,
    });
  });
});
