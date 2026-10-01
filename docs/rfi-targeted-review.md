# Targeted RFI review

"Find RFIs in drawings" has two modes on the RFIs tab:

- **Project checks** (`rfi-scan`, `workers/src/rfi_scan.py`): deterministic checks over the whole
  project. The checks decide; a cheap model only words each finding.
- **Targeted review** (`rfi-review`, `workers/src/rfi_review.py`): a person names ONE sheet, ONE
  element (a mark such as `C-6`, optionally narrowed to a level or area), or 2–4 sheets that should
  agree, and a vision model reads those drawings against the 16 original RFI questions. This
  document is about this mode.

Both write to the same `rfi_candidates` table and the same "Needs your review" list. A candidate
is never an issued RFI: it gets a number only when a person accepts it.

## The 16 questions — one catalogue

`RFI_REVIEW_CHECKS` in `@cdip/shared` holds the 16 original questions VERBATIM (G01–G02 general,
F01–F04 foundations, FL01–FL04 floors, C01–C03 columns, B01–B03 beams), each with its section and
number, a short label, the observations it needs recorded, its comparison rules, what makes a
candidate, and whether an exact check backs it (`grid_mismatch` for G01, `column_mismatch` for C01)
or a measured aid (`column_grid_offsets` for C03). Routing, every prompt and the UI read that one
list; `packages/shared/fixtures/rfi-original-questions.json` is the golden copy both test suites
check the wording against (`rfiCatalogue.test.ts`, `test_rfi_catalogue.py`). Every run stores the
`RFI_REVIEW_CATALOGUE_VERSION` it was planned against.

Three ways to choose questions:

