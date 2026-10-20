# Structured RFI Review — Requirements and Phase Plan

Status: proposed, not built. Written 2026-10-19 from the client's request and the
"Concrete Scope Element Library" workbook.

## 1. What the client asked for

1. **Project Settings tab.** Move every setting out of the RFIs tab into a new
   Project Settings tab. The RFIs tab keeps only the work: choose what to check,
   run it, and review the results.
2. **Structured RFI generation.** The user works top down: package → element
   (grids first) → question category → questions, picked from the client's
   question bank or typed by the user. Then they pick which pages to compare: a
   few sheets, or sheets from different disciplines. Narrow, chosen input should
   make the AI more accurate.
3. **Chat to review.** When a chat answer finds an RFI-worthy problem, the user
   can move it into "Needs your review" in the RFIs tab.

## 2. The client's workbook (input data)

File: `Concrete_Scope_Element_Library.xlsx`

| Sheet | Contents | Count |
|---|---|---|
| Element Library | ID, review order, package, element, variants, sheets to compare, attributes to verify, typical RFI triggers | 120 elements in 12 packages |
| Question Bank | Q-ID, element, category, question, where to look | 572 questions |
| Universal Questions | U-01..U-10, templates with `{element}` that apply to every element | 10 |
| Universal Checks | 15 check categories, with what to verify and what to compare | 15 |
| Summary | Counts per package | — |

**Red rows = not used for now.** 58 elements are red, so 62 are active.
Questions inherit this from their element: **346 active questions**.

| Package | Active elements | Active questions |
|---|---|---|
| 01 Project Setup & Reference | 3 (grids, levels, datum) | 24 |
| 02 Subgrade & Below-Slab | 0 | 0 |
| 03 Deep Foundations | 4 | 27 |
| 04 Shallow Foundations | 13 | 84 |
| 05 Pits, Trenches & Depressions | 6 | 31 |
| 06 Slab on Grade | 6 | 34 |
| 07 Vertical Elements | 9 | 49 |
| 08 Elevated Structure | 17 | 80 |
| 09–11 Stairs / Site / Specialty | 0 | 0 |
| 12 Embeds, Penetrations & Interfaces | 4 | 17 |

Questions per category (active): Coordination 100, Geometry/Size 63,
Reinforcement 46, Elevation 40, Location 38, Missing info 27,
Code/Constructability 18, Material/Spec 14.

**Rule:** red/active status is data, not code. Hidden elements can be switched on
later with no release.

## 3. Requirements

### R1 — Project Settings tab

A new tab at project level, next to Docs, Summary & categories and RFIs.

