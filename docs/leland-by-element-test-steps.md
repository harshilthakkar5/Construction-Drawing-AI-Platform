# Leland — testing "By element" against the 9 real RFIs

The Leland team wrote 9 RFIs on this set. Each section below says how to ask the app for the
same thing with **RFIs → Targeted review → By element**, and what a hit looks like.

The RFI PDFs are the ANSWER KEY. Never upload them to the project — the app would read the
answer instead of finding it.

## Before you start

1. **Settings tab → Targeted RFI review → AI model: Gemini · gemini-3.6-flash**, Depth
   **Standard**. Each review below costs about $0.05–0.10 and takes 2–4 minutes.
2. **Clear old findings, or a hit shows as "found again".** RFIs → Show dismissed → **Delete**
   any of these: "Unmarked element at grid line BK on S2.30-B", "Unmarked elements on S2.31-A",
   "Missing column schedule details on S8.02", "Missing column size and reinforcement on S8.02".
   A dismissed finding stays dismissed; a deleted one can be proposed again.
3. **Two passes per RFI.** Run it first with the **library questions only** — that is the fair
   test of what the app finds by itself. Then, if it missed, add the **own question** listed and
   run again — that tests whether it can answer when asked directly.
4. In **Sheets to read**, click × on the suggested sheets and add exactly the ones listed.

How to read the result: the question shows **Problem found** and a card appears in
**Needs your review**, pinned to the sheet. Click the pin — it must land on the clouded area
of the RFI.

If the question says **No problems** but has a **Considered, not raised** list under it, the AI
did propose something and the checking step set it aside — the reason is shown. If the
proposal matches the RFI, press **Keep as RFI**: it goes to Needs your review as a Low finding.
Count it as "proposed, set aside" in your results, not as a hit. (RFIs 008 and 009 landed here
in the second pass.)

---

## RFI 001 — Pile type mark missing at A1 / BK (S2.30-B)

- **Element:** Deep Foundations → **Drilled piers / caissons / drilled shafts**
- **Questions:** Q01 *Does every pier mark on the plan appear in the pier schedule, and vice
  versa?* (has a **code check**) + U-01 *Is every … tagged …?*
- **Sheets:** S2.30-B
- **Own question:** not needed.
- **Expect:** code check "Unmarked element at grid line BK on S2.30-B" (High). Already
  confirmed on 19 Oct.

## RFI 002 — Center of the pile under the wall (S2.30-B, S2.31-A)

- **Element:** **Drilled piers / caissons / drilled shafts**
- **Questions:** Q05 *Are pier locations dimensioned to grids, and are eccentric piers
  dimensioned?* + U-02 *located/dimensioned to the nearest grid line …?* + Q09 *Do pier tops
  align with column/pedestal locations above?*
- **Sheets:** S2.30-B, S2.31-A, S4.00, S8.12 (the shear wall SW3 detail)
- **Own question (pass 2):** under Q05 → **+** → *"Where a pier sits under a wall, is the
  pier centre dimensioned, or is it assumed on the wall centreline?"*
- **Expect:** AI only (Medium). A hit names a pier under a wall with no dimension to the grid.

## RFI 003 — Pile type mark and elevation near grid XA (S2.31-A)

- **Element:** **Drilled piers / caissons / drilled shafts**
- **Questions:** Q01 (code check) + Q03 *Are top-of-pier and minimum tip elevations
  specified …?* + Q05
- **Sheets:** S2.31-A
- **Own question:** not needed for the mark; for the elevation, under Q03 → *"Does every pier
  near grid XA show its top elevation?"*
- **Expect:** code check "Unmarked elements on S2.31-A" (3 piles, High) — the mark half. The
  elevation half is AI only.

## RFI 004 — Pier cap size where the typical detail does not apply (S0.03)

- **Element:** Deep Foundations → **Pile caps**
- **Questions:** Q02 *Do pile cap dimensions in the schedule fit the pile spacing …?* + Q04
  *Are top and bottom of pile cap elevations specified …?* + U-03 *Do the … dimensions match
  between plan, section, schedule and detail?*
- **Sheets:** S0.03 (typical pier cap detail), S2.30-B, S2.31-A
- **Own question (pass 2):** under Q02 → *"Does the typical pier cap detail on S0.03 apply to
  every pier cap, and where it does not, are the cap dimensions given?"*
