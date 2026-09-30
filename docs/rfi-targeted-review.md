# Targeted RFI review

"Find RFIs in drawings" has two modes on the RFIs tab:

- **Project checks** (`rfi-scan`, `workers/src/rfi_scan.py`): deterministic checks over the whole
  project. The checks decide; a cheap model only words each finding.
- **Targeted review** (`rfi-review`, `workers/src/rfi_review.py`): a person names ONE sheet, or
  2–4 sheets that should agree, and a vision model reads those drawings for coordination
  problems. This document is about this mode.

Both write to the same `rfi_candidates` table and the same "Needs your review" list. A candidate
is never an issued RFI: it gets a number only when a person accepts it.

## The flow

```
POST /projects/:id/rfis/reviews/plan      (API, no model call)
  resolve each named sheet → pages       exact sheetNumber, else a whole token of the title-block text;
                                          two pages claiming one sheet are BOTH kept and reported
  select checks                           auto: G01 + C01 always, C02 when the target text names a core
                                          or shear wall; custom: exactly what was ticked
  retrieve related pages                  retrieveChunkIds (the chat's own hybrid retrieval), one query
                                          per check — never re-implemented, never in Python
  rank + cap                              rfiReviewRules.rankScope: named sheets are never dropped; the
                                          named sheets' gridmarks chunks are always kept; related pages
                                          fill the depth's chunk and image budget in rank order
  hash + price                            scopeHash, estimateReview (priced at the model the worker runs)
  → rfi_review_runs row, status planned

POST /:runId/start                        re-checks the hash and that every document and chunk in the
                                          scope is still live (else 409 + "plan again"); enqueues
                                          rfi-review {runId}
worker: rfi_review.run(runId)             reads the STORED scope — the pages a person approved,
                                          nothing more
```

The plan screen shows the pages, why each check was chosen, the image count and the estimated
cost. Removing a page re-plans without it. Nothing is spent before Start.

## What the worker does

1. **Evidence.** Every scoped chunk becomes a text evidence item; every visual page gets a
   whole-sheet overview and close-ups around its best-ranked chunks, rendered from the ORIGINAL
   PDF. Each item gets a server-made id (`ev1`, `ev2`, …). Chunk boxes live in the page's
   unrotated space and rendering in the displayed one, so each crop goes through
   `page.rotation_matrix` (tested at 0/90/180/270).
2. **Exact geometry.** G01 runs the project scan's grid comparison (`rfi_grid`) on the scoped
   pages. It only compares sheets of DIFFERENT disciplines, so a one-sheet review whose related
   pages are all structural compares nothing; the run's note (`grid_scope_note`) names every
   sheet with a grid and every pair compared, and says so when none was, instead of a bare "0". Its findings keep the scan's fingerprint, so one grid disagreement is one candidate
   whichever mode found it, and the model is told not to report it again.
3. **Discovery** (images + text): what each source SHOWS, cited by evidence id. No verdicts.
4. **Reasoning** (text only): which observations are a conflict, a missing value, a plan/schedule
   inconsistency, or an ambiguity.
5. **Verification** (only the evidence each problem cites): keep or reject, and word the kept ones.
6. **The code's rules**, which the model is not trusted to apply to itself (`rfi_review.guard`):
   - an evidence id the server did not issue is dropped at parse time;
   - a finding supported only by a model's description is rejected;
   - a conflict must cite two different pages;
   - the question may not contain an identifier or any digit run that neither the cited evidence
     nor the observations made from it contain (digit runs, not just free-standing numbers:
     `W14x90` would otherwise pass unchecked).
7. **Save** as `origin = 'targeted_review'`. The fingerprint is the check, the kind, the pages and
   the identifiers — never the wording — and the upsert never touches a candidate someone has
   accepted or dismissed, or one the project scan owns.

A call that FAILS (a 503 "high demand", a 429, a timeout) is asked again after 5s and 20s
(`CALL_RETRY_DELAYS`) — one busy moment at Gemini once failed a whole review at the reasoning
stage and threw away the discovery call already paid for. A reply that is not usable JSON gets
one salvage retry. After that the run fails and says at which stage. It is never turned into "no RFIs found", which would read as a clean bill of health.
Cancel is checked between stages; a heartbeat lets the API report a dead worker's run as failed.

