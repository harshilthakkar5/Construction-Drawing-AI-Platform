import { PDFDocument, StandardFonts, rgb, type PDFFont, type PDFPage } from "pdf-lib";
import { RFI_REVIEW_CHECKS, type RfiReviewRunDto } from "@cdip/shared";

/**
 * A targeted review's report, as JSON and as a PDF (docs/rfi-targeted-review.md).
 *
 * Two kinds, and the difference is the whole point of the header:
 *   draft     every candidate, each marked with its state. Nothing in it has
 *             been issued; it is the review's output, for a person to read.
 *   accepted  only candidates a person accepted into the RFI log, with the
 *             RFI number they were given. Still not an issued RFI unless that
 *             RFI's own status says so — the log is the record, not this.
 *
 * Built from stored rows only; no model is asked anything to make it.
 */

export type ReportKind = "draft" | "accepted";

export interface ReportCandidate {
  id: string;
  status: string;
  checkType: string;
  confidence: string;
  priority: string | null;
  subject: string;
  question: string;
  reasoning: string | null;
  questionSource: string;
  evidence: unknown;
  rfiNumber: number | null;
}

export interface ManifestItem {
  evidenceId: string;
  kind: string;
  sheetNumber: string | null;
  pageNumber: number;
  caption: string | null;
  imageKey: string | null;
}

export interface ReviewReport {
  kind: ReportKind;
  title: string;
  generatedAt: string;
  disclaimer: string[];
  run: {
    id: string;
    status: string;
    target: string;
    catalogueVersion: string;
    provider: string;
    model: string;
    depth: string;
    thinkingRequested: string;
    thinkingSent: string[];
    limits: RfiReviewRunDto["limits"];
    createdAt: string;
    completedAt: string | null;
  };
  checks: {
    id: string;
    section: string;
    number: number;
    originalQuestion: string;
    selected: boolean;
    applicability: string;
    outcome: string;
    reason: string;
    candidates: number;
    gaps: string[];
  }[];
  scope: {
    pages: { sheetNumber: string | null; pageNumber: number; role: string; reason: string; rendered: boolean }[];
    omittedPages: { sheetNumber: string | null; reason: string }[];
    unresolvedReferences: string[];
    omissions: string[];
    searchLog: { query: string; found: number; stage: string }[];
  };
  candidates: {
    id: string;
    state: string;
    rfiNumber: number | null;
    checkType: string;
    confidence: string;
    priority: string | null;
    subject: string;
    question: string;
    whyFlagged: string | null;
    wordedBy: string;
    evidence: { evidenceId: string | null; sheetNumber: string | null; pageNumber: number | null; kind: string | null; quote: string; observation: string | null; imageKey: string | null }[];
  }[];
  inventory: RfiReviewRunDto["inventory"];
  cost: {
    estimatedLowUsd: number | null;
    estimatedHighUsd: number | null;
    actualUsd: number | null;
    actualTokens: number | null;
    planningUsd: number | null;
    pricingVersion: string | null;
  };
  notes: string[];
}

const targetText = (t: RfiReviewRunDto["target"]): string => {
  if (t.type === "sheet") return `Sheet ${t.value}`;
  if (t.type === "compare") return `Compare ${t.values.join(" / ")}`;
  return `Element ${t.value}${t.level ? `, ${t.level}` : ""}${t.area ? `, ${t.area}` : ""}`;
};

const STATE: Record<string, string> = {
  pending: "Draft candidate — not reviewed",
  accepted: "Accepted into the RFI log",
  dismissed: "Dismissed by a person",
};

