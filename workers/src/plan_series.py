"""A level missing from a series of plans — the check behind the client's RFI 007.

UT Law's structural set has a Forming Plan for Level 2, 3-4, 5, 7-13, 14, Roof
and Hi-Roof, and none for Level 6 — while its PT Plan, slab and framing series
all have one. The BIM team asked for it ("Please provide the forming plan at
level 6 as it is not included in the current issued drawing set"). Nothing in
the drawings SAYS a sheet is missing; the gap is only visible as a hole in a
sequence, which is exactly the kind of fact code finds and a model, reading one
sheet at a time, never sees.

How it decides, each step built for precision:

  * A SERIES is a plan title with a level: "FORMING PLAN - LEVEL 5",
    "PT PLAN - LEVEL 7-13". Only whole title lines count, the plan type must
    end in PLAN, and an ENLARGED / PARTIAL plan or a qualified one
    ("ENLARGED FORMING PLAN - STAIR 2 - LEVEL 2") is never a series: those
    show some levels by design, and every level they skip would be a "gap".
  * A series needs THREE distinct numbered levels before its gaps mean
    anything, and only a level strictly BETWEEN its lowest and highest one can
    be missing (a foundation series that stops at Level 1 is not missing 2).
  * The missing level must EXIST in the set: another series of the same
    discipline has it. A building with no Level 6 at all has no gap.
  * The SHEET INDEX is read as entries, "S2.103 Forming Plan - Level 3-4",
    and never as drawings: on UT Law the index (A1.00) prints every series of
    every discipline, and read as titles it made the architectural sheet a
    Forming Plan for all fourteen levels. An entry for a sheet that IS in the
    set covers its levels — S2.103's own view title says only "Level 3", so
    without the index Level 4 was reported missing.
  * An entry for a sheet that is NOT in the set is the other half of the
    answer. When it is the ONLY indexed sheet of that series missing, the set
    was issued without it: that is the client's RFI 007 exactly (the index
    lists "S2.106 Forming Plan - Level 6"; the issued set has no S2.106), and
    the finding names the sheet. When several of the series' indexed sheets
    are missing, the upload is partial — "not uploaded" is never an RFI (the
    rule missing sheet references follow) and it goes to the notes.

Pure: it reads the chunks' text and the pages' disciplines, nothing else.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

MIN_LEVELS = 3
TITLE_MAX = 60

# "FORMING PLAN - LEVEL 5", "PT PLAN – LEVELS 7-13", "FLOOR PLAN: LEVEL 2".
_TITLE = re.compile(
    r"^(?P<series>[A-Z][A-Z0-9 &/.]*?\bPLANS?)\s*[-–:]\s*LEVELS?\s*(?P<lo>\d{1,3})(?:\s*(?:-|–|TO|THRU)\s*(?P<hi>\d{1,3}))?$"
)
_NOT_A_SERIES = re.compile(r"^(?:ENLARGED|PARTIAL|KEY|TYPICAL|SITE)\b")


@dataclass
class Series:
    discipline: str | None
    name: str
    # level -> [(page, chunk, title as printed)]: the sheets' own titles, and
    # index entries for sheets that are in the set.
    levels: dict[int, list] = field(default_factory=dict)
    # Sheet labels in the set carrying this series.
    sheets: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class IndexEntry:
    """One line of the sheet index: "S2.106" + "Forming Plan - Level 6"."""
    sheet: str           # as printed
    key: str             # normalized ("S2106")
    discipline: str | None
    series: str
    levels: tuple[int, ...]
    title: str
    page: object
    chunk: object


def read_title(line: str) -> tuple[str, list[int], str] | None:
    """(series name, levels, title as normalised) for a plan title line, or
    None. Pure. "FORMING PLAN - LEVEL 7-13" -> ("FORMING PLAN", [7..13])."""
    text = " ".join(line.upper().split())
    if not text or len(text) > TITLE_MAX:
        return None
    m = _TITLE.match(text)
    if not m:
        return None
    series = re.sub(r"\bPLANS\b", "PLAN", " ".join(m.group("series").split()))
    if _NOT_A_SERIES.match(series) or " - " in series:
        return None
    lo = int(m.group("lo"))
    hi = int(m.group("hi")) if m.group("hi") else lo
    if hi < lo or hi - lo > 60:
        return None
    return series, list(range(lo, hi + 1)), text


def _sheet_line():
    from rfi_checks import _SHEET_TOKEN
    return re.compile(r"^\s*" + _SHEET_TOKEN + r"(?:\s+(?P<rest>.+?))?\s*$")


def index_pages(chunks) -> set[str]:
    """Pages carrying the project's sheet index (rfi_checks.index_page_ids)."""
    from rfi_checks import index_page_ids

    return index_page_ids(chunks)


