"""Deterministic checks that find RFI-worthy gaps in a drawing set.

This module DECIDES what is missing. `rfi_scan.py` hands each finding to a
model to be worded as a question, and that is all the model does. The split
is the design, not a convenience:

    A model asked "what is missing from these drawings?" writes a fluent,
    confident list, and nothing tells the real items from the invented ones.
    An RFI that is not real costs an engineer an afternoon and costs the tool
    its credibility, so every finding here comes from the project's own data
    and carries the evidence a person needs to check it in one click.

Four checks, each precise before it is thorough — a missed gap is found the
normal way, a false one is a question someone has to answer:

  dangling_reference   a sheet the drawings point at ("SEE 5/S-501") that is
                       not in the set.
  unscheduled_mark     a mark on a plan (PC4) whose family has a schedule in
                       the set (PC1, PC2, PC3) with no row for it.
  open_item_note       text the drafter left open: TBD, TO BE DETERMINED,
                       ???, verify in field.
  grid_mismatch        one grid line named differently by two drawings
                       (rfi_grid.py — read from the PDF's geometry, the one
                       check that is not built on text).

Everything here is pure — pages and chunks in, findings out — so every rule
and every guard is tested without a database or a PDF. Only `kind="text"`
chunks are ever passed in: a description is a vision model's account of the
drawing, and a finding built on one would be a model's claim wearing a
check's confidence.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from classify import PREFIX_TO_DISCIPLINE

# Keys written into rfi_candidates.checkType. Mirrored as RFI_CHECK_LABELS in
# @cdip/shared, and test_rfi_checks reads that file to fail on a drift — a
# check the UI has no label for renders as its raw key.
CHECK_TYPES = ("dangling_reference", "unscheduled_mark", "open_item_note", "grid_mismatch")

# Evidence kept per finding. A TBD repeated in the general notes of forty
# sheets is ONE open item; forty evidence rows would bury the one that matters.
MAX_EVIDENCE = 5
# A scan that proposes three hundred RFIs has stopped being reviewable. The cap
# is per check so one noisy check cannot crowd the others out, and hitting it
# is reported in the scan's notes rather than silently truncating.
MAX_FINDINGS_PER_CHECK = 100
# A schedule needs at least this many marks of a family before a mark missing
# from it means anything: one row is as likely a note as a schedule.
MIN_SCHEDULE_MARKS = 2
# Below this many sheet numbers read, the set's numbering FORMAT is unknown and
# every reference would look dangling.
MIN_KNOWN_SHEETS = 2


@dataclass(frozen=True)
class Page:
    id: str
    document_id: str
    page_number: int
    combined_page_number: int | None
    sheet_number: str | None
    # The title-block text the region scrape read. A sheet whose number the
    # classifier failed to extract still usually has it in here, which is the
    # last guard before calling a reference to it dangling.
    region_text: str | None = None
    # pages.discipline, from the sheet number. The grid check compares grids
    # ACROSS disciplines; None (no sheet number read) compares with anything.
    discipline: str | None = None


@dataclass(frozen=True)
class Chunk:
    id: str
    page_id: str
    text: str
    bbox: dict | None
    # Normalized identifiers from chunk_identifiers — the ONE definition of
    # what an identifier is (cdip_identifiers() in SQL). Not re-derived here.
    identifiers: tuple[str, ...] = ()


@dataclass
class Finding:
    check_type: str
    fingerprint: str
    confidence: str  # high | medium | low
    subject: str
    question: str
    evidence: list[dict]
    # Structured facts the model may use when it words the question. The
    # wording guard in rfi_scan.py rejects a reply that names an identifier
    # found in neither these facts nor the evidence quotes.
    facts: dict = field(default_factory=dict)


# --- Normalization -----------------------------------------------------------


def normalize(token: str) -> str:
    """Upper-case with every non-alphanumeric removed: "S-501" -> "S501".

    The same normalization cdip_identifiers() applies in SQL, so a sheet number
    read off a title block and an identifier read off a note compare equal
    whatever separators the drafter used.
    """
    return re.sub(r"[^A-Z0-9]", "", token.upper())


_SIGNATURE = re.compile(r"^([A-Z]{1,3})(\d+)([A-Z]?)$")


def signature(normalized: str) -> tuple[str, int, bool] | None:
    """(prefix, digit count, trailing letter) — the SHAPE of a sheet number.

    "S1000" (from S-100.0) is ("S", 4, False); "S102A" is ("S", 3, True). A set
    numbers its sheets in one or two shapes, and a reference whose shape
    matches none of them is almost always something else — a column mark C3, a
    grid label, a code section — rather than a missing sheet.
    """
    m = _SIGNATURE.match(normalized)
    if not m:
        return None
    return m.group(1), len(m.group(2)), bool(m.group(3))


def _discipline_prefix(prefix: str) -> bool:
    return prefix in PREFIX_TO_DISCIPLINE


def page_label(page: Page) -> str:
    """How a reader names a page: its sheet number, else its combined page."""
    if page.sheet_number:
        return page.sheet_number
    if page.combined_page_number is not None:
        return f"page {page.combined_page_number}"
    return f"page {page.page_number}"


def fingerprint(check_type: str, *parts: str) -> str:
    """Identity of a FINDING, not of its wording.

    Built from the check and the identifiers involved, so the same gap found by
    two scans is one row — a dismissed candidate stays dismissed, an accepted
    one is not proposed again. The model's wording changes every time it is
    asked and must never be part of this.
    """
    body = "|".join([check_type, *parts])
    return f"{check_type}:{hashlib.sha256(body.encode()).hexdigest()[:24]}"


def _quote(text: str, start: int, end: int, limit: int = 220) -> str:
    """The line holding a match, trimmed around it — what a reviewer reads."""
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    if line_end == -1:
        line_end = len(text)
    line = text[line_start:line_end]
    if len(line) <= limit:
        return " ".join(line.split())
    # Window around the match inside an overlong line.
    rel = start - line_start
    lo = max(0, rel - limit // 2)
    window = line[lo : lo + limit]
    return ("…" if lo > 0 else "") + " ".join(window.split()) + "…"


def _evidence(page: Page, chunk: Chunk, quote: str, role: str = "finding") -> dict:
    return {
        "documentId": page.document_id,
        "pageNumber": page.page_number,
        "combinedPageNumber": page.combined_page_number,
        "sheetNumber": page.sheet_number,
        "bbox": chunk.bbox,
        "chunkId": chunk.id,
        "quote": quote,
        "role": role,
    }


def _where(evidence: list[dict]) -> str:
    """ "S-201 and S-202" — the places a finding was seen, for a template."""
    labels: list[str] = []
    for item in evidence:
        if item.get("role") != "finding":
            continue
        label = item["sheetNumber"] or (
            f"page {item['combinedPageNumber']}"
            if item["combinedPageNumber"] is not None
            else f"page {item['pageNumber']}"
        )
        if label not in labels:
            labels.append(label)
    if not labels:
        return "the drawings"
    if len(labels) == 1:
        return labels[0]
    return ", ".join(labels[:-1]) + " and " + labels[-1]


# --- Check 1: dangling sheet references --------------------------------------

_SHEET_TOKEN = r"([A-Z]{1,2}-?\d{1,4}(?:\.\d{1,2})?[A-Z]?)"

# "5/S-501", "A/S501": a detail callout. The lookbehind requires the callout
# number to stand on its own. Without it a material grade reads as a callout:
# "ASTM A36/A572" is detail 36 on sheet A572 — right shape, real discipline
# prefix — and becomes "sheet A572 referenced but not in the set".
_DETAIL_CALLOUT = re.compile(r"(?<![A-Z0-9/.])(\d{1,2}|[A-Z]\d?)\s*/\s*" + _SHEET_TOKEN + r"(?![A-Z0-9/])")

# "SEE S-501", "REFER TO SHEET A-301", "SEE DETAIL 4 ON S-501".
_KEYWORD_REF = re.compile(
    r"\b(?:SEE|REFER\s+TO|REF\.?|REFERENCE|ON|SHEET|SHT\.?|DWG\.?|DRAWING)\s+"
    r"(?:(?:SHEET|SHT\.?|DWG\.?|DRAWING)\s+)?"
    r"(?:(?:DETAIL|DET\.?|SECTION|SECT\.?)\s+[A-Z0-9]{1,3}\s+(?:ON\s+)?(?:SHEET\s+)?)?"
    + _SHEET_TOKEN
    + r"(?![A-Z0-9])"
)


def sheet_references(text: str) -> list[tuple[str, int, int]]:
    """Every (raw sheet token, start, end) the text POINTS AT.

    Only references with a pointer around them — a callout slash or a word like
    SEE — count. A bare "S-501" in a title block or a sheet index is the sheet
    naming itself or listing the set, not a reference.
    """
    upper = text.upper()
    found: list[tuple[str, int, int]] = []
    seen: set[int] = set()
    for pattern in (_DETAIL_CALLOUT, _KEYWORD_REF):
        for m in pattern.finditer(upper):
            token_start = m.start(2) if pattern is _DETAIL_CALLOUT else m.start(1)
            token = m.group(2) if pattern is _DETAIL_CALLOUT else m.group(1)
            if token_start in seen:
                continue
            seen.add(token_start)
            found.append((token, m.start(), m.end()))
    return found


def dangling_references(pages: list[Page], chunks: list[Chunk]) -> tuple[list[Finding], list[str]]:
    notes: list[str] = []
    live = [p for p in pages if p.sheet_number]
    if len(live) < MIN_KNOWN_SHEETS:
        notes.append(
            "Sheet-reference check skipped: fewer than "
            f"{MIN_KNOWN_SHEETS} sheet numbers have been read for this project, so "
            "there is no set to check references against. Mark the title block "
            "region first."
        )
        return [], notes

    known = {normalize(p.sheet_number) for p in live if p.sheet_number}
    shapes = {signature(k)[1:] for k in known if signature(k)}  # (digits, trailing)
    digit_counts = {shape[0] for shape in shapes}
    prefixes = {signature(k)[0] for k in known if signature(k)}
    title_block_text = " ".join(normalize(p.region_text or "") for p in pages)
    unread = sum(1 for p in pages if not p.sheet_number)

    by_page = {p.id: p for p in pages}
    hits: dict[str, list[dict]] = {}
    raw_form: dict[str, str] = {}
    off_pattern: set[str] = set()

    for chunk in chunks:
        page = by_page.get(chunk.page_id)
        if page is None:
            continue
        for token, start, end in sheet_references(chunk.text):
            ref = normalize(token)
            sig = signature(ref)
            if sig is None or ref in known:
                continue
            prefix, digits, trailing = sig
            # The shape guard: "5/C3" is a column mark in a set numbered
            # S-101, not a missing sheet C3 — the DIGIT COUNT is what tells a
            # sheet from a mark. A trailing letter that differs ("S-501" in a
            # set numbered S-101P) is weaker evidence rather than none: it may
            # be a sheet from another package. It is kept, and marked low.
            if digits not in digit_counts or not _discipline_prefix(prefix):
                continue
            if (digits, trailing) not in shapes:
                off_pattern.add(ref)
            # The sheet may be in the set with its number misread by the
            # classifier; its title block text would still carry it.
            if ref in title_block_text:
                continue
            quote = _quote(chunk.text, start, end)
            items = hits.setdefault(ref, [])
            if len(items) < MAX_EVIDENCE:
                items.append(_evidence(page, chunk, quote))
            raw_form.setdefault(ref, token)

    findings: list[Finding] = []
    for ref, evidence in sorted(hits.items()):
        token = raw_form[ref]
        prefix = signature(ref)[0]
        discipline = PREFIX_TO_DISCIPLINE[prefix].replace("_", " ")
        if ref in off_pattern:
            # Right digits, different suffix from every sheet in the set:
            # plausibly another package's sheet rather than a missing one.
            confidence = "low"
        elif prefix in prefixes:
            # Other sheets of this discipline were uploaded and this one was
            # not: the strongest version of the finding.
            confidence = "high" if unread == 0 else "medium"
        else:
            # No sheet of this discipline is here at all — most likely it was
            # simply not uploaded, which is a note to the uploader, not an RFI.
            confidence = "low"
        where = _where(evidence)
        subject = f"Sheet {token} referenced but not in the drawing set"
        question = (
            f"{where} references sheet {token}, but no sheet {token} is in the "
            f"drawing set issued for this project. Please issue sheet {token} or "
            "confirm the correct sheet reference."
        )
        findings.append(
            Finding(
                check_type="dangling_reference",
                fingerprint=fingerprint("dangling_reference", ref),
                confidence=confidence,
                subject=subject,
                question=question,
                evidence=evidence,
                facts={"missingSheet": token, "discipline": discipline, "referencedFrom": where},
            )
        )

    if unread:
        notes.append(
            f"{unread} page(s) have no sheet number read. A reference to one of "
            "them would look missing, so sheet-reference findings are marked "
            "medium rather than high confidence."
        )
    return _cap("dangling_reference", findings, notes), notes


# --- Check 2: marks missing from their schedule -------------------------------

_MARK = re.compile(r"^([A-Z]{1,3})(\d{1,3})([A-Z]?)$")
_SCHEDULE_WORD = re.compile(r"\bSCHEDULE\b", re.I)
_SCHEDULE_TITLE = re.compile(r"((?:[A-Z][A-Z/&-]*\s+){0,4}SCHEDULE)\b")


def _mark_parts(identifier: str) -> tuple[str, int, bool] | None:
    m = _MARK.match(identifier)
    if not m:
        return None
    return m.group(1), len(m.group(2)), bool(m.group(3))


def _mark_pattern(mark: str) -> re.Pattern:
    """Find a normalized mark in raw text, however it was separated: PC4, PC-4."""
    m = _MARK.match(mark)
    assert m, mark
    return re.compile(
        rf"(?<![A-Z0-9]){m.group(1)}[-. ]?{m.group(2)}{m.group(3)}(?![A-Z0-9])"
    )


def _schedule_title(text: str) -> str | None:
    m = _SCHEDULE_TITLE.search(text.upper())
    return " ".join(m.group(1).split()) if m else None


def unscheduled_marks(pages: list[Page], chunks: list[Chunk]) -> tuple[list[Finding], list[str]]:
    notes: list[str] = []
    by_page = {p.id: p for p in pages}
    sheet_keys = {normalize(p.sheet_number) for p in pages if p.sheet_number}

    def marks_of(chunk: Chunk) -> dict[str, set[str]]:
        families: dict[str, set[str]] = {}
        for ident in chunk.identifiers:
            if ident in sheet_keys:
                continue
            parts = _mark_parts(ident)
            if parts is None:
                continue
            families.setdefault(parts[0], set()).add(ident)
        return families

    # A family's schedule PAGES, not just its schedule chunks. A schedule is
    # routinely split across two chunks and only one carries the word
    # SCHEDULE; counting the whole page as "the schedule" means a mark in the
    # continuation is still found. The cost is a check that is blind on a plan
    # sheet which carries its own schedule — a missed finding, which is the
    # direction an error here is allowed to fall.
    schedule_pages: dict[str, set[str]] = {}
    schedule_chunk: dict[str, Chunk] = {}
    for chunk in chunks:
        if not _SCHEDULE_WORD.search(chunk.text):
            continue
        for family, marks in marks_of(chunk).items():
            if len(marks) >= MIN_SCHEDULE_MARKS:
                schedule_pages.setdefault(family, set()).add(chunk.page_id)
                schedule_chunk.setdefault(family, chunk)

    if not schedule_pages:
        notes.append(
            "Schedule check found no schedule in this project's text (no chunk "
            "carrying the word SCHEDULE and at least two marks of one family), "
            "so no mark could be checked against one."
        )
        return [], notes

    scheduled: dict[str, set[str]] = {}
    on_plans: dict[str, dict[str, list[tuple[Page, Chunk]]]] = {}
    for chunk in chunks:
        page = by_page.get(chunk.page_id)
        if page is None:
            continue
        for family, marks in marks_of(chunk).items():
            if family not in schedule_pages:
                continue
            if chunk.page_id in schedule_pages[family]:
                scheduled.setdefault(family, set()).update(marks)
            else:
                for mark in marks:
                    on_plans.setdefault(family, {}).setdefault(mark, []).append((page, chunk))

    findings: list[Finding] = []
    for family in sorted(on_plans):
        in_schedule = scheduled.get(family, set())
        # Only marks SHAPED like the schedule's own: a schedule of S1, S2 slab
        # marks says nothing about S501, which is a sheet number.
        shapes = {_mark_parts(m)[1:] for m in in_schedule}
        title = _schedule_title(schedule_chunk[family].text) or f"{family} schedule"
        schedule_page = by_page[schedule_chunk[family].page_id]
        listed = sorted(in_schedule, key=lambda m: (len(m), m))

        for mark, places in sorted(on_plans[family].items()):
            if mark in in_schedule or _mark_parts(mark)[1:] not in shapes:
                continue
            pattern = _mark_pattern(mark)
            evidence: list[dict] = []
            for page, chunk in places:
                hit = pattern.search(chunk.text.upper())
                if hit is None:
                    # The identifier index saw it and the raw text does not
                    # show it plainly; no quote means nothing to check.
                    continue
                if len(evidence) < MAX_EVIDENCE:
                    evidence.append(_evidence(page, chunk, _quote(chunk.text, hit.start(), hit.end())))
            if not evidence:
                continue
            distinct_places = {(e["documentId"], e["pageNumber"], e["chunkId"]) for e in evidence}
            evidence.append(
                _evidence(
                    schedule_page,
                    schedule_chunk[family],
                    f"{title}: lists {', '.join(listed[:12])}{'…' if len(listed) > 12 else ''}",
                    role="context",
                )
            )
            # Called out in two places is a mark someone drew on purpose; once
            # could be a stray note that happens to share the shape.
            confidence = "high" if len(distinct_places) >= 2 else "medium"
            where = _where(evidence)
            findings.append(
                Finding(
                    check_type="unscheduled_mark",
                    fingerprint=fingerprint("unscheduled_mark", mark),
                    confidence=confidence,
                    subject=f"{mark} has no entry in the {title.lower()}",
                    question=(
                        f"Mark {mark} is shown on {where}, but the {title} lists "
                        f"{', '.join(listed[:12])} and has no entry for {mark}. Please "
                        f"provide the {title} entry for {mark}, or confirm which mark "
                        "is intended."
                    ),
                    evidence=evidence,
                    facts={
                        "mark": mark,
                        "schedule": title,
                        "scheduledMarks": listed[:12],
                        "shownOn": where,
                    },
                )
            )
    return _cap("unscheduled_mark", findings, notes), notes


# --- Check 3: notes the drafter left open --------------------------------------

# (pattern, confidence, what it means). HIGH is text that says outright the
# information does not exist yet. LOW is a verification instruction: often a
# genuine gap, often boilerplate telling the contractor to check dimensions,
# so it is proposed but not pre-selected.
_OPEN_ITEM_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"(?<![A-Z0-9])T\.?B\.?D\.?(?![A-Z0-9])"), "high", "to be determined"),
    (re.compile(r"\bTO BE DETERMINED\b"), "high", "to be determined"),
    (re.compile(r"\bTO BE CONFIRMED\b"), "high", "to be confirmed"),
    (re.compile(r"\bNOT YET (?:DETERMINED|DESIGNED|SELECTED|CONFIRMED|AVAILABLE)\b"), "high", "not yet determined"),
    (re.compile(r"\bINFORMATION (?:TO FOLLOW|PENDING|NOT AVAILABLE)\b"), "high", "information to follow"),
    (re.compile(r"(?<![A-Z0-9])T\.?B\.?C\.?(?![A-Z0-9])"), "medium", "to be confirmed"),
    (re.compile(r"\?{2,}"), "medium", "unresolved"),
    (re.compile(r"\bPENDING (?:APPROVAL|CONFIRMATION|DESIGN|REVIEW|INFORMATION)\b"), "medium", "pending"),
    (re.compile(r"(?<![A-Z0-9])V\.I\.F\.?(?![A-Z0-9])|\bVIF\b"), "low", "verify in field"),
    (re.compile(r"\b(?:VERIFY IN FIELD|FIELD VERIFY|TO BE VERIFIED)\b"), "low", "verify in field"),
]
_RANK = {"high": 0, "medium": 1, "low": 2}


def open_items(text: str) -> list[tuple[int, int, str, str]]:
    """Every (start, end, confidence, meaning) open-item marker in the text.

    Two patterns matching at the same spot report once, at the stronger
    confidence. No two patterns in today's list overlap, so this guards the
    list as it GROWS: a new pattern that happens to cover an old one must not
    double a finding, nor quietly demote it.
    """
    upper = text.upper()
    hits: dict[int, tuple[int, int, str, str]] = {}
    for pattern, confidence, meaning in _OPEN_ITEM_PATTERNS:
        for m in pattern.finditer(upper):
            prev = hits.get(m.start())
            if prev is None or _RANK[confidence] < _RANK[prev[2]]:
                hits[m.start()] = (m.start(), m.end(), confidence, meaning)
    return sorted(hits.values())


def open_item_notes(pages: list[Page], chunks: list[Chunk]) -> tuple[list[Finding], list[str]]:
    by_page = {p.id: p for p in pages}
    # Keyed on the NOTE, not the page: the same "BEAM SIZE TBD" in the general
    # notes of forty sheets is one open item seen forty times.
    grouped: dict[str, dict] = {}
    for chunk in chunks:
        page = by_page.get(chunk.page_id)
        if page is None:
            continue
        for start, end, confidence, meaning in open_items(chunk.text):
            quote = _quote(chunk.text, start, end)
            key = normalize(quote)
            if not key:
                continue
            entry = grouped.setdefault(
                key, {"quote": quote, "confidence": confidence, "meaning": meaning, "evidence": []}
            )
            if _RANK[confidence] < _RANK[entry["confidence"]]:
                entry["confidence"], entry["meaning"] = confidence, meaning
            already = {(e["documentId"], e["pageNumber"]) for e in entry["evidence"]}
            if (page.document_id, page.page_number) not in already and len(entry["evidence"]) < MAX_EVIDENCE:
                entry["evidence"].append(_evidence(page, chunk, quote))

    findings: list[Finding] = []
    for key, entry in sorted(grouped.items(), key=lambda kv: (_RANK[kv[1]["confidence"]], kv[0])):
        quote, where = entry["quote"], _where(entry["evidence"])
        short = quote if len(quote) <= 60 else quote[:57] + "…"
        if entry["meaning"] == "verify in field":
            ask = (
                "Please confirm whether this can be resolved from the design "
                "information, or provide the value needed before fabrication."
            )
        else:
            ask = "Please provide the missing information."
        findings.append(
            Finding(
                check_type="open_item_note",
                fingerprint=fingerprint("open_item_note", key),
                confidence=entry["confidence"],
                subject=f"Open item on {where}: \"{short}\"",
                question=f"The drawings note \"{quote}\" on {where}. {ask}",
                evidence=entry["evidence"],
                facts={"note": quote, "meaning": entry["meaning"], "shownOn": where},
            )
        )
    notes: list[str] = []
    return _cap("open_item_note", findings, notes), notes


# --- Running them -------------------------------------------------------------


def _cap(check_type: str, findings: list[Finding], notes: list[str]) -> list[Finding]:
    """Strongest first, then cut — and SAY it was cut."""
    findings.sort(key=lambda f: _RANK[f.confidence])
    if len(findings) > MAX_FINDINGS_PER_CHECK:
        notes.append(
            f"{check_type}: {len(findings)} findings, only the first "
            f"{MAX_FINDINGS_PER_CHECK} (strongest first) were kept."
        )
        return findings[:MAX_FINDINGS_PER_CHECK]
    return findings


def run_all(
    pages: list[Page], chunks: list[Chunk], grids: list | None = None
) -> tuple[list[Finding], list[str]]:
    """Every check, in a fixed order. Returns (findings, notes).

    `grids` is what `rfi_scan.load_grids` read out of the PDFs — the one check
    that needs geometry rather than text. None means it could not be read
    (the check is switched off, or storage failed), which is reported rather
    than confused with "no grids found".
    """
    # Imported here: rfi_grid builds on this module's Finding and helpers.
    import rfi_grid

    findings: list[Finding] = []
    notes: list[str] = []
    for check in (dangling_references, unscheduled_marks, open_item_notes):
        found, said = check(pages, chunks)
        findings.extend(found)
        notes.extend(said)
    if grids is None:
        notes.append("Grid check did not run: the drawings' grid bubbles were not read for this scan.")
    else:
        found, said = rfi_grid.grid_mismatches(pages, grids)
        findings.extend(found)
        notes.extend(said)
    return findings, notes