export function buildReviewReport(
  run: RfiReviewRunDto,
  candidates: ReportCandidate[],
  manifest: ManifestItem[],
  kind: ReportKind,
  now = new Date(),
): ReviewReport {
  const images = new Map(manifest.map((m) => [m.evidenceId, m.imageKey]));
  const shown = kind === "accepted" ? candidates.filter((c) => c.status === "accepted") : candidates;
  const results = run.checkResults ?? {};
  const disclaimer =
    kind === "draft"
      ? [
          "DRAFT. Nothing in this report is an issued RFI. Each candidate is a question a model proposed and code checked; a person decides whether it is asked.",
          "\"No issue found\" is a statement about the pages reviewed, not about the project.",
        ]
      : [
          "Only candidates a person accepted into the RFI log are listed. Each carries the RFI number it was given; the log, not this report, is the record of what was issued.",
        ];
  if (run.status === "partial") disclaimer.push("This review is PARTIAL: some evidence or checks were not completed — see Coverage.");

  return {
    kind,
    title: kind === "draft" ? "RFI review — draft candidates" : "RFI review — accepted RFIs",
    generatedAt: now.toISOString(),
    disclaimer,
    run: {
      id: run.id,
      status: run.status,
      target: targetText(run.target),
      catalogueVersion: run.catalogueVersion,
      provider: run.provider,
      model: run.model,
      depth: run.depth,
      thinkingRequested: run.thinkingRequested,
      thinkingSent: run.thinkingSent,
      limits: run.limits,
      createdAt: run.createdAt,
      completedAt: run.completedAt,
    },
    checks: RFI_REVIEW_CHECKS.map((c) => {
      const plan = run.checkPlan[c.id];
      const result = results[c.id];
      return {
        id: c.id,
        section: c.sourceSection,
        number: c.sourceQuestionNumber,
        originalQuestion: c.originalQuestion,
        selected: plan?.selected ?? false,
        applicability: plan?.applicability ?? "unknown",
        outcome: result?.outcome ?? (plan?.selected ? (run.status === "ready" || run.status === "partial" ? "failed" : "not run yet") : "not_selected"),
        reason: result?.reason ?? plan?.reason ?? "",
        candidates: result?.candidates ?? 0,
        gaps: result?.gaps ?? [],
      };
    }),
    scope: {
      pages: run.pages.map((p) => ({ sheetNumber: p.sheetNumber, pageNumber: p.combinedPageNumber ?? p.pageNumber, role: p.role, reason: p.reason, rendered: p.visual })),
      omittedPages: run.coverage.omittedPages,
      unresolvedReferences: run.coverage.unresolvedReferences,
      omissions: run.coverage.omissions,
      searchLog: run.coverage.searchLog,
    },
    candidates: shown.map((c) => ({
      id: c.id,
      state: STATE[c.status] ?? c.status,
      rfiNumber: c.rfiNumber,
      checkType: c.checkType,
      confidence: c.confidence,
      priority: c.priority,
      subject: c.subject,
      question: c.question,
      whyFlagged: c.reasoning,
      wordedBy: c.questionSource === "model" ? "AI (checked against the cited evidence)" : "the check's template",
      evidence: (Array.isArray(c.evidence) ? c.evidence : []).map((raw) => {
        const e = (raw ?? {}) as Record<string, unknown>;
        const id = typeof e.evidenceId === "string" ? e.evidenceId : null;
        return {
          evidenceId: id,
          sheetNumber: typeof e.sheetNumber === "string" ? e.sheetNumber : null,
          pageNumber: typeof e.combinedPageNumber === "number" ? e.combinedPageNumber : typeof e.pageNumber === "number" ? e.pageNumber : null,
          kind: typeof e.kind === "string" ? e.kind : null,
          quote: typeof e.quote === "string" ? e.quote : "",
          observation: typeof e.observation === "string" ? e.observation : null,
          imageKey: id ? (images.get(id) ?? null) : null,
        };
      }),
    })),
    inventory: run.inventory,
    cost: {
      estimatedLowUsd: run.estimate?.costLowUsd ?? null,
      estimatedHighUsd: run.estimate?.costHighUsd ?? null,
      actualUsd: run.usage?.total?.costUsd ?? null,
      actualTokens: run.usage?.total ? run.usage.total.inputTokens + run.usage.total.outputTokens : null,
      planningUsd: run.estimate?.planningCostUsd ?? null,
      pricingVersion: run.estimate?.pricingVersion ?? null,
    },
    notes: run.notes,
  };
}

// --- PDF --------------------------------------------------------------------------

/** Standard PDF fonts encode WinAnsi only; anything else would throw mid-render. */
export function winAnsi(text: string): string {
  return text
    .replace(/[‘’]/g, "'")
    .replace(/[“”]/g, '"')
    .replace(/[–—]/g, "-")
    .replace(/…/g, "...")
    .replace(/×/g, "x")
    .replace(/[^\x09\x0A\x0D\x20-\x7E -ÿ]/g, "?");
}

