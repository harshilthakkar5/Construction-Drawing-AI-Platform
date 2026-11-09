"""A schedule and a plan that each point at the other for one mark's value.

A schedule row says SEE PLAN in one of its cells (Leland S9.01: every
post-tensioned beam's "POST-TENSIONING FORCE (KIPS)" is "SEE PLAN"), and the
plan, beside the same mark, says SEE SCHEDULE. Each document defers to the
other, so no drawing gives the value — the loop a reviewer has to catch by hand.

What this deliberately does NOT do: flag a SEE PLAN cell because no value is
printed BESIDE the mark on a plan. Leland's beam forces are on separate
post-tensioning plans, along the tendons ("505 K"), nowhere near the "1PB1" the
framing plan prints, so that rule would raise every PT beam in the set. Only a
pointer printed back at the schedule, beside the mark, is evidence of a loop.

Read from word positions, not the text layer's order: a schedule's rows and
two tables side by side share lines, and only positions tell a row's cells
apart. Pure but for `findings`, which opens pages.
"""

from __future__ import annotations

import re

import rfi_checks

Word = tuple[float, float, float, float, str]

# A mark as a schedule's first column prints it: PC4, PC-4, F6A, and a level
# prefix (Leland's 1PB1 is level 1's post-tensioned beam 1).
MARK = re.compile(r"^(\d{0,2})([A-Z]{1,3})-?(\d{1,3}[A-Z]?)$")
LINE_PT = 2.5  # words whose centres are this close vertically share a line
GAP_PT = 18.0  # SEE and PLAN are neighbouring words, not two cells apart
BACK_PT = 120.0  # a plan's "SEE SCHEDULE" counts when printed this near the mark
# A schedule's rows reach this far either side of its title: Leland S9.01's
# post-tensioned beam schedule starts 580pt left of its centred title.
TABLE_SIDE_PT = 1000.0
# A table's rows run on from each other: the first starts near its title and
# each next one follows closely (Leland's rows are 24-48pt apart). A line that
# starts with a mark further down is a note, not a row.
FIRST_ROW_PT = 150.0
ROW_GAP_PT = 60.0
_SEE = {"SEE", "PER", "REF", "REF.", "REFER"}
_PLAN = re.compile(r"^PLANS?[.,;:)]*$")
_SCHED = re.compile(r"^SCHED(?:ULE)?S?[.,;:)]*$")


def _mid_y(w: Word) -> float:
    return (w[1] + w[3]) / 2


def lines(words: list[Word]) -> list[list[Word]]:
    """Words grouped into printed lines, each sorted left to right."""
    out: list[list[Word]] = []
    for w in sorted(words, key=_mid_y):
        if out and abs(_mid_y(out[-1][-1]) - _mid_y(w)) <= LINE_PT:
            out[-1].append(w)
        else:
            out.append([w])
    return [sorted(line, key=lambda w: w[0]) for line in out]


def _pointer(words: list[Word], target: re.Pattern) -> list[tuple[int, int]]:
    """(i, j) spans in one line where a SEE-word is followed, within a word or
    two, by `target` (PLAN or SCHEDULE): "SEE PLAN", "SEE FRAMING PLAN", "PER SCHED"."""
    spans = []
    for i, w in enumerate(words):
        if w[4].upper() not in _SEE:
            continue
        for j in range(i + 1, min(i + 4, len(words))):
            if words[j][0] - words[j - 1][2] > GAP_PT:
                break
            if target.match(words[j][4].upper()):
                spans.append((i, j))
                break
    return spans


def mark_of(token: str) -> tuple[str, str] | None:
    """(normalized mark, family) for a schedule mark token, else None."""
    m = MARK.match(token.upper().strip(".,:;"))
    if not m:
        return None
    return f"{m.group(1)}{m.group(2)}{m.group(3)}", m.group(2)


def mark_regex(mark: str) -> re.Pattern:
    """`mark` as text prints it, with or without the hyphen: PC4 or PC-4."""
    m = MARK.match(mark)
    return re.compile(rf"(?<![A-Z0-9]){m.group(1)}{m.group(2)}-?{m.group(3)}(?![A-Z0-9])")


def _box(words: list[Word]) -> tuple[float, float, float, float]:
    return (min(w[0] for w in words), min(w[1] for w in words), max(w[2] for w in words), max(w[3] for w in words))