def index_entries(pages, chunks, index_ids: set[str]) -> list[IndexEntry]:
    """Plan-series entries of the sheet index: a sheet number followed, on
    its own line or the next, by a series title. Pure."""
    from classify import PREFIX_TO_DISCIPLINE
    from rfi_checks import normalize, signature

    by_page = {p.id: p for p in pages}
    line_re = _sheet_line()
    out = []
    for chunk in chunks:
        if chunk.page_id not in index_ids:
            continue
        lines = chunk.text.splitlines()
        for i, line in enumerate(lines):
            # A table row, "| S2.106 | Forming Plan - Level 6 |": number, title.
            cells = [c.strip() for c in line.strip().strip("|").split("|")] if line.lstrip().startswith("|") else []
            m = line_re.match((cells[0] if cells else line).upper())
            if not m:
                continue
            if cells:
                rest = next((c for c in cells[1:] if c), "")
            else:
                rest = m.group("rest") or (lines[i + 1] if i + 1 < len(lines) else "")
            read = read_title(rest)
            sig = signature(normalize(m.group(1)))
            if read is None or not sig:
                continue
            name, levels, title = read
            out.append(IndexEntry(m.group(1), normalize(m.group(1)), PREFIX_TO_DISCIPLINE.get(sig[0]),
                                  name, tuple(levels), title, by_page.get(chunk.page_id), chunk))
    return out


def collect(pages, chunks, index: list[IndexEntry] | None = None,
            index_ids: set[str] = frozenset()) -> dict[tuple[str | None, str], Series]:
    """Every plan series in the set, keyed by (discipline, name): the sheets'
    own titles, plus index entries for sheets that are in the set."""
    from rfi_checks import normalize

    by_page = {p.id: p for p in pages}
    out: dict[tuple[str | None, str], Series] = {}
    for chunk in chunks:
        page = by_page.get(chunk.page_id)
        if page is None or chunk.page_id in index_ids:
            continue
        for line in chunk.text.splitlines():
            read = read_title(line)
            if read is None:
                continue
            name, levels, title = read
            series = out.setdefault((page.discipline, name), Series(page.discipline, name))
            series.sheets.add(_sheet(page))
            for level in levels:
                series.levels.setdefault(level, []).append((page, chunk, title))
    in_set = {normalize(p.sheet_number): p for p in pages if p.sheet_number}
    for e in index or []:
        page = in_set.get(e.key)
        series = out.get((e.discipline, e.series))
        # Only a series the set's own sheets carry; an index entry alone
        # starts nothing.
        if page is None or series is None:
            continue
        series.sheets.add(_sheet(page))
        for level in e.levels:
            series.levels.setdefault(level, []).append((e.page, e.chunk, e.title))
    return out


@dataclass(frozen=True)
class Gap:
    series: Series
    level: int
    # Other series of the discipline drawing that level.
    others: list
    # The index entry naming the sheet for this level, when the sheet is not
    # in the set and is the only one of its series missing.
    listed: IndexEntry | None = None


def gaps(series: dict[tuple[str | None, str], Series], index: list[IndexEntry] | None = None,
         in_set: set[str] = frozenset()) -> tuple[list[Gap], list[str]]:
    """Levels missing from a series, and notes for those the index explains
    as not uploaded. `in_set` holds the normalized sheet numbers of the set.
    Pure."""
    out, notes = [], []
    for (discipline, name), s in series.items():
        present = sorted(s.levels)
        if len(present) < MIN_LEVELS or len(s.sheets) < 2:
            continue
        indexed = [e for e in index or [] if e.discipline == discipline and e.series == name]
        absent = {e.key for e in indexed if e.key not in in_set}
        for level in range(present[0] + 1, present[-1]):
            if level in s.levels:
                continue
            listed = [e for e in indexed if level in e.levels]
            if listed:
                entry = listed[0]
                if absent == {entry.key}:
                    out.append(Gap(s, level, [], entry))
                else:
                    notes.append(
                        f"{display(name)} - Level {level} is listed in the sheet index as {entry.sheet}, "
                        f"which is not in the upload, along with {len(absent) - 1} other "
                        f"{display(name)} sheet(s) — upload the rest of the set to check it.")
                continue
            others = [o for (d, n), o in series.items()
                      if d == discipline and o is not s and level in o.levels
                      and len(o.levels) >= MIN_LEVELS]
            if others:
                out.append(Gap(s, level, others))
    return out, notes


