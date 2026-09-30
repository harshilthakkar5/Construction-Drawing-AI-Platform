import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { FILENAME_EXCLUSION_REASON, RFI_FILENAME_PATTERN, looksLikeRfi } from "./rfiSources.js";

describe("looksLikeRfi", () => {
  it("catches the ways an RFI is named", () => {
    for (const name of [
      "RFI_015_-_Discrepancies_in_dimension_lines.pdf",
      "rfi 002.pdf",
      "Project-RFI-12.pdf",
      "Request for Information 7.pdf",
      "request_for_information.pdf",
    ]) {
      expect(looksLikeRfi(name), name).toBe(true);
    }
  });

  it("leaves drawings alone, including words that merely contain the letters", () => {
    for (const name of ["S2.105.pdf", "clean_S2.105_A3.01.pdf", "Garfield_tower_ARCH.pdf", "terrific.pdf"]) {
      expect(looksLikeRfi(name), name).toBe(false);
    }
  });
});

describe("the data migration", () => {
  it("uses the same pattern and reason as uploads do", () => {
    const sql = readFileSync(new URL("../prisma/migrations/20260930090000_rfi_review_full/migration.sql", import.meta.url), "utf8");
    expect(sql).toContain(`'${RFI_FILENAME_PATTERN}'`);
    expect(sql).toContain(`'${FILENAME_EXCLUSION_REASON}'`);
  });
});