## Column overlay — laying one sheet over another (C01's exact half, and the picture pairs)

Built from the client's RFI 015 ("Discrepancies in dimensions and column location", A3.27 against
A3.35), which the first slice read and found nothing in, for reasons nothing could see: both sheets
are architectural (the grid check compares only across disciplines), A3.35 has no grid bubbles, the
two are drawn at 1/8" and 1/4", and each was shown to the model whole at ~44 DPI where a 2'x1' column
is a speck. What the two sheets DO share is the columns, so the columns line them up
(`workers/src/plan_match.py`):

1. **Scales** — every `1/4" = 1'-0"` on each sheet, read line by line (joined into one string, a date
   on the line before turned `1/4"` into 27 1/4"). The ratios between them are the candidates.
2. **Elements** — a filled rectangle of column size (8" to 4') with the concrete stipple drawn inside.
   At 1/8" a 2'x1' column holds only two dots, so one or two also count when the box carries its own
   outline. Three look-alikes are refused, each once reported as a missing column on the real sheets:
   a box with a word in it (the white mask behind a dimension), a box touching another short stippled
   box (a wall corner drawn as two rectangles), and a box of another fill than the columns that lined
   up (a grey pad).
3. **Details** — the enlarged sheet is split into its drawings (connected ink), because each detail is
   its own window onto the overall plan with its own offset. Named "detail 1" off the sheet's own title.
4. **Alignment** — every size-matched pair votes for a translation; the winner needs 3 columns and must
   beat the runner-up by 2, because a regular bay lines up with itself one bay over.

`rfi_columns.column_mismatches` (A) then reports, per aligned detail, a column one sheet shows and
the other does not, one moved, or one resized — never a size difference at the detail's edge (the
window may cut the column off), and never when differences outnumber matches (the alignment is
suspect, and the note says so). `rfi_columns.pair_windows` (B) cuts the SAME area out of both sheets,
differences first, as labelled image pairs ("Pair 1 of 4, first half: A3.35 detail 1 (1/4")").
`RFI_REVIEW_DEPTHS.pairWindows` (4) caps them and the estimate prices them.

**What RFI 015 turned out to be.** Laid over each other, A3.35 and A3.27 agree: 9 columns in two
details, every one where the other sheet has it. The green boxes in the RFI are the author's
annotations of where the LEVEL BELOW puts its columns. So the check reports nothing for that pair,
correctly, and the RFI becomes findable with the level-13 plan in the project: the same overlay at
a 1:1 ratio reports level-14 columns that do not stack (confidence capped at medium, since a column
that does not stack may be an intended transfer).

## Trust order

`text` (the drawing's own words) > `gridmarks` (measured geometry) > `page`/`crop` images (the
drawing itself) > `description` (another model's account). Summaries are never evidence.

## Settings

`RFI_PROVIDER` picks the provider (shared with the scan). `RFI_REVIEW_MODEL` /
`RFI_REVIEW_GEMINI_MODEL` pick the model — the vision tier, because this reads small print — and
are mirrored in `apps/api/src/llm.ts` for the estimate (`test_rfi_review` fails on a drift).
The plan screen's Low / Medium / High goes out as the stage thinking setting through
`llm.stage_thinking`'s vocabulary; what was really sent is stored per run. See `.env.example`.

## Not in this first slice

Only the `sheet` and `compare` targets, only checks G01 / C01 / C02, only the `standard` depth.
Area, discipline and free-text targets, the rest of the check catalogue, and deeper scopes come
next, on the same run table and the same three stages.

## Benchmark

`benchmarks/rfi_eval.py --run <runId>` scores a run against `benchmarks/rfi_eval_cases.json`:
RFIs a person really issued, used ONLY as the expected output. RFI 002 ("confirm the grid
layout", S2.105 against A3.01) is found by the exact grid comparison on the client's sheets.
Candidates matching no case are listed and never scored as wrong.
