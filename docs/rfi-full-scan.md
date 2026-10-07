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

Everything left out is listed on the plan screen with its reason, and when
nothing pairs the red note itself names the three biggest reasons with counts
(`no_pairs_note`). An unpaired plan with a level says which of three things
stopped it, because each has a different fix: no discipline read from its sheet
number (mark the title-block region), no plan of another discipline on its
level (upload it), or no printed scale in common.

**Levels are read in the words drawing sets use** (`sheet_facts.level_in`,
`FACTS_VERSION` 3). Only "LEVEL n" was understood at first, and a 103-page client
set read a level on 11 pages and paired NOTHING. "FIRST FLOOR", "2ND FLOOR" and
"FLOOR 2" now read as "LEVEL n" — first floor is level 1 under both the US and
UK conventions. A floor with a NAME (ground, basement n, mezzanine, roof, lower
or upper level) reads as that name and pairs only with the same name: ground is
level 1 in the US and level 0 in the UK, and a wrong guess pairs two different
floors, which is exactly the false RFI the level rule exists to stop.

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

## The third real run — read from its diagnostic export, 6 Oct 2026

The first run with `RFI_DIAGNOSTICS=on`. 498 first looks across 63 sheet pairs, 20
possible problems, 5 rejected by code before the close look, 13 rejected on it, 2
kept — one then rejected by a rule, one matching a finding dismissed earlier. The
screen said "0 findings". Every model response ended normally; the empty result
was the application's, in five places:

| What the export showed | Fix |
|---|---|
| A box in MIXED units, `[0.785, 575, 0.835, 606]`, was divided by 1000 in all four numbers. The close-up landed at the image's left edge, found C-13, and rejected the C-25 candidate for a mark it was never shown. | `read_box` refuses mixed units instead of guessing. The first look is asked ONCE to restate the box (`repair_boxes`, only with room under the ceiling); a problem still unplaceable is kept in the tile's `dropped` list and counted, never silently lost. |
| "0 findings" hid a kept finding that matched one dismissed earlier, and every other count. | `rfi_full_scans.summary` (`scan_summary`): new findings, findings already on file and where, rejected, undecided, unplaceable, areas by verdict, pages compared of pages read. The RFIs tab shows it under the count. A dismissed match is reported, never restored. |
| The prompt said "if you are not sure, return no issue" and "trust that alignment and never second-guess it", so unsure and misaligned areas read as agreement. | The first look answers `status`: `agree` / `issues` / `unclear` / `misaligned` (stored per tile as `outcome`, a reply without one is `unstated`, never assumed to agree). The close look may answer `unclear`. Unsure still never becomes an RFI — it is counted as a gap. The "a few inches" tolerance is now the 3 in the code measures columns with. |
| Two C-1 candidates were rejected as "38 ft" and "40 ft apart". Their boxes were ONE box written two ways — `[409, 731, 434, 760]` on image A and `[731, 409, 762, 434]` on image B. The model was `gemini-3.6-flash`, whose trained box order is `[ymin, xmin, ymax, xmax]`; the prompt's positional `[x0, y0, x1, y1]` invited the mix. | The prompt now asks for named edges, `{"left", "top", "right", "bottom"}` (lists are still read). A pair that is its own transpose (`transposed_pair`) is dropped as `invalid_location` and asked again, since which half is right cannot be known. Replaying all 498 real first-look replies through the new parser catches exactly these three bad locations and reads the other 17 issues unchanged. |
| Two dimension candidates were rejected as "9–10 ft apart". A dimension string sits on its dimension line, which two disciplines draw at different distances outside the plan. | `boxes_apart` no longer judges a dimension claim (`is_dimension_claim`). |
| A finding about the segments 2–2.3 and 3.5–3.7 was rejected because the overall 2–3.7 agreed. | `grid_spacing_agrees` rejects only when EVERY pair the wording names (`named_pairs`, including dash segments) is measured on both sheets and agrees. |

The grid-dimension finding (A3.34 against A3.13) is still rejected under the stricter rule, and on
the measured grids that is right: each pair it names — 2–3.7, 3.7–3.5 and 2.3–2 — is drawn the same
distance apart on both sheets, so the printed 6'-1" and 10'-1" measure to something else (the 6'-1"
was measured ending 2 1/2" past grid 3.5 on the first run). Worth one human look: if A3.34's 10'-1"
really does run grid to grid, its TEXT disagrees with its own drawing, which no rule here checks.
C-26 ("round column, square cap") went to the close look four times, against S2.102, S2.302 and
S2.402, and was rejected each time — the prompt already names that shape; the first look at
`minimal` thinking ignored it. Correct, but four paid calls.

