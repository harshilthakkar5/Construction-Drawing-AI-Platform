import { describe, expect, it } from "vitest";
import { isValidPdfFilename, sanitizeFilename } from "./sanitize.js";

describe("sanitizeFilename", () => {
  it("keeps ordinary names", () => {
    expect(sanitizeFilename("S201 - Structural Plan.pdf")).toBe("S201 - Structural Plan.pdf");
  });

  it("strips path segments (both separators)", () => {
    expect(sanitizeFilename("../../etc/passwd.pdf")).toBe("passwd.pdf");
    expect(sanitizeFilename("C:\\drawings\\A101.pdf")).toBe("A101.pdf");
  });

  it("removes control characters and angle brackets", () => {
    expect(sanitizeFilename("plan\u0000<script>.pdf")).toBe("planscript.pdf");
    expect(sanitizeFilename("a\u001fb.pdf")).toBe("ab.pdf");
  });

  it("caps length at 255", () => {
    expect(sanitizeFilename(`${"x".repeat(300)}.pdf`)).toHaveLength(255);
  });
});

describe("isValidPdfFilename", () => {
  it("accepts .pdf (case-insensitive)", () => {
    expect(isValidPdfFilename("a.pdf")).toBe(true);
    expect(isValidPdfFilename("A.PDF")).toBe(true);
  });

  it("rejects other extensions and empty names", () => {
    expect(isValidPdfFilename("a.exe")).toBe(false);
    expect(isValidPdfFilename("")).toBe(false);
    expect(isValidPdfFilename("pdf")).toBe(false);
  });
});