- **Expect:** AI only. Hard case — the RFI is about where a TYPICAL detail stops applying.

## RFI 005 — Columns at B6/B7 × BA/BB with no tag in the column schedule (S2.30-B, S8.02)

- **Element:** Vertical Elements → **Columns**
- **Questions:** Q01 *Does every column on the plan exist in the column schedule for every
  level?* (has **code checks**: schedule mark + column with no size) + U-01
- **Sheets:** S2.30-B, S8.02
- **Own question:** not needed.
- **Expect:** code check on unsized columns / unscheduled marks, or AI. A hit names columns at
  B6-BA, B6-BB, B7-BA, B7-BB.

## RFI 006 — Columns along grid A5 with no size in the column schedule (S2.30-B)

Answered by the drawings: S8.02 (page 93) schedules A5-BA, A5-BB, A5-BC.1, A5-BD, A5-BE and A5-BF at 18" X 24". A review that raises this is WRONG on this set; the benchmark scores it as a false positive.

- **Element:** **Columns**
- **Questions:** Q01 (code checks) + U-03
- **Sheets:** S2.30-B, S8.02
- **Own question:** not needed. Can be run together with RFI 005 — same element, same sheets.
- **Expect:** same as 005; a hit names columns along A5.

## RFI 007 — Elevator pit: S4.00 detail 3 says 8", detail 4 says 1'-4" (BH / B7)

- **Element:** Pits, Trenches & Depressions → **Elevator pits**
- **Questions:** Q03 *Is the top of pit slab elevation specified?* + Q08 *Are pit wall and slab
  reinforcement specified?* + U-03 *Do the … dimensions match between plan, section, schedule
  and detail?* + U-07 *Does every section/detail callout … point to an existing detail that
  matches the condition?* (has a code check)
- **Sheets:** S2.30-B, S4.00, S8.13
- **Own question (pass 2):** under U-03 → *"Do the elevator pit details called out from the
  same place give the same slab thickness?"*
- **Expect:** AI (Medium). A hit names S4.00 details 3 and 4 and the two thicknesses — a
  conflict needs both pages cited.

## RFI 008 — Retaining wall width, pier size and top of pier at the cistern (S2.31-A, S4.10)

- **Element:** Shallow Foundations → **Retaining walls (building)**
- **Questions:** Q01 *Are stem thickness, heel and toe dimensions specified?* + Q02 *Are
  top-of-wall and top-of-footing elevations given …?* + U-02 + U-04
- **Sheets:** S2.31-A, S4.10
- **Own question (pass 2):** under Q01 → *"At the underground cistern, are the retaining wall
  thickness and the pier size and location below it dimensioned in plan?"*
- **Second element (optional):** **Drilled piers** with Q03 + Q05 on the same sheets, for the
  pier half.
- **Expect:** AI. Note the plan says "CANTILEVERED RETAINING WALL BY OTHERS" — the app may
  treat it as someone else's scope, which is a fair reading.

## RFI 009 — Elevations at callout 10/S4.10 (S2.31-A)

- **Element:** Project Setup & Reference → **Levels / elevations**
- **Questions:** Q05 *Are top-of-footing, top-of-wall, top-of-pier and top-of-steel elevations
  given wherever required?* + Q07 *Are elevations … clearly defined as relative or absolute?*
- **Sheets:** S2.31-A, S4.10, S4.02 (S4.10 points to 24/S4.02 for T.O.C.)
- **Own question (pass 2):** under Q05 → *"At callout 10/S4.10, are the top and bottom
  elevations of the walls and the slab given, with their levels?"*
- **Expect:** AI. Several values say "SEE CIVIL" — a hit is a missing elevation, not a
  reference elsewhere.

---

## Score sheet

| RFI | Element | Pass 1 (library only) | Pass 2 (own question) | Pin on the cloud? | Cost |
|---|---|---|---|---|---|
| 001 | Drilled piers | | | | |
| 002 | Drilled piers | | | | |
| 003 | Drilled piers | | | | |
| 004 | Pile caps | | | | |
| 005 | Columns | | | | |
| 006 | Columns | | | | |
| 007 | Elevator pits | | | | |
| 008 | Retaining walls | | | | |
| 009 | Levels / elevations | | | | |

Write **Hit**, **Partial** (right sheet, wrong place or half the question), **Miss**, or
**False** for each finding the app raised that is not a real problem.