def headings(words: list[Word]) -> list[dict]:
    """Every schedule heading on the page: {"title", "box"}. Each is found from
    its own SCHEDULE word, so two tables titled side by side on one line keep
    their own positions."""
    found = []
    for line in lines(words):
        for end, w in enumerate(line):
            if not w[4].upper().startswith("SCHEDULE"):
                continue
            # The title is the words printed right before SCHEDULE: never across
            # a wide gap or another SCHEDULE (two titles share a line).
            start = end
            while (start > 0 and end - start < 4 and not line[start - 1][4].upper().startswith("SCHEDULE")
                   and line[start][0] - line[start - 1][2] < 2 * GAP_PT):
                start -= 1
            titles = rfi_checks.schedule_titles(" ".join(x[4] for x in line[start : end + 1]))
            if titles:
                title = titles[-1]
                found.append({"title": title, "box": _box(line[end - len(title.split()) + 1 : end + 1])})
    return found


def _heading_over(headings_: list[dict], x: float, y: float) -> dict | None:
    """The nearest heading above (x, y) whose table could hold it."""
    best = None
    for h in headings_:
        x0, y0, x1, y1 = h["box"]
        if not (y1 - 2 <= y <= y1 + rfi_checks.SCHEDULE_BODY_BELOW_PT):
            continue
        if not (x0 - TABLE_SIDE_PT <= x <= x1 + TABLE_SIDE_PT):
            continue
        # Nearest above; between two titled side by side, the nearer one sideways.
        rank = (-y1, abs((x0 + x1) / 2 - x))
        if best is None or rank < best[0]:
            best = (rank, h)
    return best[1] if best else None


def deferring_rows(words: list[Word]) -> list[dict]:
    """Schedule rows that say SEE PLAN for their mark.

    A row is the stretch of a line from a mark to the next mark (two tables
    side by side share lines). It counts only under a schedule heading that
    names the mark's family, in a table listing at least MIN_SCHEDULE_MARKS
    marks of that family — a note line that happens to start with a tag is not
    a schedule row. Each: {"mark", "family", "title", "header", "row", "cell"}."""
    heads = headings(words)
    if not heads:
        return []
    rows = []
    for line in lines(words):
        starts = [i for i, w in enumerate(line) if mark_of(w[4])]
        for k, i in enumerate(starts):
            seg = line[i : starts[k + 1] if k + 1 < len(starts) else len(line)]
            mark, family = mark_of(seg[0][4])
            head = _heading_over(heads, seg[0][0], _mid_y(seg[0]))
            if head is None or not rfi_checks.title_names_family(head["title"], family):
                continue
            rows.append({"mark": mark, "family": family, "head": head, "seg": seg})
    rows = _contiguous(rows)
    out = []
    for r in rows:
        mates = {x["mark"] for x in rows if x["head"] is r["head"] and x["family"] == r["family"]}
        if len(mates) < rfi_checks.MIN_SCHEDULE_MARKS:
            continue
        spans = _pointer(r["seg"], _PLAN)
        if not spans:
            continue
        i, j = spans[0]
        cell = _box(r["seg"][i : j + 1])
        first_row = min(_mid_y(x["seg"][0]) for x in rows if x["head"] is r["head"])
        out.append({
            "mark": r["mark"],
            "family": r["family"],
            "title": r["head"]["title"],
            "header": _header(words, cell, r["head"]["box"][3], first_row),
            "row": _box(r["seg"]),
            "cell": cell,
        })
    return out


def _contiguous(rows: list[dict]) -> list[dict]:
    """Per heading, the run of rows that starts near it and continues without a
    gap; each mark once."""
    kept = []
    for head in {id(r["head"]): r["head"] for r in rows}.values():
        mine = sorted((r for r in rows if r["head"] is head), key=lambda r: _mid_y(r["seg"][0]))
        last, seen = head["box"][3], set()
        for r in mine:
            y = _mid_y(r["seg"][0])
            if y - last > (FIRST_ROW_PT if last == head["box"][3] else ROW_GAP_PT):
                break
            last = y
            if r["mark"] not in seen:
                seen.add(r["mark"])
                kept.append(r)
    return kept


def _header(words: list[Word], cell, top: float, first_row: float) -> str:
    """The column heading above a cell: words between the schedule's title and
    its first row that sit over the cell."""
    x0, _y0, x1, _y1 = cell
    over = [w for w in words
            if top < _mid_y(w) < first_row - LINE_PT
            and min(w[2], x1 + 25) - max(w[0], x0 - 25) > 0
            and not mark_of(w[4])]
    return " ".join(w[4] for w in sorted(over, key=lambda w: (round(_mid_y(w)), w[0])))[:80]