| Section | Settings (moved from the RFIs tab unless marked new) |
|---|---|
| Project | Name, description, roles (existing project fields) |
| AI for RFIs | Provider + model; how careful (thinking level); token limits (most input tokens per call, most thinking tokens) |
| Review defaults | Depth (Quick / Standard / Deep); "also run whole-project checks" on/off |
| Cost control | Auto-start AI comparison up to $X (today's `rfiAutoScanUsd`); **new:** a per-run cost cap |
| Question library | Turn packages / elements / questions on or off for this project; add project-only custom questions (see R2) |
| RFI form | **New:** author initials, company, RFI number prefix, default discipline, used on the marked-up PDF cover |
| Chat | **New:** chat findings go to review: "ask me" (default) or "add automatically" (see R4) |

Rules:
- Only project owners can change settings; members can read them.
- Every run stores the settings it used, so changing settings never changes an old result.
- The RFIs tab shows one line with the active settings ("Gemini 3.8 Flash · Standard ·
  cap $2") and a link to Settings. It shows no settings controls.

### R2 — Element library and question bank

- **Users never load the Excel.** A developer imports the workbook once
  (`npx tsx apps/api/src/rfiLibraryImport.ts <file.xlsx>`) into
  `packages/shared/data/rfi-library.json`, which ships with the app. Every project sees
  the same built-in questions. Users only pick questions, and add their own if they want.
- Packages → elements → questions, plus the universal questions (expanded per element,
  with `{element}` filled in).
- Keep the client's IDs (`CE-04-01`, `CE-04-01-Q03`, `U-02`) as stable keys. Results,
  RFIs and reports cite them.
- Each element keeps "compare across", "attributes to verify" and "typical RFI
  triggers". The AI receives them as context, and sheet suggestions use them.
- **Any depth:** any question, built-in or custom, can have follow-up questions under it,
  and those can have their own.
- **New version from the client:** the developer runs the importer again and commits the
  file. Questions update by ID; red rows go off. A link to an ID the new workbook dropped
  stops the import.
- **Custom questions:** users add their own, saved in the project (`rfi_custom_questions`),
  under an element or as a follow-up under any question of that element. They are marked "custom" and are AI-only, so never above medium confidence.
- **Existing questions:** today's 17 questions (G01…O01) map onto library elements, e.g.
  G01 → CE-01-01, G02 → CE-01-02, F01 → CE-04-01, F04 → CE-03-0x, C01/C03 → CE-07-01,
  C02 → CE-07-03, B01–B03 → CE-08-03, O01 → CE-08-10 / CE-07-05. The 7 code checks keep
  running under the library questions they answer, so nothing that works today is lost.

### R3 — Structured RFI review (the new RFIs tab flow)

A step-by-step flow. Each step can be changed before the run.

1. **Package.** In the workbook's review order: Setup → Foundations → Vertical →
   Elevated → Embeds. Grids first, as the client asked.
2. **Element.** For example "Grid lines" or "Pile caps", with its variants shown.
3. **Questions.** Grouped by category with checkboxes. Child questions are shown under
   their parent. "Select all in category", universal questions and "Add my own question"
   are available.
4. **Pages.**
   - Pick sheets by number, discipline, or from thumbnails, e.g. 2 structural +
     1 architectural sheet, or "all S2.1xx".
   - **Suggested sheets** come from the element's "compare across" (e.g. Grid lines →
     Arch plans, Struct plans, Civil site plan) matched to sheet kinds already read
     (plan, schedule, section, level, scale). The user can accept or edit them.
   - If no uploaded sheet matches a required kind (e.g. "Specs Div 03", "Geotech
     report"), the flow says so. Those questions end "needs a document that is not
     uploaded", never "no issue".
5. **Plan and price.** Lists the pages, the questions and a cost range before anything is
   sent to the AI. Uses the settings from R1.
6. **Run.** Shows progress, as scans do today, and can be stopped.
7. **Results.**
   - An answer per question: issue / agrees / not shown / unclear / needs a document.
   - RFIs go into "Needs your review", tagged with the question ID and element.
   - The user can open a question, see the evidence boxes and go "next element" to
     continue down the package.

Rules (unchanged from today's pipeline):
- Only the chosen pages are sent to the AI. Annotations are stripped. Historical RFI
  documents are never input.
- Code checks run first where a question has one (grids, schedule marks, missing level,
  column schedule, sheet references…). Code + AI agreement is High; AI-only is at most Medium.
- Every RFI cites sheet, grid/location and a box on the drawing, and asks one question.
- A finding already on file is shown as "found again", never as a duplicate.
- "Scan all drawings" stays available. Some RFIs only show up across the whole set:
  missing sheets, schedule lookups, sheet index.

### R4 — Chat → "Needs your review"

- When a chat answer describes a problem (a conflict, a missing dimension, a TBD, a
  mismatch between sheets), the answer shows a **"Send to review"** button.
- The model also returns a structured flag ("this looks like an RFI": subject, question,
  cited chunks), so the button is suggested on the right answers. The user can still
  press it on any answer.
- The candidate is built **only from the chunks the answer cited**: page, box and quote.
  Its origin is `chat`, it is linked to the chat message, and it is capped at Medium with
  "check on the sheet". No citation means no button: an RFI must point at the drawing.
- With the R1 chat setting at "add automatically", flagged answers go straight to review.
  The chat shows "added to review" with an undo.
- It is deduplicated against existing findings by fingerprint, and the user is told if
  it is already on file.
- After that it behaves like every other candidate: accept → numbered RFI, dismiss,
  marked-up PDF, Excel log.

## 4. What exists already (reused, not rebuilt)

| Need | Already built |
|---|---|
| Choose sheets / disciplines to compare | Scoped scan (discipline A vs B), targeted review (sheet / element / compare sheets) |
| Questions with checkboxes, own questions | 17-question catalogue, Q1–Q5 custom questions per scan |
| Cost estimate before a run, stop a run | Plan + price, Stop button, token ceilings |
| Code checks + AI agreement, confidence caps | 7+ code checks, `agreement.py`, AI-only ≤ Medium |
| "Needs your review" list, accept → RFI | `rfi_candidates`, accept/dismiss, numbered RFIs |
| Marked-up PDF + Excel log | `rfi_package.py`, preview modal, export |
| Chat with citations → page + box | RAG chat, chunk citations, viewer highlight |

New work: the Settings tab, the library tables + import, the step-by-step UI, running
library questions on hand-picked pages, and chat → review.

## 5. Phases

| # | Phase | Delivers | Effort |
|---|---|---|---|
| 1 | **Project Settings tab** ✅ built | New Settings tab: project name, description, roles; targeted-review model, depth, how careful, token limits; AI-comparison model, batch/direct, budget, auto-start limit. Saved on the project (`projects.rfiSettings`, owner only). The RFIs tab shows one line per run type with "Change in Settings". Not moved: what to compare and which questions (that is the work, not a setting). Not built: RFI form fields (author initials already come from the user's name) | done |
| 2 | **Built-in library** ✅ built | Workbook imported once into the app (developer step, not a user upload), red rows off, universal questions expanded per element, the 17 existing questions and code checks linked, saved custom questions with follow-ups to any depth (API: `GET/POST/PATCH/DELETE /projects/:id/rfis/library`) | done |
| 3 | **Structured review UI** ✅ built | "By element" is the targeted review's first mode: element (grouped by package, review order, "Next element") → its questions by category, with follow-ups and the project's own questions (add, follow-up, delete) → any number of sheets (suggested from Compare Across: a floor plan naming a level first, every sheet on one level; type any sheet number to add; thumbnails) → the existing plan, price and results. Warns when the project has no sheets for an entry, or the element needs documents that are not drawings. Until Phase 4 the review asks a choice through the original questions it links to (34 of 62 active elements); an element with none says so and cannot be planned | done |
| 4 | **Engine** ✅ built | A By element review asks the chosen library questions in the client's words (follow-ups carry their parents' words), with the element's attributes as what to record and its typical triggers as what makes an RFI, beside any linked original questions. The code checks the questions link to run first on the chosen sheets (type marks, schedule marks, unsized columns, references, TBDs; grids and columns via G01/C01) and their findings are credited to every question they answer. AI-only answers to library questions are capped at Medium; a project's own question files as "Your own question". Outcomes per library question in the results | done |
| 5 | **Chat → review** ✅ built | The chat ends an answer with one "RFI:" line when the cited chunks show a problem; it is taken off the answer and offered as "Possible RFI". "Send to review" on any cited answer (subject + question editable) files a Medium finding (origin `chat`, linked to the message) pinned to the sheets the answer cited — never to documents kept out of RFI analysis; sending twice is "already on file". Not built: the "add automatically" setting | done |
| 6 | **Testing on client sets** — first test done | By element on Leland: Deep Foundations → Drilled piers → S2.30-B, 9 library questions + F04, Gemini 3.6 Flash, $0.06, 2.5 min. The pile type-mark code check found the client's RFI 001 ("Element with no type mark on S2.30-B at grid line BK") and credited it to F04 and CE-03-01-Q01; it was reported "found again" because a scan had already put it in Needs your review. The AI's own answers raised nothing (0 proposed). All 62 active elements (20 Oct): every element plans and runs on Leland (Gemini 3.6 Flash, 154 calls, 6.5M input / 0.53M output tokens, about $4–5, ~2.5 min each, 4 at a time). 1,010 question outcomes: 252 no issue, 72 problem found, 424 not enough evidence, 262 not applicable; 16 new medium findings. Fixed from it: a bare "Struct" suggests the plan, not the first schedule; a sheet number matches as printed first (S0.01 was pulling in a wall type misread as S-001); and a type-mark finding answers an element's questions only when the mark's prefix names that element (the pile finding had been credited to 55 elements, Anchor bolts included). Scored 20 Oct against the 9 Leland RFIs (`rfi_eval.py --project-reviews`), 6 elements on Gemini 3.6 Flash: auto-suggested sheets 2 of 9; the test guide's sheets (docs/leland-by-element-test-steps.md) **3 of 9** — RFI 001, 003 and 005, all by code checks ($0.48, 1–2 min each). The AI found none of the other six (pile centre under a wall, pier caps where the typical detail stops, columns on A5, the two pit details, retaining wall and pier sizes, elevations at 10/S4.10): it answered most of those elements' questions "no issue". Still to do: a stronger model or Deep depth on those six, then UT Law | in progress |
| | **Total** | | **24–31 days (about 5–6 weeks, one developer)** |

Order: 1 → 2 → 3 → 4 → 5 → 6. Phase 5 does not depend on 2–4 and can be done earlier
if the client wants chat → review first. After Phase 3 the client can click through the
whole flow. After Phase 4 it produces RFIs.

**AI cost per run** (estimate): one element on 2–3 sheets with 5–10 questions is about
10–20 image areas. That is about $0.10–0.20 on Gemini Flash or $0.30–0.60 on Claude Sonnet.
A whole package (e.g. 13 shallow-foundation elements) on the same sheets is $1–6.

## 6. Risks and limits (tell the client)

1. **Most of the 346 questions are AI-only.** Code checks back about 15–20 of them
   (grids, levels, schedule marks, column schedules, references, TBDs). The rest depend on
   the AI reading the drawing, and that accuracy is not measured yet. Phase 6 measures
   it on grids, foundations and columns only.
2. **Some questions need documents that aren't drawings.** Specs Div 03, the geotech
   report, vendor and shop drawings. If they aren't uploaded, those questions answer
   "needs a document", not an RFI.
3. **Reinforcement and Code questions** (laps, hooks, cover, fire rating) usually need
   typical details and code tables. Expect low accuracy there. Consider leaving them off
   by default.
4. **Choosing pages helps, but can hide problems.** If the user leaves out the sheet that
   conflicts, the review cannot find it. Suggested sheets reduce this; "Scan all
   drawings" remains for whole-set checks.
5. **Chat findings are weaker evidence.** Chat reads text chunks, not the picture. Chat
   RFIs stay Medium until a person checks them.

## 7. Questions for the client

1. Should the question bank be **shared by all projects** (one library the client
   maintains) or **copied per project** (each project can edit its own)? Recommendation:
   shared library, plus per-project on/off and custom questions.
2. Who may edit the shared library: only admins, or any project owner?
3. Should universal questions (U-01..U-10) appear under every element, or only on request?
4. Do the question categories (Location, Geometry, Elevation…) need their own
   checkbox level, or is "element → questions" enough?
5. Chat → review: keep the default at "ask me" (a button), or add automatically?
6. Which packages matter most for the first test: grids + foundations + columns
   (recommended), or another set?
