import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { resolveStorage } from "./storageConfig.js";

/**
 * The cases live in packages/shared/fixtures/storage-backend.json and are read
 * by BOTH this suite and workers/tests/test_storage_config.py. The API writes
 * the uploaded PDF and the worker reads it back, so a rule that resolves
 * differently on the two sides is a worker that cannot find its own input —
 * making a shared fixture the only way either test proves anything.
 */
const fixturePath = fileURLToPath(
  new URL("../../../packages/shared/fixtures/storage-backend.json", import.meta.url),
);
const fixture = JSON.parse(readFileSync(fixturePath, "utf8")) as {
  resolve: { name: string; env: Record<string, string>; expect: Record<string, string> }[];
  errors: { name: string; env: Record<string, string>; messageContains: string[] }[];
};

describe("resolveStorage (shared fixture)", () => {
  it("covers both backends", () => {
    expect(fixture.resolve.length).toBeGreaterThan(0);
    expect(fixture.errors.length).toBeGreaterThan(0);
  });

  for (const c of fixture.resolve) {
    it(c.name, () => {
      expect({ ...resolveStorage(c.env) }).toEqual(c.expect);
    });
  }

  for (const c of fixture.errors) {
    it(c.name, () => {
      let message = "";
      expect(() => {
        try {
          resolveStorage(c.env);
        } catch (err) {
          message = (err as Error).message;
          throw err;
        }
      }).toThrow();
      for (const needle of c.messageContains) {
        expect(message).toContain(needle);
      }
    });
  }
});

describe("resolveStorage", () => {
  it("reads only the env it is handed, never the ambient process env", () => {
    // The resolver is pure so a test — and a future config endpoint — can ask
    // "what would this .env produce?" without the answer depending on how the
    // test runner was started.
    const before = process.env.STORAGE_BACKEND;
    process.env.STORAGE_BACKEND = "local";
    try {
      expect(
        resolveStorage({
          SPACES_ENDPOINT: "https://blr1.digitaloceanspaces.com",
          SPACES_BUCKET: "b",
          SPACES_KEY: "k",
          SPACES_SECRET: "s",
        }).backend,
      ).toBe("spaces");
    } finally {
      if (before === undefined) delete process.env.STORAGE_BACKEND;
      else process.env.STORAGE_BACKEND = before;
    }
  });

  it("keeps the public endpoint pinned to the backend it belongs to", () => {
    // A leftover SPACES_PUBLIC_ENDPOINT from a Spaces deployment must not
    // follow the switch to local: presigned URLs would point a browser at
    // DigitalOcean for bytes that are on this machine's disk.
    const cfg = resolveStorage({
      STORAGE_BACKEND: "local",
      LOCAL_S3_ENDPOINT: "http://minio:9000",
      SPACES_PUBLIC_ENDPOINT: "https://cdn.example.com",
    });
    expect(cfg.publicEndpoint).toBe("http://minio:9000");
  });
});
