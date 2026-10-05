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

## The first real run — 423 client pages, 2 Oct 2026

63 pairs, 498 tiles, Gemini in batch mode. Ten marked-up packages came back
(eight AI findings and two from Project checks), and each was checked against
the drawings: by measurement where the claim is a number, by eye otherwise.

| Finding | Verdict | Why |
|---|---|---|
| C-32 drawn across an interior wall on A3.01, along the outside wall on S2.102 | **plausible** | the one a reviewer should look at |
| C-5 (S2.106) vs wall tag W1-14 (A3.05) | wrong | C-5 is on grid D on both sheets; the cloud is the next wall, where W1-14 is a tag |
| round column (A3.22) vs square (S2.402) | wrong | S2.402 draws the same round column inside its cap |
| 6'-1" vs 5'-10" between grids 3.7 and 3.5 (A3.34 / A3.13) | wrong, **measured** | both sheets draw the lines 5'-10" apart; the 6'-1" ends 2 1/2" past 3.5 |
| 10'-1" vs 10'-0" between grids 2 and 2.3 | wrong, measured | same: 10'-0" on both, the 10'-1" starts short of 2.3 |
| SW-1.6 vs "shaft opening" (A3.25 / S2.106) | probably wrong | the same rectangle in the same place, hatched differently |
| slab edge "steps" (A3.26) vs "straight" (S2.407) | probably wrong | S2.407 also stops the slab at the shaft wall |
| C-10.S offset in its planter (A3.00 / S1.101) | doubtful | a planter box against a pier, a few inches apart |
| Project checks: mark F7 missing from a finish schedule | wrong | F7 is grid line F.7 |
| Project checks: grids 2.3 = 2.4, 1.4 = 1.5, G.9 = H | wrong | one line with two names on the structural sheets; leader bubbles |

About one AI finding in eight held up. What changed because of it:

- **Measured, not instructed:** `grid_spacing_agrees` rejects a "dimension
  between grid X and Y differs" finding when both sheets draw X and Y the same
  distance apart at their printed scales (from the catalogue's grid and scales).
- **Grid reading** (`grid.leader_target`): a bubble pushed sideways on a kinked
  leader is placed where its leader lands. Bubbles are still GROUPED by where
  they are printed; grouping by the moved position chained A3.01's dense
  secondary lines into false axes and hid RFI 002 — caught by its tests. RFI 002
  is still found, with more of its lines paired (B.5 = B1.5, 8.2 = 5) and two
  leader artefacts gone. `GRID_CACHE_VERSION` 5, `FACTS_VERSION` 2.
- **Two names, one line:** in `rfi_grid._match` the same name wins inside the
  tolerance, so a structural "2.3/2.4" line matches the architectural "2.3".
- **Schedule check:** a dotted label (F.7) is never a mark, and a schedule title
  never runs across a line break.
- **Prompts** name the shapes the wrong findings took (a label beside an element,
  the same element in another symbol or hatch, inches of drafting tolerance,
  a dimension read as grid-to-grid).
- **Benchmark:** the proven ones are `rejected` entries (`fp-full-scan-ai`,
  `fp-full-scan-project-checks`), so a run that raises them again fails.

## The second real run — 5 Oct 2026

Three packages came back, and a second AI model reviewing them called two "correct" and one "needs
verification". Measured from the drawings, none holds up as written:

| Finding | Verdict | Measured |
|---|---|---|
| C-13 "west face on the grid" on A3.05 vs centred on S2.106 | wrong | A3.05's 22x22 fill is centred on E/4 within ½"; S2.106's C-13 within 1½". The S2.106 cloud sat on an empty patch about 10 ft from C-13 |
| Round column on A3.01 vs square C-10.S on S2.102 (Room B5) | wrong | two different columns: B/1.4 on A3.01, C/2.3 on S2.102 (38 ft apart). S2.102 draws C-1 (26" DIA) round at B/1.4, and A3.01 a square column at C/2.3 — the sheets agree at both |
| 4'-0" x 4'-0" on A3.21A vs C-13 (24 x 24) on S1.101 | wrong question | same place (E/4), two elements: the 4'-0" square is the pier/pedestal (S1.101 draws an outline round its 24 x 24 column too). It compared a pier with a column |

The first two share one cause: the model put its box on image A and its box on image B round
DIFFERENT things, and the close look then rendered a close-up around EACH box — so it was shown two
different places and "confirmed" that they differ. What changed:

- **`boxes_apart`** (before any close-look call): the two windows of a tile are the same area, so one
  element sits at the same FRACTION of both images. Box centres more than 3 ft apart on the drawing
  (or 35% of the larger box) are two things, and the issue is rejected for free. Both findings above
  are rejected by it (10 ft and 38 ft).
- **One close-up area for both sheets** (`shared_box`): the union of the two boxes, so the model
  always sees the same place twice.
- **`column_position_agrees`** (before any call): a claim that a column is in a different place is
  MEASURED on both sheets — the column body nearest each box (vector fill first, then the raster
  reader, the smallest shape when one holds another), its centre against its own sheet's grid lines,
  lines matched by POSITION through the tile, never by name. Within 3" on both axes → rejected. On
  the client's C-13 it reports "on grid line E and on grid line 4" on both sheets.
- **Prompts:** a pier, pedestal, footing, pile cap or drop cap outline round a column is a different
  element; a kept finding calls two things "the same element" only when a mark on both sheets says
  so, and otherwise says "at the same location" and asks.
- **Benchmark:** `fp-full-scan-ai-2` holds all three as rejected entries.

The pier-vs-column case is still caught only by the prompt; no code measures it yet.

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

To see exactly what the model was given and returned on a scan (prompts, every
image, every reply and every rule decision), turn on `RFI_DIAGNOSTICS=on`:
see `docs/rfi-diagnostics.md`.