/** Greedy word wrap at a measured width; a word longer than the line is cut. */
export function wrap(text: string, width: number, measure: (s: string) => number): string[] {
  const lines: string[] = [];
  for (const paragraph of winAnsi(text).split("\n")) {
    let line = "";
    for (let word of paragraph.split(/\s+/).filter(Boolean)) {
      while (measure(word) > width) {
        let cut = word.length - 1;
        while (cut > 1 && measure(word.slice(0, cut)) > width) cut--;
        if (line) lines.push(line);
        lines.push(word.slice(0, cut));
        line = "";
        word = word.slice(cut);
      }
      const next = line ? `${line} ${word}` : word;
      if (measure(next) > width && line) {
        lines.push(line);
        line = word;
      } else {
        line = next;
      }
    }
    lines.push(line);
  }
  return lines;
}

/** Most pictures a report embeds: a report is read, not archived — the
 * evidence ids point at the rest. */
export const MAX_REPORT_IMAGES = 40;

class Writer {
  page!: PDFPage;
  y = 0;
  readonly width = 612;
  readonly height = 792;
  readonly margin = 48;
  constructor(
    readonly doc: PDFDocument,
    readonly font: PDFFont,
    readonly bold: PDFFont,
  ) {
    this.newPage();
  }
  newPage() {
    this.page = this.doc.addPage([this.width, this.height]);
    this.y = this.height - this.margin;
  }
  room(h: number) {
    if (this.y - h < this.margin) this.newPage();
  }
  text(s: string, size = 9.5, font: PDFFont = this.font, indent = 0, color = rgb(0.1, 0.1, 0.1)) {
    const lines = wrap(s, this.width - 2 * this.margin - indent, (t) => font.widthOfTextAtSize(t, size));
    for (const line of lines) {
      this.room(size * 1.35);
      this.page.drawText(line, { x: this.margin + indent, y: this.y - size, size, font, color });
      this.y -= size * 1.35;
    }
  }
  heading(s: string, size = 13) {
    this.room(size * 2.2);
    this.y -= size * 0.6;
    this.text(s, size, this.bold);
    this.y -= 2;
  }
  gap(h = 6) {
    this.y -= h;
  }
}

const money = (v: number | null) => (v === null ? "unknown" : `$${v.toFixed(v < 1 ? 3 : 2)}`);