def display(name: str) -> str:
    """"FORMING PLAN" -> "Forming Plan", "PT PLAN" -> "PT Plan": a short word
    is an abbreviation and stays as printed."""
    return " ".join(w if len(w) <= 3 and w.isalpha() and w != "AND" else w.capitalize() for w in name.split())


def _sheet(page) -> str:
    return page.sheet_number or f"page {page.combined_page_number or page.page_number}"


def _ranges(levels: list[int]) -> list[str]:
    """[2, 3, 4, 5, 7, 8, 9] -> ["2-5", "7-9"]."""
    out, start = [], None
    for i, n in enumerate(levels):
        if start is None:
            start = n
        if i + 1 == len(levels) or levels[i + 1] != n + 1:
            out.append(str(start) if start == n else f"{start}-{n}")
            start = None
    return out


def missing_plan_levels(pages, chunks) -> tuple[list, list[str]]:
    """Findings for every level missing from a plan series. Returns
    (findings, notes) like every check in rfi_checks."""
    from rfi_checks import Finding, _evidence, fingerprint

    from rfi_checks import normalize

    index_ids = index_pages(chunks)
    index = index_entries(pages, chunks, index_ids)
    in_set = {normalize(p.sheet_number) for p in pages if p.sheet_number}
    found, notes = gaps(collect(pages, chunks, index, index_ids), index, in_set)
    findings = []
    for gap in found:
        s, level, others, listed = gap.series, gap.level, gap.others, gap.listed
        title = f"{display(s.name)} - Level {level}"
        below = max(lv for lv in s.levels if lv < level)
        above = min(lv for lv in s.levels if lv > level)
        evidence, seen = [], set()
        for lv, role in ((below, "finding"), (above, "finding")):
            for page, chunk, printed in s.levels[lv][:1]:
                if (page.id, printed) in seen:
                    continue
                seen.add((page.id, printed))
                evidence.append(_evidence(page, chunk, printed, role=role, term=printed.split(" - ")[0]))
        shown_in = []
        for o in others[:3]:
            page, chunk, printed = o.levels[level][0]
            shown_in.append(f"{display(o.name)} - Level {level} ({_sheet(page)})")
            evidence.append(_evidence(page, chunk, printed, role="context", term=printed.split(" - ")[0]))
        spans = _ranges(sorted(s.levels))
        covered = ("Level " if len(spans) == 1 and "-" not in spans[0] else "Levels ") + (
            spans[0] if len(spans) == 1 else ", ".join(spans[:-1]) + " and " + spans[-1])
        sheets = sorted(s.sheets)
        in_list = f"{', '.join(sheets[:6])}{'…' if len(sheets) > 6 else ''}"
        if listed is not None:
            where = _sheet(listed.page) if listed.page is not None else "the cover sheet"
            evidence.append(_evidence(listed.page, listed.chunk, f"{listed.sheet} {listed.title}",
                                      role="finding", term=listed.sheet))
            subject = f"{listed.sheet} {title} is in the sheet index but not in the set"
            question = (
                f"The sheet index on {where} lists {listed.sheet} {title}, but {listed.sheet} is not "
                f"in the issued drawing set; the {display(s.name)} sheets in the set ({in_list}) cover "
                f"{covered}. Please issue {listed.sheet} {title}, or confirm which sheet shows Level {level}."
            )
            confidence = "high"
        else:
            subject = f"No {title} in the drawing set"
            question = (
                f"The {display(s.name)} sheets in the set ({in_list}) cover {covered}, but no {title} "
                f"is included, although Level {level} is drawn in {'; '.join(shown_in)}. "
                f"Please issue the {title}, or confirm which sheet shows it."
            )
            confidence = "high" if len(others) >= 2 else "medium"
        findings.append(Finding(
            check_type="missing_plan_level",
            fingerprint=fingerprint("missing_plan_level", s.discipline or "", s.name, str(level)),
            confidence=confidence,
            subject=subject,
            question=question,
            evidence=evidence,
            facts={"series": display(s.name), "missingLevel": level, "covered": covered,
                   "sheets": sheets, "shownIn": shown_in,
                   **({"indexedSheet": listed.sheet} if listed is not None else {})},
        ))
    return findings, notes
