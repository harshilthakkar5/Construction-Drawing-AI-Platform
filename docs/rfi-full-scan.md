# Full AI scan

The third way the RFIs tab finds problems, beside **Project checks** (code only,
text only) and the **Targeted review** (one sheet, one element, or a few sheets
a person names). The full scan answers a different question: *"show the AI every
part of the drawings that should agree, and tell me where they do not"* — on a
set of hundreds of pages.

Status: **built, accuracy NOT measured.** Every test uses a stub model. It ships
behind `RFI_FULL_SCAN=beta` (the default), and the screen says "Beta — accuracy
not measured yet" on the scan and on every finding it makes. `on` is for after
`benchmarks/rfi_eval.py --fullscan` passes on real issued RFIs.

## Why it is not "send the PDF to the AI"

A 500 MB set is thousands of pages and the model sees each page at a few
hundred pixels. Asked "what is wrong with these drawings" it writes a fluent,
confident list with nothing to tell the real items from the invented ones. So
the work is split the same way as everywhere else in the RFI features: **code
decides WHAT to compare and WHERE; the model only says whether two things that
code lined up disagree; code decides whether its answer may become a finding.**

## The six phases

| Phase | What | Where | Model call? |
|---|---|---|---|
| 1. Page catalogue | Per page: sheet kind (plan, enlarged plan, section, …), level, printed scales, grid. Stored on `pages` (`sheetKind`, `level`, `scales`, `gridSummary`, `factsVersion`), so a second plan re-reads only new pages | `workers/src/sheet_facts.py` | no |
| 2. Pairs + tiles + estimate | Pair sheets that should agree, line them up, cut matching windows ("tiles"), count tokens | `workers/src/fullscan_plan.py`, `fullscan.py` | no |
| 3. First look | Each tile pair (two images + the words printed inside each) → "where do A and B disagree?" — direct, or the provider's batch API at half price | `workers/src/fullscan_run.py` | yes |
| 4. Close look + rules | Each possible problem re-rendered close up on both sheets → keep/reject and an RFI wording; code rules; saved as a candidate | `fullscan_run.py` | yes |
| 5. Measure | `benchmarks/rfi_eval.py --fullscan <id> --case a,b,c` against issued and rejected RFIs | `benchmarks/` | — |
| 6. Button | Plan → see pairs, left-out reasons, price → set budget → Start; progress, stop, resume | `apps/web/src/components/RfiFullScan.tsx`, `apps/api/src/routes/rfiFullScan.ts` | — |

### Pairing rules (phase 2)

Each comes from a draft the client rejected:

- **Same level, two disciplines, a common printed scale** (`same_level`). Never
  two levels (A3.03 L4 against A3.05 L6 reported setback columns "missing").
  A page whose titles name no single level is left out and listed.
- **Enlarged plan with its overall plan** (`enlarged`): an `enlarged_plan`, or a
  plan of the same discipline drawn at least twice as large, over the plan of
  the same level.
- **Never a sheet with several views** — a grid label bubbled twice more than
  60pt apart (S1.102 against two details on A3.36).
- **Grid NAMES are never used to line sheets up.** Two disciplines name lines
  differently (that is RFI 002), so alignment is by line POSITIONS
  (`rfi_grid.align`, falling back to primary lines when secondaries crowd an
  axis), or for an enlarged plan by the columns both draw (`plan_match`), with
  the window bounded by the detail's own ink so it never runs into the title
  block.

Everything left out is listed on the plan screen with its reason. "Plan with no
other sheet of its level to compare with" is the common one: the full scan
needs both disciplines' plans of a level in the project.

### What the model may and may not do (phases 3–4)

- It is told the two images are the same area, lined up by the system, and
  never to second-guess that. It reports only elements both drawings would show
  and that disagree; anything cut off at a window edge, drafting style, items one
  discipline does not draw, and differences a note explains are not problems.
- First-look issues need a valid box on each image, a known question id
  (`C01`… from the 16-question catalogue) and what each sheet shows; at most four
  per tile.
- The close look must keep or reject. A kept finding is still rejected by code
  when: the two sheets are not one level; or its subject/question names an
  identifier or ANY digit run that the words printed in the two close-ups and
  the sheet names do not carry (`rfi_review.grounded`). The model's own
  first-look description is deliberately NOT grounding — it is the claim being
  checked.
- A finding is a CANDIDATE (`origin = full_scan`, `fullScanId`), never an RFI,
  until a person accepts it. One element on one pair of sheets is one
  fingerprint, whichever tile saw it; a finding already decided (accepted,
  dismissed, or another tool's) is never overwritten and is reported as "found
  again" in the notes.

### Money

- Planning spends nothing. The worker counts TOKENS; the API prices them on the
  model the person picks, batch and direct (`rfiFullScanRules.priceEstimate`).
  Unknown prices stay unknown.
- Start requires a budget. Dollars become a token ceiling at the FULL rate of
  the estimate's own input/output mix, so the ceiling cannot overrun the budget
  even if the batch half price is not applied.
- The ceiling is read from the usage ledger (`usage_events."reviewRunId"` holds
  the scan id) and counts calls still IN FLIGHT, so it is checked before each
  call or each batch wave and never crossed. A batch wave is sized to the room
  left before it is sent: a batch cannot be stopped half way.
- Batch rows are recorded under the model's plain name with stage
  `discovery_batch`; the API prices that stage at half.

### Resume, stop, stale

Every tile is saved `done` with its issues as its answer lands, and every
issue's verdict is written back into its tile, so a stopped scan — budget
reached, failed batch, dead worker (no heartbeat for 10 minutes reads as
failed) — resumes exactly where it stopped and never asks the same question
twice. A cancel stops between calls. The person who started it losing access
stops it. A document added, replaced, removed or excluded since the plan makes
the scan `stale` before its first call: the tiles point at pages that moved.

## What is NOT claimed

- **Accuracy.** All tests use stub models; they prove what the code does with
  any reply. Only `rfi_eval.py --fullscan` on a real set with real issued RFIs
  measures it.
- `rfi_eval`'s `rfi-015` case accepts any C01 finding with evidence on A3.27 and
  A3.35. The stub model's meaningless "column at the drain" finding scored a HIT
  on it. A hit on that case must be read by a person; and the earlier analysis
  found those two sheets AGREE (the RFI was about the level below), so a full
  scan finding there is more likely a false positive than a hit.
- Sections, elevations, details and schedules are catalogued but not yet paired:
  phase 2 pairs plans only.
- Only tile windows' close-up crops are stored (as the finding's evidence
  pictures). The tile images themselves are not stored; they are reproducible
  from the immutable PDF and the stored window.

## Running it

```
# worker: consumes rfi-full-scan (plan and run)
cd workers && python src/worker.py
# score a finished scan
DATABASE_URL=… python benchmarks/rfi_eval.py --fullscan <scan id> --case rfi-002,fp-a303-a305-levels,fp-s1102-a336-scales
```

Settings: `RFI_FULL_SCAN`, `FULL_SCAN_*`, `RFI_FULL_SCAN_CONCURRENCY` in
`docs/environment-variables.md`. Provider and model default to the targeted
review's (`RFI_PROVIDER`, `RFI_REVIEW_MODEL`) and are chosen per scan.