def points_back(words: list[Word], mark: str, away_from=None) -> list[tuple[float, float, float, float]]:
    """Where `mark` is printed with SEE SCHEDULE beside it (within BACK_PT),
    outside `away_from` (the schedule's own row)."""
    hits = []
    pointers = []
    for line in lines(words):
        for i, j in _pointer(line, _SCHED):
            pointers.append(_box(line[i : j + 1]))
    if not pointers:
        return hits
    for w in words:
        found = mark_of(w[4])
        if not found or found[0] != mark:
            continue
        if away_from and _inside(w, away_from):
            continue
        cx, cy = (w[0] + w[2]) / 2, _mid_y(w)
        for p in pointers:
            px, py = (p[0] + p[2]) / 2, (p[1] + p[3]) / 2
            if abs(px - cx) <= BACK_PT and abs(py - cy) <= BACK_PT:
                hits.append(_box([w, (p[0], p[1], p[2], p[3], "")]))
                break
    return hits


def _inside(w: Word, box) -> bool:
    x0, y0, x1, y1 = box
    return x0 - 2 <= (w[0] + w[2]) / 2 <= x1 + 2 and y0 - 2 <= _mid_y(w) <= y1 + 2


def _bbox(box) -> dict:
    x0, y0, x1, y1 = box
    return {"x": round(x0, 2), "y": round(y0, 2), "width": round(x1 - x0, 2), "height": round(y1 - y0, 2)}


def findings(pages, chunks, open_page, every=None) -> tuple[list, list[str]]:
    """schedule_plan_loop findings for the project: (findings, notes).

    Opens only pages whose stored text says SEE PLAN under a schedule, then
    only pages whose text carries one of those marks and points at a schedule."""
    from rfi_checks import Finding, fingerprint, page_label

    text_by_page: dict[str, list[str]] = {}
    for c in chunks:
        text_by_page.setdefault(c.page_id, []).append(c.text)
    joined = {pid: "\n".join(t).upper() for pid, t in text_by_page.items()}
    candidates = [p for p in pages
                  if re.search(r"\bSCHEDULE\b", joined.get(p.id, "")) and re.search(r"\bSEE\s+(?:\w+\s+)?PLANS?\b", joined.get(p.id, ""))]
    # A review reads its own sheets' schedules; the plan pointing back may be any page.
    back_pages = [p for p in (every or pages) if re.search(r"\b(?:SEE|PER|REF\.?|REFER)\s+(?:\w+\s+)?SCHED", joined.get(p.id, ""))]
    out, notes = [], []
    for row in candidates:
        page = open_page(row.document_id, row.page_number)
        if page is None:
            continue
        for r in deferring_rows([tuple(w[:5]) for w in page.get_text("words")]):
            loops = []
            for other in back_pages:
                if not mark_regex(r["mark"]).search(joined.get(other.id, "")):
                    continue
                opage = page if other.id == row.id else open_page(other.document_id, other.page_number)
                if opage is None:
                    continue
                words = [tuple(w[:5]) for w in opage.get_text("words")]
                for box in points_back(words, r["mark"], r["row"] if other.id == row.id else None):
                    loops.append((other, box))
            if not loops:
                continue
            what = r["header"].lower() or "this value"
            sheets = sorted({page_label(o) for o, _ in loops})
            evidence = [{
                "documentId": row.document_id, "pageNumber": row.page_number,
                "combinedPageNumber": row.combined_page_number, "sheetNumber": row.sheet_number,
                "bbox": _bbox(r["row"]), "chunkId": None,
                "quote": f"{r['title']}: {r['mark']} … SEE PLAN", "role": "finding",
            }] + [{
                "documentId": o.document_id, "pageNumber": o.page_number,
                "combinedPageNumber": o.combined_page_number, "sheetNumber": o.sheet_number,
                "bbox": _bbox(box), "chunkId": None,
                "quote": f"{r['mark']} … SEE SCHEDULE", "role": "finding",
            } for o, box in loops[: rfi_checks.MAX_EVIDENCE - 1]]
            out.append(Finding(
                check_type="schedule_plan_loop",
                fingerprint=fingerprint("schedule_plan_loop", r["mark"], r["header"].upper()),
                confidence="high",
                subject=f"{r['mark']}: schedule and plan point at each other"[:120],
                question=(f"The {r['title'].lower()} on {page_label(row)} gives {what} for {r['mark']} as "
                          f"\"SEE PLAN\", and {', '.join(sheets)} prints {r['mark']} with \"SEE SCHEDULE\". "
                          f"Neither drawing gives the value. Please provide {what} for {r['mark']}."),
                evidence=evidence,
                facts={"mark": r["mark"], "schedule": r["title"], "column": r["header"],
                       "scheduleSheet": page_label(row), "planSheets": sheets},
            ))
    return out, notes