Not fixed, and not a code defect: the one kept C-10.S finding looks like a misreading
(the structural close-up prints a 7½" offset the verdict says is not there). A model's
"keep" is not proof. And coverage: the plan compared 48 of 423 pages; the rest are
listed in the plan with their reasons (no level read, no alignment, several views on
one sheet). "Full scan" means every PAIR the planner could line up, not every page.

These are stub-model tests (`test_fullscan_run.py`); no real replay has measured them.

### A 103-page set that read nothing, then paired nothing (JETRIGHT, 6 Oct 2026)

The first plan said "Read 0 pages … 103 kept out". `live_pages` keeps out three kinds
of page — an old revision, a document switched off for RFI analysis, and a document
still processing — and the note lumped them together, so the person could not tell
which one to fix. The plan was most likely made right after upload, before ingest had
finished. `sheet_facts.summary` now returns `excludedBy`, and
`fullscan_plan.kept_out_note` says for each reason what to do: wait for the Docs tab to
show "completed" and plan again, switch the document back on, or nothing (old revisions
are meant to be left out). For a document switched off for RFI analysis it names the
file and its stored reason ("by a person", "filename", "form text"), since a drawing set
can be off without looking like an RFI. With no page read, the "nothing could be paired"
box is not shown: it only restated the first one.

With the pages read, the set still paired nothing. There were two reading bugs:

- **Titles.** `title_lines` takes the second-largest text size on the sheet as the
  title size. On these sheets that size was a big detail number or a logo letter, so
  the real drawing titles ("LEVEL 1 FLOOR PLAN") were too small to count, and no level
  was read. Text shorter than `REFERENCE_MIN_CHARS` (4) no longer sets the reference
  size.
- **Grid bubbles.** These sheets draw each bubble as a ring of about 24 short straight
  segments (about 3.5 pt each, radius about 13 pt) over a white mask, not as a curve,
  so `styled_systems` found no grid. `grid.ring_around` accepts a ring only when the
  pieces are equal in length, sit on one radius around the label, cover at least 10 of
  12 thirty-degree sectors and leave the inside empty. Electrical fixture clutter
  failed every earlier, looser version of this test. Rings are used only on a page
  with no curve bubbles. `FACTS_VERSION` is now 4 and `GRID_CACHE_VERSION` 6, so every
  page is re-read.

After these fixes the set gave 15 candidate pairs, and none lined up: the electrical
(E) and life-safety (G) plans draw no grid at all (the 1–12 and A–K around their
frame are zone markers), and the roof plumbing plan P1.04 shows only 3 grid lines on
one axis where `rfi_grid.align` needs 4.

**Lining up by walls (`workers/src/wall_match.py`).** Two earlier attempts failed on
this set and were not shipped: raster ink matching put "OFFICE 104" in different places
on the two sheets, and room labels as anchors agreed in only 1–2 of 9 votes (the
electrical drafter moves the labels). What does hold is that an engineer's plan is
drawn over the architect's floor plan as an exact copy. So every long horizontal and
vertical line of both sheets is read, and each pair of EQUAL-LENGTH lines votes for the
shift between them. A copied background gives one sharp peak. It is accepted only when:

- at least 60 lines land within 0.6 pt (`MIN_MATCHED`, `MATCH_TOL`);
- they are at least 15% of the smaller sheet's lines (`MIN_SHARE`);
- the best shift beats the best OTHER shift by 4× (`MIN_RATIO`). A regular wall
  module lines up with itself shifted one bay, as a grid does.

The shared border and title block vote for a zero shift on any two sheets; on this
set they are 29 lines, half the minimum. It runs only for a same-level pair the grid
could not line up, translation only, as the grid does.

Measured on this set (checked by overlaying the two sheets; walls drawn on both come
out black):

| | pairs | result |
|---|---|---|
| Should line up (same floor, two disciplines) | 13 | 12 lined up, 670–965 lines matching, next-best shift 31–148 |
| Should NOT line up (different floors, a site plan, a schedule sheet, roof against level 1) | 7 | 7 refused |

The one miss is the roof pair A3.01 / P1.04. The plan now has 18 pairs and 114 areas,
where it had 0. A stub-model run over all of them completes; evidence boxes land on
the second sheet exactly at the measured shift. This is NOT a measurement of finding
accuracy, only of the line-up.

The same set also left E4.01 and E4.02 out as "sheet with several views": one stray
bubble on one axis and two on the other were read as a repeated grid label. That rule
now needs a real grid (2 or more lines on each axis).

Other pages left out of this set, with the reason shown in the plan: 28 plans with no
level in their title (mostly civil), 3 sheets with several views, M1.02 "GROUND FLOOR"
(the mechanical and plumbing sheets say GROUND and the architectural and electrical
sheets say LEVEL 1; these are deliberately not treated as the same floor), and A5.31,
which shares no printed scale with any other sheet.

### A second reviewer's comparison (JETRIGHT, 7 Oct 2026)

A scan of the JETRIGHT set (Gemini 3.6 Flash, 114 areas) returned **0 findings**: every
area came back "agree", and the diagnostic export shows it was right to: the sheets line
up and their walls agree. A separate package from the same set held one AI RFI, "mezzanine
floor edge uniform on E2.02, stepped on P1.03". A second reviewer (ChatGPT Astra) compared
that package with its own coordination review of the set and rejected it:

- both highlighted lines were pale BACKGROUND linework, the architect's plan that each
  consultant copied, compared as if it were design;
- neither sheet labels a floor edge, so the RFI named an element nobody drew as one;
- it read "different elevations" off lines moving across a PLAN;
- the governing sheet (the architectural mezzanine plan) was never checked.

What changed in the full scan:

| Rule | Where |
|---|---|
| A pair needs a governing sheet (architectural, structural or interiors) or two sheets of one discipline. E, P and G plans are each compared with the architectural or structural plan of their level, never with each other. A level with no governing plan says so in "Left out". | `fullscan_plan.governs` |
| Both looks are told that pale or halftone background linework proves nothing, that a plan shows no heights, and to name an element only when a label says what it is. | prompts, `PROMPT_VERSION` fullscan-2026-10-07.1 |
| A kept claim of a height, elevation or level change is rejected unless a close-up prints EL., T.O., SLOPE, STEP, SECTION or similar. | `fullscan_run.level_claim_unsupported` |
| The package cover reads the issue line and date off each finding sheet's title block ("03/31/26 95% MNAA-AIR REVIEW SUBMITTAL (NOT FOR CONSTRUCTION)"), not "Revision: Unknown". | `rfi_package.title_block_issue` |

Astra's own six RFIs were mostly not pictures at all. They were ENTITY matches: one tag
with two values, a tag with no schedule row, one grid with two names. Code does that
better than a vision model, so three of them are now **Project checks** (run "Find RFIs
in drawings", not the full scan):

| Astra | Now | How |
|---|---|---|
| JR-001 F6.0 footing tag with no schedule row | found | `unscheduled_mark`: a schedule heading with its rows in separate text blocks, dotted marks when the schedule writes them that way |
| JR-002 RTU-3 80 MBH (P0.02, P1.03) vs 100 MBH (P1.04) | found | new `tag_value_conflict` |
| JR-004 A1.01's lettered grid renamed one step against S2.01 (U = T …) | found | `grid_mismatch`, after a bin-edge bug in `rfi_grid.align` was fixed |
| JR-006 CF-2 2 HP vs 2-1/2 HP | not found | both schedules (M0.02, E0.05) are pasted in as pictures, with no text to read |
| JR-003 oil/water separator dimensions, JR-005 shower recess depth | not found | need reading two product details, or noticing a missing dimension |

Run over every text block of the 103 pages, the Project checks return exactly those three, plus the gate
transceiver "PART NUMBER TBD" and the grout colour TBD (Astra's V-02 and V-04). It no longer
raises "T.B.C.O." (a traffic-bearing cover rating) as an open item, or insulation R-values
as railing marks. The other client's eleven sheets give the same output as before.

### When the provider refuses the account

A scan that stops on the provider's side — Gemini's `402 RESOURCE_EXHAUSTED … prepayment credits are
depleted`, Anthropic's "credit balance is too low", a quota (429) or a rejected key (401/403) — is
not a scan problem and not this scan's own dollar budget. `provider_failure_message` says which it
is and what to do, in place of the raw SDK error. Every area already checked is saved: fix the
account and press Resume. A scan keeps the provider it was planned with, so switching to another
provider means planning a new scan.

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
