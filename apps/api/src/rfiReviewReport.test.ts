import { PDFDocument } from "pdf-lib";
import { describe, expect, it } from "vitest";
import type { RfiReviewRunDto } from "@cdip/shared";
import { MAX_REPORT_IMAGES, buildReviewReport, renderReviewReportPdf, winAnsi, wrap, type ReportCandidate } from "./rfiReviewReport.js";

const run = (over: Partial<RfiReviewRunDto> = {}): RfiReviewRunDto => ({
  id: "11111111-2222-3333-4444-555555555555",
  status: "ready",
  stage: null,
  progress: 100,
  target: { type: "compare", values: ["S2.105", "A3.01"] },
  checkMode: "auto",
  checkIds: ["G01", "C01"],
  checkReasons: {},
  checkPlan: { G01: { selected: true, applicability: "applicable", reason: "always" }, C01: { selected: true, applicability: "applicable", reason: "column" } },
  checkResults: { G01: { outcome: "candidate_found", reason: "grids differ", observations: 2, candidates: 1, gaps: [] } },
  inventory: [],
  coverage: { omittedPages: [{ sheetNumber: "S6.01", reason: "ranked below the cap" }], unresolvedReferences: ["S599"], searchLog: [], omissions: [] },
  catalogueVersion: "2026-09-30.1",
  depth: "standard",
  provider: "claude",
  model: "claude-sonnet-5",
  limits: { maxInputTokens: 120_000, maxThinkingTokens: null, thinkingEffort: "medium", maxTotalTokens: 500_000, maxBatches: 2 },
  thinkingRequested: "medium",
  thinkingSent: ["effort=medium"],
  pages: [],
  ambiguous: [],
  chunkCount: 0,
  scopeHash: "x",
  estimate: null,
  usage: null,
  candidates: 2,
  notes: [],
  error: null,
  createdAt: "2026-09-30T00:00:00Z",
  startedAt: null,
  completedAt: null,
  ...over,
});

const candidate = (over: Partial<ReportCandidate> = {}): ReportCandidate => ({
  id: "c1",
  status: "pending",
  checkType: "grid_mismatch",
  confidence: "high",
  priority: "high",
  subject: "Grid 6 is grid 9 on the architectural plan",
  question: "Confirm which naming governs — “6” or “9”?",
  reasoning: "Both sheets draw the same line",
  questionSource: "model",
  evidence: [{ evidenceId: "ev1", sheetNumber: "S2.105", pageNumber: 1, quote: "(whole sheet)", kind: "page" }],
  rfiNumber: null,
  ...over,
});

describe("buildReviewReport", () => {
  it("a draft lists every candidate as NOT issued, and all 16 questions", () => {
    const r = buildReviewReport(run(), [candidate(), candidate({ id: "c2", status: "dismissed" })], [], "draft");
    expect(r.disclaimer[0]).toMatch(/Nothing in this report is an issued RFI/);
    expect(r.candidates.map((c) => c.state)).toEqual(["Draft candidate — not reviewed", "Dismissed by a person"]);
    expect(r.checks).toHaveLength(16);
    expect(r.checks.find((c) => c.id === "G01")).toMatchObject({ outcome: "candidate_found", candidates: 1 });
    expect(r.checks.find((c) => c.id === "C01")!.outcome).toBe("failed");
    expect(r.checks.find((c) => c.id === "F04")!.outcome).toBe("not_selected");
  });

  it("an accepted report lists only accepted candidates, with their RFI numbers", () => {
    const r = buildReviewReport(run(), [candidate(), candidate({ id: "c2", status: "accepted", rfiNumber: 17 })], [], "accepted");
    expect(r.candidates.map((c) => [c.id, c.rfiNumber])).toEqual([["c2", 17]]);
  });

  it("a partial run says so in the header", () => {
    expect(buildReviewReport(run({ status: "partial" }), [], [], "draft").disclaimer.join(" ")).toMatch(/PARTIAL/);
  });

  it("attaches the stored picture to the evidence that cited it", () => {
    const r = buildReviewReport(run(), [candidate()], [{ evidenceId: "ev1", kind: "page", sheetNumber: "S2.105", pageNumber: 1, caption: null, imageKey: "k/ev1.png" }], "draft");
    expect(r.candidates[0]!.evidence[0]!.imageKey).toBe("k/ev1.png");
  });

  it("an unknown cost stays unknown", () => {
    expect(buildReviewReport(run(), [], [], "draft").cost.actualUsd).toBeNull();
  });
});

describe("PDF text", () => {
  it("maps typographic characters into what a standard font can encode", () => {
    expect(winAnsi("“6” — 1/4″ ✓")).toBe('"6" - 1/4? ?');
  });

  it("wraps at the measured width and cuts a word longer than a line", () => {
    const measure = (s: string) => s.length;
    expect(wrap("aa bb cc", 5, measure)).toEqual(["aa bb", "cc"]);
    expect(wrap("abcdefghij", 4, measure)).toEqual(["abcd", "efgh", "ij"]);
  });
});

async function png(): Promise<Uint8Array> {
  // A 1x1 PNG.
  return Uint8Array.from(Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==", "base64"));
}

describe("renderReviewReportPdf", () => {
  it("renders a readable PDF with the evidence pictures, capped", async () => {
    const many = Array.from({ length: MAX_REPORT_IMAGES + 5 }, (_, i) => candidate({ id: `c${i}`, evidence: [{ evidenceId: `ev${i}`, quote: "x" }] }));
    const manifest = many.map((_, i) => ({ evidenceId: `ev${i}`, kind: "crop", sheetNumber: null, pageNumber: 1, caption: null, imageKey: `k/ev${i}.png` }));
    let reads = 0;
    const bytes = await renderReviewReportPdf(buildReviewReport(run(), many, manifest, "draft"), async () => {
      reads++;
      return png();
    });
    expect(Buffer.from(bytes).subarray(0, 5).toString()).toBe("%PDF-");
    expect(reads).toBe(MAX_REPORT_IMAGES);
    expect((await PDFDocument.load(bytes)).getPageCount()).toBeGreaterThan(1);
  });

  it("survives a picture that is not a PNG", async () => {
    const bytes = await renderReviewReportPdf(
      buildReviewReport(run(), [candidate()], [{ evidenceId: "ev1", kind: "page", sheetNumber: null, pageNumber: 1, caption: null, imageKey: "k" }], "draft"),
      async () => new Uint8Array([1, 2, 3]),
    );
    expect(bytes.length).toBeGreaterThan(500);
  });
});
