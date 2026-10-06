"""Phase 1 of the full AI scan: what every page IS, read off the PDF.

The full scan sends the model pairs of sheets that should agree, so it first
has to know, for every page: is it a plan (the only kind it pairs), which
level its drawing titles name, which scales it prints, and where its grid
lines are. None of that needs a model, and none of it may be guessed — a pair
built on a wrong level is the false RFI the client already rejected (A3.03
Level 4 against A3.05 Level 6). So a page whose level cannot be read, or whose
titles name two levels, is LEFT OUT of the AI pass and the plan says so.

Read once per page and cached on `pages` (sheetKind, level, scales,
gridSummary, factsVersion); bump FACTS_VERSION when the reading changes.

Everything that decides is pure (`classify_kind`, `title_level`) and tested
without a PDF; `read_page` is the only part that touches PyMuPDF.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass, field

log = logging.getLogger("worker.sheet_facts")

FACTS_VERSION = 4  # 2: kinked leaders; 3: floors named as words; 4: short labels never set the title size, bubbles drawn as segment rings

# Mirrors SHEET_KINDS in @cdip/shared; test_sheet_facts reads the TypeScript.
SHEET_KINDS = ("plan", "enlarged_plan", "section", "elevation", "detail", "schedule", "notes", "cover", "other")
PLAN_KINDS = ("plan", "enlarged_plan")

# A drawing title is among the LARGEST text on a sheet. Relative, not absolute:
# the client's sheets title at 24.9pt beside a 28pt sheet number on one set and
# at 24.7pt beside a 62pt sheet number on another. The second-largest size is
# the reference (the largest is often a logo or the sheet number alone).
TITLE_SIZE_SHARE = 0.55
TITLE_MAX_CHARS = 70
REFERENCE_MIN_CHARS = 4
# A label bubbled at two places this far apart along its own axis is two
# views on one sheet, not one grid (the same rule as rfi_grid.REPEAT_PT).
REPEAT_PT = 60.0

# Ordered: the first kind any title line names wins. A plan sheet with a
# "SECTION" callout is still a plan; a cover with a drawing list is a cover.
_KIND_RULES: list[tuple[str, re.Pattern]] = [
    ("cover", re.compile(r"\b(?:SHEET\s+INDEX|DRAWING\s+(?:LIST|INDEX)|COVER\s+SHEET|TITLE\s+SHEET)\b")),
    ("enlarged_plan", re.compile(r"\bENLARGED\b.*\b(?:PLANS?|EXHIBITS?|DIAGRAMS?)\b")),
    ("plan", re.compile(r"(?<!KEY )\b(?:PLANS?|EXHIBITS?)\b")),
    ("schedule", re.compile(r"\bSCHEDULES?\b")),
    ("section", re.compile(r"\bSECTIONS?\b")),
    ("elevation", re.compile(r"\bELEVATIONS?\b")),
    ("detail", re.compile(r"\bDETAILS?\b")),
    ("notes", re.compile(r"\b(?:GENERAL\s+NOTES|NOTES|SPECIFICATIONS?|ABBREVIATIONS)\b")),
]
# Words that make a line a pointer or a sub-heading rather than the sheet's
# own title: "SEE PLAN", "KEY PLAN", "PLAN NOTES", "TOP OF CONCRETE = SEE PLAN".
_NOT_A_TITLE = re.compile(r"\b(?:SEE|REFER|REF|PER|KEY\s+PLAN|PLAN\s+NOTES?|LEGEND)\b|=")

_LEVEL = re.compile(r"\bLEVELS?\s*([0-9]{1,3}|[A-Z]{1,2}\d?)\b(?:\s*(?:-|TO|THRU|THROUGH)\s*([0-9]{1,3})\b)?")
_DRAWING_KIND = re.compile(r"\b(?:PLANS?|EXHIBITS?|FRAMING|FORMING|LAYOUT|DIAGRAMS?)\b")


def _norm_level(token: str) -> str:
    return re.sub(r"^0+(?=[0-9])", "", token)


_ORDINALS = {
    "FIRST": 1, "SECOND": 2, "THIRD": 3, "FOURTH": 4, "FIFTH": 5, "SIXTH": 6, "SEVENTH": 7,
    "EIGHTH": 8, "NINTH": 9, "TENTH": 10, "ELEVENTH": 11, "TWELFTH": 12,
}
# "SECOND FLOOR", "2ND FLOOR", "FLOOR 2". A numbered floor IS a numbered
# level: first floor is level 1 whether the set follows the US or the UK
# convention, which differ only on what GROUND means.
_ORDINAL_FLOOR = re.compile(r"\b(" + "|".join(_ORDINALS) + r")\s+(?:FLOOR|STOREY|STORY)\b")
_NTH_FLOOR = re.compile(r"\b([0-9]{1,3})(?:ST|ND|RD|TH)\s+(?:FLOOR|STOREY|STORY)\b")
_FLOOR_N = re.compile(r"\bFLOOR\s+([0-9]{1,3})\b")
# Names with no number. Each pairs only with ITSELF: "GROUND FLOOR" is level 1
# in the US and level 0 in the UK, so mapping it onto a number could pair two
# different floors — the false RFI this whole reader exists to prevent.
_NAMED_LEVELS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(?:GROUND|GRADE)\s+(?:FLOOR|LEVEL)\b"), "GROUND FLOOR"),
    (re.compile(r"\b(?:LOWER|UPPER)\s+(?:GROUND\s+)?LEVEL\b"), None),  # filled from the match
    (re.compile(r"\bBASEMENT(?:\s+(?:LEVEL\s+)?([0-9]{1,2}))?\b"), "BASEMENT"),
    (re.compile(r"\bMEZZANINE\b"), "MEZZANINE"),
    (re.compile(r"\bPENTHOUSE\b"), "PENTHOUSE"),
    (re.compile(r"\bROOF\b"), "ROOF"),
]


def level_in(text: str | None) -> str | None:
    """"LEVEL 5", "LEVEL 7-13" (a range of floors drawn once), or None.
    "LEVEL 01" is "LEVEL 1", as in rfi_columns.level_of.

    The first full scan of another client set paired NOTHING: 33 plans, 11 with
    a level read, because only "LEVEL n" was understood and that set says
    "FIRST FLOOR PLAN", "ROOF PLAN". So a numbered floor in any of its usual
    spellings reads as "LEVEL n", and a floor with a NAME (ground, basement,
    roof…) reads as that name and pairs only with the same name."""
    upper = " ".join((text or "").upper().split())
    m = _LEVEL.search(upper)
    if m:
        first = _norm_level(m.group(1))
        if m.group(2) and first.isdigit() and int(m.group(2)) > int(first):
            return f"LEVEL {first}-{_norm_level(m.group(2))}"
        return f"LEVEL {first}"
    for pattern in (_ORDINAL_FLOOR, _NTH_FLOOR, _FLOOR_N):
        m = pattern.search(upper)
        if m:
            token = m.group(1)
            return f"LEVEL {_ORDINALS.get(token) or _norm_level(token)}"
    for pattern, name in _NAMED_LEVELS:
        m = pattern.search(upper)
        if not m:
            continue
        if name is None:
            return " ".join(m.group(0).split())
        if name == "BASEMENT" and m.group(1):
            return f"BASEMENT {_norm_level(m.group(1))}"
        return name
    return None


def title_level(titles: list[str]) -> str | None:
    """The ONE level the sheet's drawing titles name, or None when they name
    none or several. A title names a kind of drawing ("FORMING PLAN - LEVEL
    5", "CONCRETE EXHIBIT - LEVEL 14"); a bare "LEVEL 14" counts only when no
    titled line exists. The client's A3.32 titles "LEVEL 6" and "LEVEL 7-13"
    side by side — two floors on one sheet, so no one level, so no pair."""
    titled, bare = set(), set()
    for line in titles:
        upper = " ".join(line.upper().split())
        if len(upper) > TITLE_MAX_CHARS or _NOT_A_TITLE.search(upper):
            continue
        level = level_in(upper)
        if not level:
            continue
        if _DRAWING_KIND.search(upper):
            titled.add(level)
        elif upper.replace(" ", "") == level.replace(" ", ""):
            bare.add(level)
    levels = titled or bare
    return levels.pop() if len(levels) == 1 else None


def classify_kind(titles: list[str], has_grid: bool) -> str:
    """What the sheet is, from its title lines. With no title word at all, a
    sheet carrying a grid is a plan and anything else is "other"."""
    for kind, pattern in _KIND_RULES:
        for line in titles:
            upper = " ".join(line.upper().split())
            if len(upper) > TITLE_MAX_CHARS:
                continue
            if kind in ("plan", "enlarged_plan") and _NOT_A_TITLE.search(upper):
                continue
            if pattern.search(upper):
                return kind
    return "plan" if has_grid else "other"


def title_lines(sized_lines: list[tuple[float, str]], extra: list[str] = ()) -> list[str]:
    """The lines big enough to be titles: TITLE_SIZE_SHARE of the second
    largest size on the page. `extra` is the title-block region the user
    marked, which carries the sheet title on most sets."""
    # The reference ignores text of under REFERENCE_MIN_CHARS: a detail
    # number in its bubble ("01", 51.6pt on a client's A1.01) or a logo
    # ("GMC") is huge and says nothing about how big a title is printed, and it
    # set the bar above "FLOOR PLAN - LEVEL 1 OVERALL" at 25.5pt — so every
    # architectural plan of that set read as "other" and nothing paired.
    sizes = sorted(
        {round(size, 1) for size, text in sized_lines if len(text.replace(" ", "")) >= REFERENCE_MIN_CHARS},
        reverse=True,
    ) or sorted({round(size, 1) for size, text in sized_lines if text.strip()}, reverse=True)
    if not sizes:
        return list(extra)
    reference = sizes[1] if len(sizes) > 1 else sizes[0]
    out, seen = [], set()
    for size, text in sorted(sized_lines, key=lambda st: -st[0]):
        text = " ".join(text.split())
        if size >= TITLE_SIZE_SHARE * reference and 3 < len(text) <= TITLE_MAX_CHARS and text not in seen:
            seen.add(text)
            out.append(text)
    for line in extra:
        line = " ".join(line.split())
        if line and line not in seen:
            seen.add(line)
            out.append(line)
    return out


def repeats_a_label(columns: dict, rows: dict, bubbles: dict[str, list[tuple[float, float]]]) -> bool:
    """A label bubbled at two places along its own axis: several views on one
    sheet. Positions on such a sheet are paper-space and cannot be aligned
    with another sheet (S1.102 against A3.36's two enlarged details)."""
    for axis, i in ((columns, 0), (rows, 1)):
        for label in axis:
            centres = [c[i] for c in bubbles.get(label, [])]
            if len(centres) > 1 and max(centres) - min(centres) > REPEAT_PT:
                return True
    return False


@dataclass
class PageFacts:
    page_id: str
    document_id: str
    page_number: int
    combined_page_number: int | None
    sheet_number: str | None
    discipline: str | None
    kind: str | None = None
    level: str | None = None
    scales: list[float] = field(default_factory=list)
    # {"x": {label: pos}, "y": {label: pos}, "size": [w, h], "repeats": bool},
    # DISPLAY space. Empty axes when no grid was read.
    grid: dict = field(default_factory=dict)

    @property
    def label(self) -> str:
        return self.sheet_number or (f"page {self.combined_page_number}" if self.combined_page_number else f"page {self.page_number}")

    def has_grid(self) -> bool:
        return len(self.grid.get("x") or {}) >= 2 and len(self.grid.get("y") or {}) >= 2

    def ref(self) -> dict:
        """The shape the plan screen shows (RfiFullScanSheetRef)."""
        return {
            "pageId": self.page_id,
            "documentId": self.document_id,
            "pageNumber": self.page_number,
            "combinedPageNumber": self.combined_page_number,
            "sheetNumber": self.sheet_number,
            "discipline": self.discipline,
            "level": self.level,
            "kind": self.kind,
        }


def read_page(page, region_text: str | None = None) -> dict:
    """The facts of one fitz page, annotations stripped first (a finding must
    come from the drawings as issued)."""
    import grid
    import plan_match

    clean = grid.without_markup(page)
    sized: list[tuple[float, str]] = []
    for block in clean.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            text = " ".join(s["text"] for s in line["spans"]).strip()
            if text:
                sized.append((max(s["size"] for s in line["spans"]), text))
    titles = title_lines(sized, (region_text or "").splitlines())
    # The grid read is the slow part (every drawing on the sheet, 2-4s); a
    # sheet whose title already says section, schedule or notes never pairs,
    # so it is not paid for.
    named = classify_kind(titles, has_grid=False)
    columns, rows, bubbles = {}, {}, {}
    if named in ("plan", "enlarged_plan", "other"):
        try:
            columns, rows, bubbles = grid.page_grid_with_bubbles(clean)
        except Exception as exc:  # a sheet whose grid cannot be read has no grid
            log.debug("grid read failed: %s", exc)
    rect = clean.rect
    grid_summary = {
        "x": {k: round(v, 2) for k, v in columns.items()},
        "y": {k: round(v, 2) for k, v in rows.items()},
        "size": [round(rect.width, 2), round(rect.height, 2)],
        "repeats": repeats_a_label(columns, rows, bubbles),
    }
    has_grid = len(columns) >= 2 and len(rows) >= 2
    return {
        "kind": classify_kind(titles, has_grid),
        # The big titles first. When they name no level, the smaller view
        # title under the drawing does: S1.101's big title breaks after
        # "Foundation Plan - Level" and its view title reads "Foundation
        # Plan - Level 1". Still exactly ONE level or none.
        "level": title_level(titles) or title_level([text for _, text in sized]),
        "scales": sorted({round(s, 3) for s in plan_match.page_scales(clean)}),
        "grid": grid_summary,
    }


# --- The catalogue -----------------------------------------------------------------


def live_pages(project_id: str) -> tuple[list[dict], dict[str, int]]:
    """Every page the full scan may look at, and counts of those it may not.

    The same rules as every RFI path: a superseded document is history, a
    document excluded from RFI analysis (an RFI itself, the answer key) is
    never input, and a document still processing has no pages to trust."""
    import db

    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT p.id, p."documentId", p."pageNumber", p."combinedPageNumber", p."sheetNumber",
                   p.discipline, p."sheetRegionText", p."sheetKind", p.level, p.scales, p."gridSummary",
                   p."factsVersion", d."spacesKey", d."includeInRfiAnalysis", d.status::text,
                   d."supersededAt" IS NOT NULL
              FROM pages p JOIN documents d ON d.id = p."documentId"
             WHERE d."projectId" = %s
             ORDER BY p."combinedPageNumber" NULLS LAST, p."pageNumber"
            """,
            (project_id,),
        ).fetchall()
    keys = (
        "id", "documentId", "pageNumber", "combinedPageNumber", "sheetNumber", "discipline", "regionText",
        "sheetKind", "level", "scales", "gridSummary", "factsVersion", "spacesKey", "include", "docStatus", "superseded",
    )
    live, excluded = [], {"superseded": 0, "excludedFromRfi": 0, "notProcessed": 0}
    for row in rows:
        r = dict(zip(keys, row))
        if r["superseded"]:
            excluded["superseded"] += 1
        elif not r["include"]:
            excluded["excludedFromRfi"] += 1
        elif r["docStatus"] != "completed":
            excluded["notProcessed"] += 1
        else:
            live.append(r)
    return live, excluded


def _facts(row: dict) -> PageFacts:
    def parsed(value):
        return json.loads(value) if isinstance(value, str) else value

    return PageFacts(
        page_id=row["id"],
        document_id=row["documentId"],
        page_number=row["pageNumber"],
        combined_page_number=row["combinedPageNumber"],
        sheet_number=row["sheetNumber"],
        discipline=row["discipline"],
        kind=row["sheetKind"],
        level=row["level"],
        scales=list(parsed(row["scales"]) or []),
        grid=parsed(row["gridSummary"]) or {},
    )


def catalogue(project_id: str, progress=None) -> tuple[list[PageFacts], dict[str, int], int]:
    """(facts for every live page, excluded counts, pages read now).

    Pages already read at FACTS_VERSION come from Postgres; the rest are read
    one document at a time from a temporary download, one page at a time —
    never the whole set in memory. `progress(done, total)` is called as pages
    finish."""
    import db
    import fitz
    import storage

    rows, excluded = live_pages(project_id)
    todo: dict[str, list[dict]] = {}
    for row in rows:
        if row["factsVersion"] != FACTS_VERSION:
            todo.setdefault(row["documentId"], []).append(row)
    total = sum(len(v) for v in todo.values())
    done = 0
    for document_id, doc_rows in todo.items():
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "original.pdf")
            storage.download_to_file(doc_rows[0]["spacesKey"], path)
            pdf = fitz.open(path)
            try:
                for row in doc_rows:
                    index = row["pageNumber"] - 1
                    facts = {"kind": "other", "level": None, "scales": [], "grid": {}}
                    if 0 <= index < pdf.page_count:
                        try:
                            facts = read_page(pdf.load_page(index), row["regionText"])
                        except Exception as exc:  # one unreadable page is "other", not a failed plan
                            log.warning("full scan: could not read page %s: %s", row["id"][:8], exc)
                    with db.connect() as conn:
                        conn.execute(
                            'UPDATE pages SET "sheetKind" = %s, level = %s, scales = %s::jsonb, '
                            '"gridSummary" = %s::jsonb, "factsVersion" = %s WHERE id = %s',
                            (facts["kind"], facts["level"], json.dumps(facts["scales"]), json.dumps(facts["grid"]),
                             FACTS_VERSION, row["id"]),
                        )
                    row.update(sheetKind=facts["kind"], level=facts["level"], scales=facts["scales"],
                               gridSummary=facts["grid"], factsVersion=FACTS_VERSION)
                    done += 1
                    if progress:
                        progress(done, total)
            finally:
                pdf.close()
    return [_facts(r) for r in rows], excluded, done


def summary(facts: list[PageFacts], excluded: dict[str, int]) -> dict:
    """RfiFullScanCatalogueDto."""
    by_kind: dict[str, int] = {}
    for f in facts:
        by_kind[f.kind or "other"] = by_kind.get(f.kind or "other", 0) + 1
    return {
        "pages": len(facts),
        "byKind": by_kind,
        "withLevel": sum(1 for f in facts if f.level),
        "withScale": sum(1 for f in facts if f.scales),
        "withGrid": sum(1 for f in facts if f.has_grid()),
        "excluded": sum(excluded.values()),
        "excludedBy": dict(excluded),
    }