| Mode | What runs |
|---|---|
| `auto` ("Choose for me") | G01 and G02 always; a family when the target names it (a keyword as a whole WORD, or an element mark's shape: `C-` columns, `PC` piles, `F` footings, `WF` wall footings, `SW`/`CW` walls, `B`/`BM`/`G` beams). A target that names NO family runs all 16. Every question left out says why. |
| `all_original` ("All 16") | every question; applicability is still reported |
| `custom` | exactly the ticked ones; the rest are `not_selected` and say so |

Each question ends a run with ONE outcome: `candidate_found`, `complete_no_issue`,
`insufficient_evidence`, `not_applicable`, `failed` or `not_selected`. `complete_no_issue` is a claim
about the pages reviewed, not the project, and it is never given by a run that left evidence unread
— that run can only say `insufficient_evidence`. `not_applicable` needs evidence cited by the model
("no beams on this slab plan"); a bare claim is dropped.

## The flow

```
GET  /projects/:id/rfis/reviews/options   the catalogue, depths, limits, and the models a review can run
                                          on — per model: key present, price known, and whether a numeric
                                          thinking limit means anything to it (reviewThinkingCapability,
                                          the same version sniff as workers/src/llm.py; fixture-tested)
POST /projects/:id/rfis/reviews/plan      (API, no REVIEW model call)
  resolve the target                      sheet: exact sheetNumber, else a whole token of the title-block
                                          text; element: the mark through cdip_identifiers() — the ONE
                                          identifier definition — narrowed by level / area, ambiguity
                                          kept and reported, more than 6 sheets refused with the choices
  select questions                        rfiReviewRules.selectRfiChecks (above)
  forced pages                            sheets the target's own words REFER to (identifiers matching
                                          another page's sheet number) and the same level drawn by another
                                          discipline — added before ranking so no cap drops them; a
                                          sheet-shaped reference the project does not have is listed as
                                          unresolved
  retrieve related pages                  retrieveChunkIds (the chat's own hybrid retrieval), one query per
                                          question — never re-implemented, never in Python — with every
                                          embedding/rerank call tagged to the run (withUsageContext), so
                                          planning's own spend is read back from usage_events
  rank + cap                              named, element and forced pages are never dropped; related pages
                                          fill the depth's chunk and image budget; every page carries a
                                          REASON, and pages left out by a cap are listed with theirs
  hash + price                            scopeHash (over checks, pages, chunks, model and limits,
                                          canonicalised — Postgres JSONB reorders keys), estimateReview
                                          as a RANGE, the pricing version, the planning cost already spent
  → rfi_review_runs row, status planned, with checkPlan, limits, coverage, sourceRevisions

POST /:runId/estimate                     the same scope priced on another model; stores nothing
POST /:runId/start                        IDEMPOTENT: an Idempotency-Key header (the UI sends one per plan)
                                          names the start, a repeat returns the run it started, and
                                          planned → queued is a conditional update. Refused when the scope
                                          no longer matches its hash, a document was revised (revision
                                          number or supersede), excluded, or re-ingested, or the chosen
                                          provider has no key
worker: rfi_review.run(runId)             reads the STORED scope — the pages a person approved, nothing more
GET  /:runId/compare/:otherId             two runs' cost side by side, with caveats when target, questions,
                                          drawings or coverage differ — never a bare percentage
GET  /:runId/report.pdf|json?kind=        draft (every candidate, each marked NOT issued) or accepted
                                          (only accepted, with RFI numbers); the PDF embeds the pictures the
                                          model saw
POST /:runId/cancel
```

Depth presets (`RFI_REVIEW_DEPTHS`): quick / standard / deep set chunks, rendered pages, crops per
page, side-by-side pairs, hits per query, forced reference pages, discovery calls and a total
token ceiling. The plan also carries `maxInputTokens` (per discovery call, 20k–400k),
`maxThinkingTokens` (only on a model that takes a budget — refused otherwise, never silently
re-mapped) and the effort.

## What the worker does

1. **Checks before spending.** Stale scope (including a new revision number), and whether the
   person who started it can still see the project (`still_allowed`: owner, member, or a legacy
   ownerless project) — re-asked between stages; a revoked run is cancelled, not finished.
2. **Evidence.** Every scoped chunk becomes a text evidence item; every visual page gets a
   whole-sheet overview and close-ups around its best-ranked chunks, rendered from the ORIGINAL
   PDF with its ANNOTATIONS REMOVED (`grid.without_markup`) — a marked-up copy must not show the
   model someone's RFI. Each item gets a server-made id (`ev1`, `ev2`, …). Chunk boxes live in the
   page's unrotated space and rendering in the displayed one, so each crop goes through
   `page.rotation_matrix` (tested at 0/90/180/270). Every picture is stored
   (`reviewEvidence` object key) and every id is written to `evidenceManifest` with where it points.
3. **Exact geometry.** G01 runs the project scan's grid comparison (`rfi_grid`) on the scoped
   pages. It only compares sheets of DIFFERENT disciplines, so a one-sheet review whose related
   pages are all structural compares nothing; the run's note (`grid_scope_note`) names every
   sheet with a grid and every pair compared, and says so when none was, instead of a bare "0". Its findings keep the scan's fingerprint, so one grid disagreement is one candidate
   whichever mode found it, and the model is told not to report it again. C01's overlay is below.
4. **Aids** (`workers/src/review_aids.py`, kind `aid`, trust `derived`). C03 gets every concrete
   column's offset from its nearest grid crossing, MEASURED from the PDF at the printed scale
   ("1'-2\" right of grid line 3"); G02 gets an index of the levels and elevations the text prints,
   sheet by sheet. An aid is never a finding's only support, and its numbers are NOT grounding
   material: a question may state an offset only if a printed dimension carries it.
5. **Discovery** (images + text), split into calls of at most `maxInputTokens`, a page's evidence
   always together, the side-by-side pairs first, at most `maxBatches` calls. What does not fit is
   written into `coverage.omissions` and the run ends `partial`. Returns observations, an
   INVENTORY (per element, per required field: supported / unknown / not applicable, with
   evidence; "supported" without server evidence is demoted), gaps and not-applicable claims.
   Nothing observed means no reasoning or verification call is paid for.
6. **Reasoning** (text only): conflict / missing / inconsistency / ambiguity, with a derivation,
   impact and unresolved reason — and a DISPOSITION: `rfi`, or `needs_evidence` with `searchFor`.
7. **Resolving search** for each needs-evidence item (up to 6): identifier match through
   `cdip_identifiers()` then full text, over the project's analysable drawings only, logged in
   `coverage.searchLog`. What it finds is shown to verification as `searched` evidence; if it
   settles the problem, verification rejects it; if nothing was found, the reasoning says so.
8. **Verification** (only the evidence each problem cites, plus what was searched): keep or
   reject, and word the kept ones.
9. **The code's rules**, which the model is not trusted to apply to itself (`rfi_review.guard`):
   - an evidence id the server did not issue is dropped at parse time;
   - a finding supported only by a model's description, or only by an aid, is rejected;
   - a conflict must cite two different pages;
   - the question may not contain an identifier or any digit run that neither the cited evidence
     nor the observations made from it contain (digit runs, not just free-standing numbers:
     `W14x90` would otherwise pass unchecked).
10. **Save** as `origin = 'targeted_review'`. The fingerprint is the check, the kind, the pages and
   the identifiers — never the wording — and the upsert never touches a candidate someone has
   accepted or dismissed, or one the project scan owns. Then `checkResults` (all 16), `coverage`,
   `inventory` and usage are written, and the status is `ready` or `partial`.

**Budgets.** `maxTotalTokens` is checked BEFORE every call (a sent call is paid for either way).
Reaching it stops the run as `partial`: what was found before — the exact findings at least — is
saved, and each unfinished question is `failed` with the reason. Every model call's usage row
carries `reviewRunId`, `stage` and `attempt` (`usage.tagged`), so a run's cost per stage comes
from the ledger rather than an estimate.

A call that FAILS (a 503 "high demand", a 429, a timeout) is asked again after 5s and 20s
(`CALL_RETRY_DELAYS`) — one busy moment at Gemini once failed a whole review at the reasoning
stage and threw away the discovery call already paid for. A reply that is not usable JSON gets
one salvage retry. After that the run fails and says at which stage (and every selected question
reads `failed`). It is never turned into "no RFIs found", which would read as a clean bill of health.
Cancel is checked between stages; a heartbeat lets the API report a dead worker's run as failed.

**A problem found AGAIN is still a problem.** Findings are UNIQUE on `(projectId, fingerprint)`,
so a second review of the same sheets re-finds the same grid mismatch and cannot add it twice. It
used to skip it in silence, count 0 candidates and print "No problems were confirmed" — on a run
whose G01 had found exactly the problem an earlier run filed. Now a finding already on file keeps
its question `candidate_found` with 0 new candidates, and the reason and the run's notes say where
it is (`rfi_review.already_found`): "already RFI 002 in the RFI log", "dismissed earlier —
restore it", or "already waiting in Needs your review". A PENDING finding of an earlier review
moves to the newest run that still finds it, like a model-worded one; a person's decision and a
scan's finding stay where they are.

## Historical RFIs are never input

An RFI someone already issued is the ANSWER KEY. `documents.includeInRfiAnalysis = false` keeps a
document out of every review path — planning, retrieval, rendering, the exact checks and the
resolving search. It is set automatically in two places, each with a reason: at upload when the
FILENAME says RFI (`apps/api/src/rfiSources.ts`; the migration applied the same rule to documents
already uploaded, and a test holds the two to one pattern), and at ingest when the first pages READ
like an RFI form (`workers/src/rfi_sources.py` — the client's "BIM RFI:" form with Date Issued /
Author / Plan/Sheet). The Docs tab's "RFI review input" box is the person's override either way,
and the ingest check never overrules a person's decision. `benchmarks/rfi_eval.py` uses real RFIs
only as expected output.

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
correctly — and RFI 015 stays a known gap, on purpose (next section).

**Never two different floors.** The first client test of the drafts rejected A3.03 (Level 4) laid
over A3.05 (Level 6): the two floors share most columns, so they lined up, and every column of the
southeast wing that stops at the Level 6 roof deck was reported as missing. Columns stop, move and
shrink between floors by design. `rfi_columns.comparable_alignments` now decides which alignments
may be read as a disagreement, from the level each sheet's drawing TITLES name
(`rfi_columns.sheet_level`: a short line naming a kind of drawing — "LEVEL 4 BUILDING PLAN",
"CONCRETE EXHIBIT - LEVEL 14" — never a note pointing elsewhere, and a bare "LEVEL n" line only when
no titled line exists, since S2.105 also carries a level schedule listing LEVEL 1..14):

  * two sheets naming different levels are never compared (no finding and no picture pair);
  * two sheets at the SAME scale are compared only when both name the same level;
  * an enlarged plan over its overall plan is compared unless the levels are known to differ.

The level token is read by the planner's `levelOf` too; both read
`packages/shared/fixtures/sheet-level.json` ("LEVEL 01" is "LEVEL 1"). This closes RFI 015's
level-14-over-level-13 route as well: whether two floors' columns stack is a structural judgement
the overlay cannot make.

**The grid comparison needs a common scale.** The same test rejected S1.102 (overall plan, 1/8")
against A3.36 (two enlarged 1/4" details): four lines of one fell on four of the other by
translation, and grid 4 was reported as grid 1 on two sheets that name every line the same. Each
page's printed scales now travel with its grid (`GRID_CACHE_VERSION` 4); two pages are compared only
when they share one, and a grid whose label is bubbled at two places along its axis (two views on
one sheet) is not compared across pages at all. Both refusals are written to the scan's notes.

## Marked-up RFI packages — the output the team sends

A text report is not an RFI. The team's own issued RFIs (RFI 001–019 on UT Law Student Housing)
are all one shape, and `workers/src/rfi_package.py` produces that shape:

1. **A cover form** — PROJECT - <name>, `RFI: 002` (or `DRAFT RFI` for a finding nobody accepted),
   Date Issued, Author (initials), Discipline, Description, Plan/Sheet, Revision, the question in the
   shaded box as `Q.1)`, and cropped pictures of each clouded area captioned with its sheet.
2. **The drawing sheets themselves**, copied from the ORIGINAL PDF as vector pages (they zoom and
   print like the drawing), with a red revision **cloud** round each piece of evidence, a yellow
   **callout** carrying the RFI number and question, and a **leader** from the callout to the cloud.

The marks are real PDF annotations — Polygon with a cloudy border, FreeText, Line — the same kinds
the team draws in Bluebeam, so every mark can be moved, edited or deleted there. Earlier markup on
the source sheet is not copied. A cloud is never drawn round a box covering most of a sheet (it
would point at nothing), round a model's description or round a server aid.

Where the clouds go comes from what the RFI or finding already stores (`rfi_locations`, or the
candidate's evidence), in the page's UNROTATED space — the space `get_text` reports, chunk boxes
use and PDF annotations take. Only the callout is placed in DISPLAY space (beside the first cloud,
on the sheet, clear of every cloud) and mapped back. Tested at 0/90/180/270 by clipping the
drawing's words out of the cloud, never by comparing the cloud with the box it was made from.

Building this found that the grid check stored its evidence in DISPLAY space: on the /Rotate 90
A3.01 the cloud landed on the title-block strip instead of the grid bubbles. Grid systems now carry
their page's display→unrotated matrix (`GRID_CACHE_VERSION` 3), gridmarks chunk boxes are mapped the
same way, and the viewer's highlight — which had drawn every UNROTATED chunk box as if it were
displayed — now maps boxes through `displayBox` (`@cdip/shared`, golden fixture
`fixtures/display-box.json` regenerated from PyMuPDF by `test_display_box.py`) using
`pages.rotation`, recorded at ingest and back-filled by the next region scrape. Pending scan
findings are corrected by the next scan; an RFI already accepted keeps the pin it was given.

Flow: `POST /projects/:id/rfis/packages {items: [{type: "rfi"|"candidate", id}] | reviewRunId}` →
an `rfi_packages` row → the `rfi-package` job (`RFI_PACKAGE_CONCURRENCY`, default 2) → the PDF at
`projects/{projectId}/rfi-packages/{packageId}.pdf` → `GET /…/packages/:id` returns a short-lived
download link once ready. The API never opens a drawing. Buttons: "Marked-up PDF" on an RFI,
"Marked-up preview" on a finding, "Marked-up PDF of the findings" on a finished review. On the
client sheets a package renders in about two seconds.

## Trust order

`text` (the drawing's own words) > `gridmarks` (measured geometry) > `page`/`crop` images (the
drawing itself) > `aid` (the server's measurements and indexes: exact about what was measured,
never sole support, never grounding for a number) > `description` (another model's account).
Summaries are never evidence.

## The exception to "the AI never reads raw PDFs"

Everywhere else the model sees derived text only. A targeted review RENDERS the original drawings
for it, because a coordination problem is often a picture and never a word (two sheets drawing a
column in different places). The exception is bounded: only pages a person approved in the plan,
annotations stripped, excluded documents never, and every picture stored so the report shows what
the model saw.

## Settings

`RFI_PROVIDER` and `RFI_REVIEW_MODEL` / `RFI_REVIEW_GEMINI_MODEL` are the DEFAULTS the plan screen
starts from; a run may choose another offered model, and stores the provider, model, limits and
effort it was planned with. The defaults are mirrored in `apps/api/src/llm.ts` for the estimate
(`test_rfi_review` fails on a drift). Effort goes out as the stage thinking setting; a numeric
limit as `budget:N` (`llm.thinking_budget`). What was really sent is stored per run. See
`.env.example`.

## Tests and what they do NOT show

Every model in the test suites is a stub (`FakeModel`, `review_stub.py`). They prove what the code
does with ANY reply — ids dropped, guards applied, outcomes for all 16, budgets, partial runs,
idempotent starts, revoked access, searches, reports — and nothing about how well a real model
reviews drawings. No accuracy figure may be quoted from them. What would measure that is
`benchmarks/rfi_eval.py` against RFIs a person really issued, on clean drawings.

## Not built yet

Whole-project review (RFI-B) and persisted relationship edges between sheets wait until RFI-A
meets its acceptance criteria on real evals, as the plan requires. Area, discipline and free-text
targets are not offered.

## Benchmark

`benchmarks/rfi_eval.py --run <runId>` scores a run against `benchmarks/rfi_eval_cases.json`:
RFIs a person really issued, used ONLY as the expected output. RFI 002 ("confirm the grid
layout", S2.105 against A3.01) is found by the exact grid comparison on the client's sheets.
Candidates matching no case are listed and never scored as wrong.
