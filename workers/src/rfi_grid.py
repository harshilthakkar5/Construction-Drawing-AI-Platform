"""Check 4: grid lines the drawings name differently.

A structural plan and an architectural plan of the same building are drawn on
ONE set of grid lines. When the two disciplines number them differently — the
line the structural sheet calls 6 is the architectural sheet's 9 — every
layout crew, every formwork drawing and every BIM model has to pick one, and
that is an RFI a contractor really did send ("CONFIRM THE GRID LAYOUT").

It is found from the PDF's GEOMETRY, never from text or a model. `grid.py`
reads each grid bubble — a drawn circle holding one label — and groups them by
drawing style, so a sheet that carries two grids (its own, and an
architectural background in another colour) yields two systems. Annotations
are stripped before any of it (`grid.without_markup`): a finding comes from the
drawings as issued, never from someone's markup of them. Two systems are then
laid over each other, and a line that sits in the same place in both under two
different names is the finding. The evidence is the drawing itself: a bubble,
a position, a label.

Everything here is pure — systems in, findings out — so the alignment rules
are tested without a PDF. `rfi_scan.load_grids` is the part that reads PDFs.

Precision first, like every check in rfi_checks:

  * Two systems are compared only after a TRANSLATION aligns them, and the
    alignment must be unambiguous. A grid on a regular bay lines up with a
    copy of itself shifted by one bay nearly as well as with the original, and
    that shift renames every line — the one false finding this check could
    produce in bulk. So the best offset must beat the runner-up by
    MIN_MARGIN lines, or nothing is reported.
  * No scale search, and no comparison ACROSS scales. Two pages are compared
    only when they print a common drawing scale (`scales`, read off the page's
    own `1/8" = 1'-0"` text). Without that rule a translation still "aligns"
    two grids at different scales whenever four lines happen to fall on four
    others, and renames all of them: S1.102 (an overall plan at 1/8") against
    A3.36 (two enlarged 1/4" details) was reported as grid 4 = grid 1, on two
    sheets that name every line identically. An enlarged plan beside an
    overall plan is now a missed finding, the direction an error here is
    allowed to fall.
  * A grid whose label appears at two DIFFERENT positions on its axis is not
    one grid but several views on one sheet (two enlarged details side by
    side, each with its own A and B). Its positions are paper-space
    coincidences, so it is not compared with another page at all.
  * Across pages, only sheets of DIFFERENT disciplines are compared (or a
    sheet whose discipline is unknown). Two structural levels whose grids
    differ are usually two parts of a building, not a naming dispute.
  * On ONE page, two styles are always compared — and must align at zero
    offset, since they are drawn on the same sheet. That is the strongest
    form: the drawing prints both names on the same line.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from rfi_checks import MAX_EVIDENCE, Finding, Page, _cap, fingerprint, page_label

CHECK_TYPE = "grid_mismatch"

# Two bubbles are on the same line when their positions agree within this,
# in points after alignment. At 1/8" = 1'-0" that is about four inches — far
# tighter than any two distinct grid lines, looser than a bubble's centring.
MATCH_TOL_PT = 3.0
# Lines a pair must share before an alignment means anything.
MIN_MATCHED = 4
# ...and the share of the smaller system that must line up. A partial plan
# showing four lines of a forty-line grid can align almost anywhere.
MIN_MATCHED_SHARE = 0.5
# How many more lines the best offset must match than the runner-up. One is
# what a one-bay shift of a regular grid costs, so one is not enough.
MIN_MARGIN = 2
# Distinct naming pairs compared at most — a 1000-sheet set with every plan
# gridded identically collapses to a handful, and a set that does not is not
# one this check can read in bulk.
MAX_SYSTEMS = 80
# A label's bubbles further apart than this ALONG its axis are two different
# lines that share a name — two viewports, not one grid. The two ends of one
# line differ by a few points (S2.105's H: 18pt for a kinked line); two views
# of the same building sit hundreds of points apart.
REPEAT_PT = 60.0
# Two printed scales are the same scale within this fraction.
SCALE_TOL = 0.02


@dataclass(frozen=True)
class GridSystem:
    """One grid on one page, in display points (see grid.styled_systems)."""

    page_id: str
    style: str
    along_x: dict[str, float]
    along_y: dict[str, float]
    # Every bubble of each label, [x0, y0, x1, y1] — both ends of a line.
    bubbles: dict[str, list[list[float]]] = field(default_factory=dict, hash=False, compare=False)
    # The page's display -> unrotated matrix (6 floats). Everything above is
    # in DISPLAY space; an evidence box is stored UNROTATED, the space chunk
    # boxes and PDF annotations use. None for a system read without it — an
    # unrotated page, where the two spaces are the same.
    to_unrotated: tuple | None = field(default=None, hash=False, compare=False)
    # Every drawing scale the page prints, in points per foot
    # (plan_match.page_scales). Empty when none was read — or for a system
    # cached before scales were stored, which compares as before.
    scales: tuple[float, ...] = field(default=(), hash=False, compare=False)

    def repeats_a_label(self) -> bool:
        """True when one label is bubbled at two places on its axis: several
        views on one sheet, not one grid (see REPEAT_PT)."""
        for axis, i in ((self.along_x, 0), (self.along_y, 1)):
            for label in axis:
                centres = [(r[i] + r[i + 2]) / 2 for r in self.bubbles.get(label, [])]
                if centres and max(centres) - min(centres) > REPEAT_PT:
                    return True
        return False


def share_a_scale(a: tuple[float, ...], b: tuple[float, ...]) -> bool:
    """Whether two pages print a common drawing scale. Unknown on either side
    is not a disagreement — a sheet printing no scale compares as it always
    did."""
    if not a or not b:
        return True
    return any(abs(x - y) <= SCALE_TOL * max(x, y) for x in a for y in b)


@dataclass(frozen=True)
class Alignment:
    offset: float
    # (label in a, label in b) for every line the two share.
    pairs: tuple[tuple[str, str], ...]


def _positions(axis: dict[str, float]) -> list[tuple[float, str]]:
    return sorted((pos, label) for label, pos in axis.items())


def _match(a: list[tuple[float, str]], b: list[tuple[float, str]], offset: float) -> list[tuple[str, str]]:
    """Each line of `a` paired with the nearest line of `b` within tolerance,
    each `b` line used once."""
    used: set[int] = set()
    pairs = []
    for pos, label in a:
        best, best_d = None, MATCH_TOL_PT
        for j, (other, other_label) in enumerate(b):
            d = abs(other - (pos + offset))
            if j not in used and d <= best_d:
                best, best_d = j, d
        if best is not None:
            used.add(best)
            pairs.append((label, b[best][1]))
    return pairs


def align(a: dict[str, float], b: dict[str, float], fixed_offset: float | None = None) -> Alignment | None:
    """The translation that lays `a` over `b`, or None when there is no
    unambiguous one.

    Candidate offsets are every pairwise difference, voted into bins one
    tolerance wide — the offset most line pairs agree on. The top two distinct
    candidates are then matched exactly, and the best must win by MIN_MARGIN.
    `fixed_offset` skips the search: two grids on one sheet are drawn in one
    coordinate system, so the only offset that can mean anything is zero.
    """
    pa, pb = _positions(a), _positions(b)
    smaller = min(len({round(p) for p, _ in pa}), len({round(p) for p, _ in pb}))
    if smaller < MIN_MATCHED:
        return None
    need = max(MIN_MATCHED, int(MIN_MATCHED_SHARE * smaller + 0.999))

    if fixed_offset is not None:
        pairs = _match(pa, pb, fixed_offset)
        return Alignment(fixed_offset, tuple(pairs)) if len(pairs) >= need else None

    votes: Counter[int] = Counter()
    for pos_a, _ in pa:
        for pos_b, _ in pb:
            votes[round((pos_b - pos_a) / MATCH_TOL_PT)] += 1
    # A bin and its neighbours: a true offset landing on a bin edge splits its
    # votes, and the pair would lose to a lucky single bin.
    scored = sorted(
        ((votes[k - 1] + votes[k] + votes[k + 1], k) for k in votes), reverse=True
    )
    candidates: list[tuple[int, float, list[tuple[str, str]]]] = []
    tried: list[int] = []
    for _, k in scored:
        if any(abs(k - t) <= 2 for t in tried):
            continue
        tried.append(k)
        # Refine inside the bin: the mean of the differences that voted for it.
        diffs = [
            pos_b - pos_a
            for pos_a, _ in pa
            for pos_b, _ in pb
            if abs((pos_b - pos_a) / MATCH_TOL_PT - k) <= 1
        ]
        offset = sorted(diffs)[len(diffs) // 2]
        pairs = _match(pa, pb, offset)
        candidates.append((len(pairs), offset, pairs))
        if len(tried) >= 6:
            break
    candidates.sort(key=lambda c: -c[0])
    best = candidates[0]
    runner_up = candidates[1][0] if len(candidates) > 1 else 0
    if best[0] < need or best[0] - runner_up < MIN_MARGIN:
        return None
    return Alignment(best[1], tuple(best[2]))


def _renamed(alignment: Alignment | None) -> list[tuple[str, str]]:
    if alignment is None:
        return []
    return [(x, y) for x, y in alignment.pairs if x != y]


def _comparable(a: GridSystem, b: GridSystem, pages: dict[str, Page]) -> bool:
    if a.page_id == b.page_id:
        return a.style != b.style
    da, db = pages[a.page_id].discipline, pages[b.page_id].discipline
    if not (da is None or db is None or da != db):
        return False
    return share_a_scale(a.scales, b.scales) and not a.repeats_a_label() and not b.repeats_a_label()


def _signature(system: GridSystem) -> tuple:
    """Grids that name the same lines the same way, at the same spacing, are
    one grid for this check — every floor plan of a tower shares one."""

    def shape(axis: dict[str, float]) -> tuple:
        if not axis:
            return ()
        origin = min(axis.values())
        return tuple(sorted((label, round((pos - origin) / MATCH_TOL_PT)) for label, pos in axis.items()))

    return (shape(system.along_x), shape(system.along_y))


def _axis_word(pairs: list[tuple[str, str]]) -> str:
    """"numbered" or "lettered" — how a reader names an axis, whichever way
    the sheet is turned."""
    letters = sum(1 for x, _ in pairs if x[:1].isalpha())
    return "lettered" if letters * 2 > len(pairs) else "numbered"


def _system_name(system: GridSystem, pages: dict[str, Page], same_page: bool) -> str:
    label = page_label(pages[system.page_id])
    return f"{label} ({system.style.split()[0]} grid bubbles)" if same_page else label


def _bubble_row(system: GridSystem, labels: list[str], axis: str) -> dict | None:
    """The box around the renamed labels' bubbles at ONE end of their lines.

    A line is bubbled at both ends, so the renamed labels sit in two (or more)
    rows of bubbles across the sheet. The row holding most of them is the one
    a reviewer is sent to; a box around all of them would highlight the sheet.
    """
    # Bubbles of lines placed along x share a y, and the other way round.
    cross = 1 if axis == "along_x" else 0
    rows: list[list[list[float]]] = []
    for rect in sorted(
        (rect for label in labels for rect in system.bubbles.get(label, [])),
        key=lambda rect: (rect[cross] + rect[cross + 2]) / 2,
    ):
        middle = (rect[cross] + rect[cross + 2]) / 2
        if rows and abs(middle - (rows[-1][-1][cross] + rows[-1][-1][cross + 2]) / 2) <= MATCH_TOL_PT * 2:
            rows[-1].append(rect)
        else:
            rows.append([rect])
    if not rows:
        return None
    best = max(rows, key=len)
    x0, y0 = min(r[0] for r in best), min(r[1] for r in best)
    x1, y1 = max(r[2] for r in best), max(r[3] for r in best)
    return unrotated_box(system, x0, y0, x1, y1)


def unrotated_box(system: GridSystem, x0: float, y0: float, x1: float, y1: float) -> dict:
    """A display-space rectangle as the stored evidence box. On a rotated sheet
    the two spaces differ, and a box left in display space puts the reviewer's
    highlight — and the cloud on a marked-up RFI — on the wrong part of the
    drawing (A3.01, /Rotate 90: the title-block strip instead of the grid)."""
    if system.to_unrotated:
        import fitz

        r = fitz.Rect(x0, y0, x1, y1) * fitz.Matrix(*system.to_unrotated)
        r.normalize()
        x0, y0, x1, y1 = r.x0, r.y0, r.x1, r.y1
    return {"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0}


def _evidence(system: GridSystem, page: Page, labels: list[str], name: str, axis: str) -> dict:
    shown = ", ".join(labels[:14]) + ("…" if len(labels) > 14 else "")
    return {
        "documentId": page.document_id,
        "pageNumber": page.page_number,
        "combinedPageNumber": page.combined_page_number,
        "sheetNumber": page.sheet_number,
        "bbox": _bubble_row(system, labels, axis),
        "chunkId": None,
        "quote": f"{name}: grid lines {shown}",
        "role": "finding",
    }


def grid_mismatches(pages: list[Page], systems: list[GridSystem]) -> tuple[list[Finding], list[str]]:
    notes: list[str] = []
    by_page = {p.id: p for p in pages}
    systems = [s for s in systems if s.page_id in by_page]
    if not systems:
        notes.append(
            "Grid check found no grid bubbles on any sheet (a circle holding one "
            "grid label, three or more on a line), so no grid could be compared."
        )
        return [], notes

    # One representative per distinct grid, keeping every page it appears on
    # so evidence can point at more than one sheet.
    unique: dict[tuple, GridSystem] = {}
    for system in systems:
        unique.setdefault((_signature(system), system.page_id, system.style), system)
    reps = list(unique.values())
    if len(reps) > MAX_SYSTEMS:
        notes.append(
            f"Grid check: {len(reps)} distinct grids found; only the first "
            f"{MAX_SYSTEMS} were compared."
        )
        reps = reps[:MAX_SYSTEMS]

    # fingerprint -> accumulating finding
    found: dict[str, dict] = {}
    other_scale: set[tuple[str, str]] = set()
    several_views: set[str] = set()
    for i, a in enumerate(reps):
        for b in reps[i + 1 :]:
            if not _comparable(a, b, by_page):
                if a.page_id != b.page_id:
                    for system in (a, b):
                        if system.repeats_a_label():
                            several_views.add(page_label(by_page[system.page_id]))
                    if not share_a_scale(a.scales, b.scales):
                        other_scale.add(tuple(sorted((page_label(by_page[a.page_id]), page_label(by_page[b.page_id])))))
                continue
            if _signature(a) == _signature(b):
                continue
            same_page = a.page_id == b.page_id
            fixed = 0.0 if same_page else None
            for axis in ("along_x", "along_y"):
                alignment = align(getattr(a, axis), getattr(b, axis), fixed)
                renamed = _renamed(alignment)
                if not renamed:
                    continue
                # Identity of the DISAGREEMENT, whichever sheet is listed
                # first: {6, 9} is the same finding from either side.
                key_pairs = sorted("~".join(sorted(pair)) for pair in renamed)
                fp = fingerprint(CHECK_TYPE, _axis_word(renamed), *key_pairs)
                entry = found.setdefault(
                    fp,
                    {
                        "a": a,
                        "b": b,
                        "renamed": renamed,
                        "shared": len(alignment.pairs),
                        "same_page": same_page,
                        "evidence": [],
                        "places": set(),
                    },
                )
                entry["same_page"] = entry["same_page"] or same_page
                for system, side in ((a, 0), (b, 1)):
                    place = (system.page_id, system.style)
                    if place in entry["places"] or len(entry["evidence"]) >= MAX_EVIDENCE:
                        continue
                    entry["places"].add(place)
                    labels = [pair[side] for pair in renamed]
                    name = _system_name(system, by_page, same_page)
                    entry["evidence"].append(
                        _evidence(system, by_page[system.page_id], labels, name, axis)
                    )

    if other_scale:
        shown = ", ".join(f"{x} / {y}" for x, y in sorted(other_scale)[:6])
        notes.append(
            f"Grid check: {len(other_scale)} sheet pair(s) print different drawing scales and were not "
            f"compared ({shown}{'…' if len(other_scale) > 6 else ''}) — an enlarged plan and an overall plan "
            "line up on paper only by coincidence."
        )
    if several_views:
        notes.append(
            "Grid check: " + ", ".join(sorted(several_views)[:6]) + " show the same grid label in two places "
            "(several views on one sheet), so their grids were not compared with other sheets."
        )
    groups, singles = _merge_by_discipline(found, by_page)
    findings: list[Finding] = [_merged_finding(group, by_page) for group in groups]
    for fp, entry in singles.items():
        a, b, renamed = entry["a"], entry["b"], entry["renamed"]
        same_page = a.page_id == b.page_id
        name_a = _system_name(a, by_page, same_page)
        name_b = _system_name(b, by_page, same_page)
        word = _axis_word(renamed)
        listed = [f"{x} = {y}" for x, y in renamed]
        shown = "; ".join(listed[:14])
        if len(listed) > 14:
            shown += f"; and {len(listed) - 14} more"
        # A sheet printing both names on one line, or many lines renamed, is
        # the drawing stating the disagreement. One or two renamed lines
        # between two sheets can be two levels whose grids genuinely differ.
        if entry["same_page"] or len(renamed) >= 3:
            confidence = "high"
        elif entry["shared"] - len(renamed) >= MIN_MATCHED:
            confidence = "medium"
        else:
            confidence = "low"
        if same_page:
            subject = f"{page_label(by_page[a.page_id])} shows two grids with different {word} line names"
            where = (
                f"{page_label(by_page[a.page_id])} shows two sets of grid bubbles on the "
                f"same lines ({a.style} and {b.style})"
            )
        else:
            subject = f"Grid {word} lines named differently on {name_a} and {name_b}"
            where = f"{name_a} and {name_b} are drawn on the same grid lines"
        # Which side of each "x = y" is which, said once.
        key = (
            f"{a.style.split()[0]} = {b.style.split()[0]}"
            if same_page
            else f"{name_a} = {name_b}"
        )
        question = (
            f"{where}, but {len(renamed)} of them are named differently "
            f"({key}): {shown}. Please confirm which grid "
            "naming governs for layout and coordination, and whether the other "
            "drawings will be reissued to match."
        )
        findings.append(
            Finding(
                check_type=CHECK_TYPE,
                fingerprint=fp,
                confidence=confidence,
                subject=subject,
                question=question,
                evidence=entry["evidence"],
                facts={
                    "sheets": [name_a, name_b],
                    "axis": word,
                    "renamedLines": listed[:20],
                    "sharedLines": entry["shared"],
                },
            )
        )
    return _cap(CHECK_TYPE, findings, notes), notes


def _discipline(page: Page) -> str:
    return (page.discipline or "unknown").replace("_", " ")


def _merge_by_discipline(found: dict[str, dict], pages: dict[str, Page]) -> tuple[list[dict], dict[str, dict]]:
    """One naming dispute between two disciplines is ONE RFI.

    Laid over each other sheet by sheet, the structural and architectural
    grids disagree on every pair of sheets and on both axes: the client's set
    produced five findings — S2.105/A3.01 numbered, S2.105/A3.01 lettered,
    A3.01/S2.107 numbered and twice lettered — where the team wrote one RFI,
    "Confirm the grid layout". So cross-page findings between the same two
    disciplines are merged when their renamings AGREE (architectural 9 is
    structural 6 in every one of them). A finding that contradicts the merged
    mapping is a different dispute and stays its own RFI, as does a finding
    between two grids on one sheet. A lone finding keeps its own fingerprint
    and wording, so an RFI already accepted from it is not proposed again.

    Returns (merged groups of two or more findings, everything else by
    fingerprint)."""
    by_pair: dict[tuple[str, str], list[tuple[str, dict]]] = {}
    singles: dict[str, dict] = {}
    for fp, entry in found.items():
        da, db = _discipline(pages[entry["a"].page_id]), _discipline(pages[entry["b"].page_id])
        if entry["same_page"] or da == db:
            singles[fp] = entry
            continue
        by_pair.setdefault(tuple(sorted((da, db))), []).append((fp, entry))

    groups = []
    for (left, right), members in by_pair.items():
        # Biggest disagreement first: it sets the mapping the others must match.
        members.sort(key=lambda m: -len(m[1]["renamed"]))
        fwd: dict[str, str] = {}
        back: dict[str, str] = {}
        merged: list[dict] = []
        for fp, entry in members:
            flip = _discipline(pages[entry["a"].page_id]) != left
            oriented = [(y, x) if flip else (x, y) for x, y in entry["renamed"]]
            if any(fwd.get(l, r) != r or back.get(r, l) != l for l, r in oriented):
                singles[fp] = entry
                continue
            for l, r in oriented:
                fwd[l], back[r] = r, l
            merged.append((fp, entry))
        if len(merged) == 1:
            fp, entry = merged[0]
            singles[fp] = entry
        elif merged:
            groups.append({"left": left, "right": right, "members": [e for _, e in merged], "mapping": fwd})
    return groups, singles


def _merged_finding(group: dict, pages: dict[str, Page]) -> Finding:
    left, right, mapping = group["left"], group["right"], group["mapping"]
    # Every sheet drawn with each naming, read off the evidence: a grid shared
    # by several sheets of one discipline is compared once, and the RFI must
    # still name all of them.
    by_place = {(p.document_id, p.page_number): p for p in pages.values()}
    sheets: dict[str, list[str]] = {left: [], right: []}
    evidence: list[dict] = []
    places: set = set()
    for entry in group["members"]:
        for item in entry["evidence"]:
            page = by_place.get((item["documentId"], item["pageNumber"]))
            if page is not None:
                label = page_label(page)
                if label not in sheets[_discipline(page)]:
                    sheets[_discipline(page)].append(label)
            place = (item["documentId"], item["pageNumber"], item["quote"])
            if place not in places and len(evidence) < MAX_EVIDENCE:
                places.add(place)
                evidence.append(item)

    def listed(names: list[str]) -> str:
        return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]

    def order(label: str) -> tuple:
        try:
            return (0, -float(label))
        except ValueError:
            return (1, label)

    pairs = sorted(mapping.items(), key=lambda kv: order(kv[0]))
    numbered = [f"{l} = {r}" for l, r in pairs if not l[:1].isalpha()]
    lettered = [f"{l} = {r}" for l, r in pairs if l[:1].isalpha()]
    parts = []
    if numbered:
        parts.append("numbered lines " + "; ".join(numbered[:14]) + ("; …" if len(numbered) > 14 else ""))
    if lettered:
        parts.append("lettered lines " + "; ".join(lettered[:14]) + ("; …" if len(lettered) > 14 else ""))
    word = " and ".join(w for w, items in (("numbered", numbered), ("lettered", lettered)) if items)
    question = (
        f"The {left} drawings ({listed(sheets[left])}) and the {right} drawings "
        f"({listed(sheets[right])}) are drawn on the same grid lines, but {len(mapping)} of them are named "
        f"differently ({left} = {right}): {'. '.join(parts)}. Please confirm which grid naming governs for "
        "layout and coordination, and whether the other drawings will be reissued to match."
    )
    return Finding(
        check_type=CHECK_TYPE,
        fingerprint=fingerprint(CHECK_TYPE, "disciplines", left, right, *sorted(f"{l}~{r}" for l, r in mapping.items())),
        confidence="high" if len(mapping) >= 3 else "medium",
        subject=f"Grid {word} lines named differently in the {left} and {right} drawings",
        question=question,
        evidence=evidence,
        facts={
            "sheets": sheets[left] + sheets[right],
            "axis": word,
            "renamedLines": (numbered + lettered)[:20],
            "sharedLines": max(e["shared"] for e in group["members"]),
        },
    )