export async function renderReviewReportPdf(
  report: ReviewReport,
  imageBytes: (key: string) => Promise<Uint8Array | null>,
): Promise<Uint8Array> {
  const doc = await PDFDocument.create();
  doc.setTitle(report.title);
  doc.setProducer("Construction Drawing AI Platform");
  const w = new Writer(doc, await doc.embedFont(StandardFonts.Helvetica), await doc.embedFont(StandardFonts.HelveticaBold));

  w.heading(report.title, 16);
  for (const line of report.disclaimer) w.text(line, 9.5, w.bold, 0, rgb(0.6, 0.1, 0.1));
  w.gap();
  const r = report.run;
  w.text(`Target: ${r.target}`);
  w.text(`Status: ${r.status}   Depth: ${r.depth}   Question catalogue: ${r.catalogueVersion}`);
  w.text(`Model: ${r.provider} / ${r.model}   Thinking asked: ${r.thinkingRequested}${r.thinkingSent.length ? `   sent: ${r.thinkingSent.join(", ")}` : ""}`);
  w.text(`Limits: ${r.limits.maxInputTokens.toLocaleString()} input tokens per call, ${r.limits.maxTotalTokens.toLocaleString()} per run, ${r.limits.maxBatches} discovery call(s)${r.limits.maxThinkingTokens ? `, thinking up to ${r.limits.maxThinkingTokens.toLocaleString()}` : ""}`);
  w.text(`Planned ${r.createdAt}${r.completedAt ? `, finished ${r.completedAt}` : ""}. Report made ${report.generatedAt}.`);
  const c = report.cost;
  w.text(`Cost: estimated ${money(c.estimatedLowUsd)} to ${money(c.estimatedHighUsd)}; actual ${money(c.actualUsd)}${c.actualTokens !== null ? ` (${c.actualTokens.toLocaleString()} tokens)` : ""}; planning ${money(c.planningUsd)}. Prices: ${c.pricingVersion ?? "unknown"}.`);

  w.heading("The 16 original questions");
  for (const check of report.checks) {
    w.text(`${check.id} (${check.section} Q${check.number}) — ${check.outcome.replace(/_/g, " ")}${check.candidates ? `, ${check.candidates} candidate(s)` : ""}`, 9.5, w.bold);
    w.text(check.originalQuestion, 8.5, w.font, 12, rgb(0.35, 0.35, 0.35));
    if (check.reason) w.text(check.reason, 8.5, w.font, 12);
    for (const gap of check.gaps) w.text(`Gap: ${gap}`, 8.5, w.font, 12);
    w.gap(3);
  }

  w.heading(report.kind === "draft" ? `Candidates (${report.candidates.length})` : `Accepted RFIs (${report.candidates.length})`);
  if (!report.candidates.length) w.text(report.kind === "draft" ? "The review proposed no candidates." : "No candidate from this review has been accepted yet.");
  let embedded = 0;
  for (const [i, cand] of report.candidates.entries()) {
    w.gap(4);
    w.text(`${i + 1}. ${cand.subject}`, 11, w.bold);
    w.text(`${cand.state}${cand.rfiNumber !== null ? ` as RFI #${cand.rfiNumber}` : ""} · ${cand.checkType} · confidence ${cand.confidence}${cand.priority ? ` · priority ${cand.priority}` : ""} · worded by ${cand.wordedBy}`, 8.5, w.font, 0, rgb(0.35, 0.35, 0.35));
    w.text(cand.question);
    if (cand.whyFlagged) w.text(`Why flagged: ${cand.whyFlagged}`, 9, w.font, 0, rgb(0.25, 0.25, 0.25));
    for (const ev of cand.evidence) {
      const where = `${ev.sheetNumber ?? "page"}${ev.pageNumber !== null ? ` p.${ev.pageNumber}` : ""}`;
      w.text(`[${ev.evidenceId ?? "?"}] ${where}: ${ev.quote}${ev.observation ? ` — ${ev.observation}` : ""}`, 8.5, w.font, 12);
      if (ev.imageKey && embedded < MAX_REPORT_IMAGES) {
        const bytes = await imageBytes(ev.imageKey);
        if (bytes) {
          try {
            const img = await doc.embedPng(bytes);
            const maxW = w.width - 2 * w.margin - 12;
            const scale = Math.min(maxW / img.width, 260 / img.height, 1);
            const h = img.height * scale;
            w.room(h + 6);
            w.page.drawImage(img, { x: w.margin + 12, y: w.y - h, width: img.width * scale, height: h });
            w.y -= h + 6;
            embedded++;
          } catch {
            w.text("(evidence picture could not be read)", 8, w.font, 12);
          }
        }
      }
    }
  }

  w.heading("Coverage — what was and was not looked at");
  for (const p of report.scope.pages) w.text(`${p.sheetNumber ?? "?"} (page ${p.pageNumber}) — ${p.role}${p.rendered ? ", rendered" : ", text only"}: ${p.reason}`, 8.5);
  if (report.scope.omittedPages.length) {
    w.text("Left out:", 9.5, w.bold);
    for (const p of report.scope.omittedPages) w.text(`${p.sheetNumber ?? "?"}: ${p.reason}`, 8.5, w.font, 12);
  }
  if (report.scope.unresolvedReferences.length) w.text(`Referenced but not in the project: ${report.scope.unresolvedReferences.join(", ")}`, 8.5);
  for (const o of report.scope.omissions) w.text(`Not completed: ${o}`, 8.5);
  if (report.notes.length) {
    w.heading("Notes");
    for (const n of report.notes) w.text(n, 8.5);
  }

  const pages = doc.getPages();
  pages.forEach((page, i) =>
    page.drawText(winAnsi(`${report.title} · run ${report.run.id.slice(0, 8)} · page ${i + 1} of ${pages.length}`), {
      x: 48,
      y: 24,
      size: 7.5,
      font: w.font,
      color: rgb(0.5, 0.5, 0.5),
    }),
  );
  return doc.save();
}
