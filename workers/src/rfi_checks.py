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

  dangling_reference   a sheet the drawings point at ("SEE 5/S-501") that the
                       set's own sheet index does not list (one merely not
                       uploaded is a note, not an RFI).
  unscheduled_mark     a mark on a plan (PC4) whose family has a schedule in
                       the set (PC1, PC2, PC3) with no row for it.
  open_item_note       text the drafter left open for the DESIGNER: TBD, TO BE
                       DETERMINED, ???. Not "verify in field" or "by
                       contractor", which are someone else's work.
  grid_mismatch        one grid line named differently by two drawings
                       (rfi_grid.py — read from the PDF's geometry, the one
                       check that is not built on text).
  tag_value_conflict   one equipment tag printed with two different ratings
                       on two sheets: RTU-3 at 80 MBH on the gas riser and the
                       mezzanine piping plan, 100 MBH on the roof piping plan.

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
CHECK_TYPES = ("dangling_reference", "unscheduled_mark", "open_item_note", "grid_mismatch", "tag_value_conflict")
# Written only by the targeted review (rfi_columns.py): they need two named
# sheets laid over each other. Labelled in the same RFI_CHECK_LABELS.
REVIEW_CHECK_TYPES = ("column_mismatch",)

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


def _evidence(page: Page, chunk: Chunk, quote: str, role: str = "finding",
              term: str | None = None, near: str | None = None) -> dict:
    """One piece of evidence. `term` is the exact words printed on the sheet
    (F6.0, RTU-3, TBD) and `near` a second word that must be close to it
    (the 100 of "RTU-3 100 MBH"): `rfi_pinpoint` finds them on the page and
    shrinks the box from the whole chunk — which on JETRIGHT spanned most of
    the sheet, so every package said "NOT pinpointed" — to the words
    themselves. Both are internal (`_`) and removed before anything is saved."""
    item = {
        "documentId": page.document_id,
        "pageNumber": page.page_number,
        "combinedPageNumber": page.combined_page_number,
        "sheetNumber": page.sheet_number,
        "bbox": chunk.bbox,
        "chunkId": chunk.id,
        "quote": quote,
        "role": role,
    }
    if term:
        item["_term"] = term
    if near:
        item["_near"] = near
    return item


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


# A drawing list / sheet index heading. The index is the ISSUED SET: a sheet it
# lists exists whether or not it was uploaded here.
_INDEX_HEADING = re.compile(
    r"\b(?:SHEET|DRAWING)S?\s+(?:INDEX|LIST)\b|\bINDEX\s+OF\s+(?:DRAWINGS|SHEETS)\b|\bLIST\s+OF\s+(?:DRAWINGS|SHEETS)\b"
)
_BARE_SHEET = re.compile(r"(?<![A-Z0-9/.-])" + _SHEET_TOKEN + r"(?![A-Z0-9/])")
# Sheet numbers a page must list before it counts as the index rather than a
# note that mentions one.
MIN_INDEX_ENTRIES = 5


def sheet_index(pages: list[Page], chunks: list[Chunk]) -> set[str] | None:
    """Every sheet the project's SHEET INDEX lists, normalized — or None when
    no index page was uploaded.

    The index page is the one whose text carries an index heading; every
    sheet-shaped token on that page with a discipline prefix counts. A cover
    sheet often splits its index across several chunks, so the PAGE is read,
    not only the chunk with the heading."""
    by_page: dict[str, list[Chunk]] = {}
    for chunk in chunks:
        by_page.setdefault(chunk.page_id, []).append(chunk)
    listed: set[str] = set()
    found = False
    for page_id, page_chunks in by_page.items():
        if not any(_INDEX_HEADING.search(c.text.upper()) for c in page_chunks):
            continue
        tokens = set()
        for c in page_chunks:
            text = c.text.upper()
            # A sheet number with a pointer before it ("SEE A-301") is a
            # reference made on the cover sheet, not an entry in its list.
            # By POSITION: the same sheet may also be listed in the index.
            pointed = {end - len(token) for token, _, end in sheet_references(text)}
            for m in _BARE_SHEET.finditer(text):
                ref = normalize(m.group(1))
                sig = signature(ref)
                if m.start(1) in pointed:
                    continue
                if sig and _discipline_prefix(sig[0]):
                    tokens.add(ref)
        if len(tokens) >= MIN_INDEX_ENTRIES:
            found = True
            listed |= tokens
    return listed if found else None


def dangling_references(pages: list[Page], chunks: list[Chunk]) -> tuple[list[Finding], list[str]]:
    """A sheet the drawings point at that is not in the ISSUED set.

    "Not uploaded here" is not "not issued", and only the second is an RFI:
    the first is answered by uploading the rest of the set. The project's own
    SHEET INDEX is what tells them apart, so a reference becomes a finding only
    when an index was uploaded and does NOT list the sheet. A sheet the index
    lists, and every reference in a project with no index, goes to the notes
    as "not uploaded" — the client's test returned references to ramp
    sections (A5.14) and an enlarged plan (A3.31) that were part of the set
    and simply not in the upload."""
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
    digits_by_prefix: dict[str, set[int]] = {}
    for k in known:
        sig = signature(k)
        if sig:
            digits_by_prefix.setdefault(sig[0], set()).add(sig[1])
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
            # When the set HAS sheets of this prefix, the reference must be
            # numbered like them: JETRIGHT numbers its electrical sheets E2.01,
            # and "MECHANICAL DRAWING E2" matched only because the cover sheet
            # is T1.
            if prefix in digits_by_prefix and digits not in digits_by_prefix[prefix]:
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
                items.append(_evidence(page, chunk, quote, term=token))
            raw_form.setdefault(ref, token)

    index = sheet_index(pages, chunks)
    not_uploaded = sorted(ref for ref in hits if index is None or ref in index)
    if not_uploaded:
        shown = ", ".join(raw_form[r] for r in not_uploaded[:12]) + ("…" if len(not_uploaded) > 12 else "")
        if index is None:
            notes.append(
                f"Sheet-reference check: the drawings refer to {len(not_uploaded)} sheet(s) that are not in "
                f"this upload ({shown}). No sheet index (drawing list) was uploaded, so a sheet that was never "
                "issued cannot be told from one that was simply not uploaded, and none is proposed as an RFI. "
                "Upload the cover sheet with the drawing list, or the rest of the set, to check them."
            )
        else:
            notes.append(
                f"Sheet-reference check: {len(not_uploaded)} referenced sheet(s) are listed in the sheet index "
                f"but were not uploaded ({shown}). They exist in the issued set, so they are not RFIs — upload "
                "them to review what they show."
            )
    for ref in not_uploaded:
        hits.pop(ref)

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
            f"{where} references sheet {token}, but sheet {token} is not listed in the "
            f"sheet index of the drawing set issued for this project. Please issue sheet "
            f"{token} or confirm the correct sheet reference."
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
# One LINE of words before SCHEDULE: `\s` crossed line breaks, so a finish
# schedule whose previous line read "SMOOTH FINISH" became the "SMOOTH FINISH
# CONCRETE FINISH SCHEDULE" — a heading no drawing carries, quoted in an RFI.
_SCHEDULE_TITLE = re.compile(r"((?:[A-Z][A-Z/&-]*[ \t]+){0,4}SCHEDULE)\b")


def _mark_parts(identifier: str) -> tuple[str, int, bool] | None:
    m = _MARK.match(identifier)
    if not m:
        return None
    return m.group(1), len(m.group(2)), bool(m.group(3))


def _mark_pattern(mark: str, dotted: bool = False) -> re.Pattern:
    """Find a normalized mark in raw text, however it was separated: PC4, PC-4.

    Never with a dot straight after the LETTERS. `cdip_identifiers` strips
    every separator, so the grid line F.7 — bubbled on every plan of a set — is
    indexed as F7, exactly the shape of a mark. A full scan of 423 client pages
    proposed "Mark F7 missing from the concrete finish schedule" with four
    sheets of evidence, every one of them the grid bubble.

    A dot BETWEEN DIGITS is allowed only when `dotted`: the family's own
    schedule writes its marks that way. JETRIGHT's footing schedule lists F5.0,
    F7.0, F11.0 and the plan tags F6.0 — a mark this pattern could never quote
    while it refused every dot, so the footing with no schedule entry (the
    first RFI a reviewer raised on that set) was invisible. A secondary grid
    line like B1.6 has the same shape, which is why the schedule has to vouch
    for it."""
    m = _MARK.match(mark)
    assert m, mark
    digits = m.group(2)
    if dotted:
        # Written the schedule's way: one dot between digits, never none — F10
        # on an architectural sheet is not F1.0 in a schedule of F5.0, F7.0.
        cuts = [digits[:i] + r"\." + digits[i:] for i in range(1, len(digits))]
        if not cuts:
            return re.compile(r"(?!x)x")  # a one-digit mark cannot carry a dot
        digits = "(?:" + "|".join(cuts) + ")"
    return re.compile(
        rf"(?<![A-Z0-9.]){m.group(1)}[- ]?{digits}{m.group(3)}(?![A-Z0-9]|\.\d)"
    )


def _writes_dotted(family: str, text: str) -> bool:
    """Whether `text` writes marks of `family` with a dot between digits
    (F5.0, F11.0B) — the schedule's own notation."""
    return re.search(rf"(?<![A-Z0-9.]){family}[- ]?\d+\.\d", text.upper()) is not None


# How far a schedule's rows may sit from its heading: the heading and the
# table are often separate text blocks (JETRIGHT's S1.02 prints "FOOTING
# SCHEDULE" 79pt above the block of F5.0 ... F7.0 rows), so a chunk holding
# only the heading would never count as a schedule. A table hangs below its
# heading; its columns may spread either side of a centred title.
SCHEDULE_BODY_BELOW_PT = 700.0
SCHEDULE_BODY_SIDE_PT = 400.0


def _under_heading(heading: dict | None, body: dict | None) -> bool:
    if not heading or not body:
        return False
    hx0, hy0 = heading["x"], heading["y"]
    hx1, hy1 = hx0 + heading["width"], hy0 + heading["height"]
    bx0, by0 = body["x"], body["y"]
    bx1 = bx0 + body["width"]
    return (
        hy0 - 2 <= by0 <= hy1 + SCHEDULE_BODY_BELOW_PT
        and bx1 >= hx0 - SCHEDULE_BODY_SIDE_PT
        and bx0 <= hx1 + SCHEDULE_BODY_SIDE_PT
    )


# Words that make "... SCHEDULE" a pointer to a schedule rather than its
# heading: "FOR STUD RAIL SCHEDULE AND DETAILS SEE SHEET S5.131" is a note on a
# forming plan, and the plan is not the stud rail schedule.
_REFERENCE_WORDS = {"SEE", "REFER", "REF", "FOR", "PER", "TO", "IN", "ON", "FROM", "WITH"}
# Words a title carries that name no element.
_TITLE_FILLER = {"AND", "OF", "THE", "&", "/", "TYPICAL", "TYP", "TYP.", "SCHEDULE", "SCHEDULES"}
_SEE_AFTER = re.compile(r"^\W{0,3}(?:[A-Z]+\s+){0,3}(?:SEE|REFER|REF\.?)\b")
# A mark followed by one of these is a product designation, not a drawn
# element: "TYPE S-8 PAN HEAD STEEL SCREWS" is a screw, and was proposed as a
# door missing from the door schedule.
_FASTENER_AFTER = re.compile(
    r"^\W{0,3}(?:[A-Z#/.-]+\s+){0,4}(?:SCREWS?|BOLTS?|NAILS?|ANCHORS?|WASHERS?|RIVETS?|FASTENERS?|STAPLES?|PINS?)\b"
    # ...or an insulation rating: "R-13 MIN.", "R-30 BATT INSULATION" sat
    # beside a railing schedule of R1..R6 and read as railings missing from it.
    r"|^\W{0,3}(?:MIN\b|MINIMUM|(?:[A-Z]+\s+){0,2}INSUL|BATT|C\.?I\.?\b|RIGID|CONTINUOUS\s+INSUL|THERMAL)"
)


def schedule_titles(text: str) -> list[str]:
    """Every schedule HEADING in `text` — "PILE CAP SCHEDULE", never the
    "FOR STUD RAIL SCHEDULE … SEE SHEET S5.131" that points somewhere else.
    A title whose own words include a pointer word, or that is followed by
    SEE / REFER, is a reference and is left out."""
    upper = text.upper()
    titles = []
    for m in _SCHEDULE_TITLE.finditer(upper):
        words = m.group(1).split()
        if any(w.strip(".,:") in _REFERENCE_WORDS for w in words[:-1]):
            continue
        if _SEE_AFTER.match(upper[m.end() : m.end() + 60]):
            continue
        titles.append(" ".join(words))
    return titles


def title_names_family(title: str, family: str) -> bool:
    """Whether a schedule's title is about marks of `family`.

    The letters of a mark family are the initials of what it marks: PC is a
    PILE CAP, SR a STUD RAIL, SW a SHEAR WALL, C a COLUMN, D a DOOR. So the
    family must be the initials of consecutive title words, or spelled in
    order inside one word ("DECON STUDRAIL SCHEDULE" for SR). Without it a
    forming plan's LEVEL SCHEDULE became the schedule SR-25 was "missing"
    from, and a DOOR SCHEDULE the one a screw type was "missing" from. A
    family a title does not name — a lighting schedule of A1, B2 fixtures —
    is a missed finding, the direction an error here is allowed to fall.
    """
    words = [w.strip(".,:()") for w in title.upper().split()]
    words = [w for w in words if w and w not in _TITLE_FILLER]
    initials = "".join(w[0] for w in words)
    if family in initials:
        return True
    for word in words:
        if word[0] != family[0]:
            continue
        rest = iter(word[1:])
        if all(ch in rest for ch in family[1:]):
            return True
    return False


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
    schedule_name: dict[str, str] = {}
    dotted: dict[str, bool] = {}
    schedule_text: dict[str, list[str]] = {}
    on_page: dict[str, list[Chunk]] = {}
    for chunk in chunks:
        on_page.setdefault(chunk.page_id, []).append(chunk)
    for chunk in chunks:
        if not _SCHEDULE_WORD.search(chunk.text):
            continue
        titles = schedule_titles(chunk.text)
        if not titles:
            continue
        # The heading's own chunk, plus the rows printed under it when the
        # table is a separate text block.
        body = [chunk] + [
            c for c in on_page.get(chunk.page_id, []) if c is not chunk and _under_heading(chunk.bbox, c.bbox)
        ]
        families: dict[str, set[str]] = {}
        for c in body:
            for family, marks in marks_of(c).items():
                families.setdefault(family, set()).update(marks)
        for family, marks in families.items():
            if len(marks) < MIN_SCHEDULE_MARKS:
                continue
            # The schedule must be a heading, and a heading about THIS family.
            named = [t for t in titles if title_names_family(t, family)]
            if not named:
                continue
            schedule_pages.setdefault(family, set()).add(chunk.page_id)
            schedule_chunk.setdefault(family, chunk)
            schedule_name.setdefault(family, named[0])
            dotted[family] = dotted.get(family, False) or any(_writes_dotted(family, c.text) for c in body)
            schedule_text.setdefault(family, []).extend(c.text.upper() for c in body)

    if not schedule_pages:
        notes.append(
            "Schedule check found no schedule in this project's text (no chunk "
            "carrying a schedule heading that names a mark family — PILE CAP "
            "SCHEDULE for PC marks — and at least two of its marks), so no mark "
            "could be checked against one."
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

    # A sheet that shows several marks of a family the schedule does not list,
    # and fewer that it does, is using those letters for something else: the
    # roof plan's R7..R18 beside a railing schedule of R1..R6, R-19 and R-30
    # insulation values, a code sheet's generic TA02..TA10 accessory diagrams.
    # A real gap is the exception on a sheet that otherwise speaks the
    # schedule's vocabulary — S2.01 tags two F6.0 among some thirty F5.0,
    # F7.0, F8.0 and F11.0 footings.
    foreign: set[tuple[str, str]] = set()
    for family, marks in on_plans.items():
        in_schedule = scheduled.get(family, set())
        per_page: dict[str, tuple[set[str], set[str]]] = {}
        for mark, places in marks.items():
            for page, _ in places:
                listed, unlisted = per_page.setdefault(page.id, (set(), set()))
                (listed if mark in in_schedule else unlisted).add(mark)
        for page_id, (listed, unlisted) in per_page.items():
            if len(unlisted) >= 2 and len(unlisted) > len(listed):
                foreign.add((family, page_id))
    schedule_discipline = {
        family: by_page[schedule_chunk[family].page_id].discipline for family in schedule_chunk
    }

    findings: list[Finding] = []
    for family in sorted(on_plans):
        in_schedule = scheduled.get(family, set())
        # Only marks SHAPED like the schedule's own: a schedule of S1, S2 slab
        # marks says nothing about S501, which is a sheet number.
        shapes = {_mark_parts(m)[1:] for m in in_schedule}
        title = schedule_name.get(family) or f"{family} schedule"
        schedule_page = by_page[schedule_chunk[family].page_id]
        def shown(mark: str, texts: list[str]) -> str:
            """The mark as the drawings print it (F6.0), not as the index
            stores it (F60) — a reader searches the sheet for what we quote."""
            pattern = _mark_pattern(mark, dotted.get(family, False))
            for text in texts:
                hit = pattern.search(text)
                if hit:
                    return hit.group(0)
            return mark

        listed = [shown(m, schedule_text.get(family, [])) for m in sorted(in_schedule, key=lambda m: (len(m), m))]

        for mark, places in sorted(on_plans[family].items()):
            if mark in in_schedule or _mark_parts(mark)[1:] not in shapes:
                continue
            pattern = _mark_pattern(mark, dotted.get(family, False))
            evidence: list[dict] = []
            for page, chunk in places:
                if (family, page.id) in foreign:
                    continue
                # A schedule speaks for its own discipline's sheets: a footing
                # schedule says nothing about F10 on an architectural sheet.
                own = schedule_discipline.get(family)
                if own and page.discipline and page.discipline != own:
                    continue
                upper = chunk.text.upper()
                hit = next(
                    (h for h in pattern.finditer(upper) if not _FASTENER_AFTER.match(upper[h.end() : h.end() + 60])),
                    None,
                )
                if hit is None:
                    # The identifier index saw it and the raw text does not
                    # show it plainly — or shows it only as a product type
                    # ("TYPE S-8 … SCREWS"); no quote means nothing to check.
                    continue
                if len(evidence) < MAX_EVIDENCE:
                    evidence.append(_evidence(page, chunk, _quote(chunk.text, hit.start(), hit.end()),
                                              term=chunk.text[hit.start():hit.end()]))
            if not evidence:
                continue
            printed = shown(mark, [c.text.upper() for _, c in places])
            distinct_places = {(e["documentId"], e["pageNumber"], e["chunkId"]) for e in evidence}
            evidence.append(
                _evidence(
                    schedule_page,
                    schedule_chunk[family],
                    f"{title}: lists {', '.join(listed[:12])}{'…' if len(listed) > 12 else ''}",
                    role="context",
                    term=title,
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
                    subject=f"{printed} has no entry in the {title.lower()}",
                    question=(
                        f"Mark {printed} is shown on {where}, but the {title} lists "
                        f"{', '.join(listed[:12])} and has no entry for {printed}. Please "
                        f"provide the {title} entry for {printed}, or confirm which mark "
                        "is intended."
                    ),
                    evidence=evidence,
                    facts={
                        "mark": printed,
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
    (re.compile(r"(?<![A-Z0-9.])T\.?B\.?D\.?(?![A-Z0-9]|\.[A-Z0-9])"), "high", "to be determined"),
    (re.compile(r"\bTO BE DETERMINED\b"), "high", "to be determined"),
    (re.compile(r"\bTO BE CONFIRMED\b"), "high", "to be confirmed"),
    (re.compile(r"\bNOT YET (?:DETERMINED|DESIGNED|SELECTED|CONFIRMED|AVAILABLE)\b"), "high", "not yet determined"),
    (re.compile(r"\bINFORMATION (?:TO FOLLOW|PENDING|NOT AVAILABLE)\b"), "high", "information to follow"),
    # Never part of a longer dotted abbreviation: C-301's "TRAFFIC BEARING
    # (T.B.C.O.)" is a cover rating, and was proposed as an open item.
    (re.compile(r"(?<![A-Z0-9.])T\.?B\.?C\.?(?![A-Z0-9]|\.[A-Z0-9])"), "medium", "to be confirmed"),
    (re.compile(r"\?{2,}"), "medium", "unresolved"),
    (re.compile(r"\bPENDING (?:APPROVAL|CONFIRMATION|DESIGN|REVIEW|INFORMATION)\b"), "medium", "pending"),
]
# "VERIFY IN FIELD" is not on this list on purpose. It is an instruction to the
# contractor — take the measurement on site — and the contract makes that the
# contractor's job; an RFI is for what the documents cannot answer.
# A note handing the open item to another party is that party's work, settled
# through a submittal or shop drawing, not a question to the designer:
# "CONNECTION TO BE DETERMINED BY FABRICATOR", "DIMENSIONS TO BE CONFIRMED BY
# CONTRACTOR".
_OTHER_PARTY = re.compile(
    r"\b(?:CONTRACTOR|SUB-?CONTRACTOR|G\.?C\.?|FABRICATOR|SUPPLIER|MANUFACTURER|VENDOR|INSTALLER|"
    r"SUBMITTALS?|SHOP\s+DRAWINGS?|DELEGATED|BY\s+OTHERS|IN\s+FIELD|ON\s+SITE)\b"
)
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
    handed_off = 0
    for chunk in chunks:
        page = by_page.get(chunk.page_id)
        if page is None:
            continue
        for start, end, confidence, meaning in open_items(chunk.text):
            if _OTHER_PARTY.search(_sentence(chunk.text, start, end).upper()):
                handed_off += 1
                continue
            quote = _quote(chunk.text, start, end)
            key = normalize(quote)
            if not key:
                continue
            # A line that is ONLY the marker is a table cell ("TBD" under
            # ACHIEVED BY on G2.01, eight rows of it). Alone it says nothing,
            # and every bare "TBD" in the project would group as one finding,
            # so it is named — and grouped — by the table it sits in.
            table = _table_title(chunk.text) if key == normalize(chunk.text[start:end]) else None
            if table:
                key = f"{key}|{normalize(table)}"
            entry = grouped.setdefault(
                key, {"quote": quote, "table": table, "cells": 0, "confidence": confidence,
                      "meaning": meaning, "evidence": []}
            )
            if table:
                entry["cells"] += 1
            if _RANK[confidence] < _RANK[entry["confidence"]]:
                entry["confidence"], entry["meaning"] = confidence, meaning
            already = {(e["documentId"], e["pageNumber"]) for e in entry["evidence"]}
            if (page.document_id, page.page_number) not in already and len(entry["evidence"]) < MAX_EVIDENCE:
                entry["evidence"].append(_evidence(page, chunk, quote, term=chunk.text[start:end]))

    findings: list[Finding] = []
    for key, entry in sorted(grouped.items(), key=lambda kv: (_RANK[kv[1]["confidence"]], kv[0])):
        quote, where = entry["quote"], _where(entry["evidence"])
        short = quote if len(quote) <= 60 else quote[:57] + "…"
        ask = "Please provide the missing information, or confirm when it will be issued."
        facts = {"note": quote, "meaning": entry["meaning"], "shownOn": where}
        if entry["table"]:
            table, cells = entry["table"], entry["cells"]
            many = f" ({cells} entries)" if cells > 1 else ""
            subject = f"Open item on {where}: \"{quote}\" in {table[:60]}{many}"
            question = (
                f"The table \"{table}\" on {where} has {cells if cells > 1 else 'an'} "
                f"entr{'ies' if cells > 1 else 'y'} reading only \"{quote}\". {ask}"
            )
            facts.update({"table": table, "entries": cells})
        else:
            subject = f"Open item on {where}: \"{short}\""
            question = f"The drawings note \"{quote}\" on {where}. {ask}"
        findings.append(
            Finding(
                check_type="open_item_note",
                fingerprint=fingerprint("open_item_note", key),
                confidence=entry["confidence"],
                subject=subject,
                question=question,
                evidence=entry["evidence"],
                facts=facts,
            )
        )
    notes: list[str] = []
    if handed_off:
        notes.append(
            f"Open-item check: {handed_off} open-item note(s) assign the item to the contractor, a supplier "
            "or a submittal (\"… BY CONTRACTOR\", \"VERIFY IN FIELD\"); that is their work, not a question for "
            "the designer, so none was proposed as an RFI."
        )
    return _cap("open_item_note", findings, notes), notes


TABLE_TITLE_LINES = 6  # a table's title is printed above its rows


def _table_title(text: str) -> str | None:
    """The heading a chunk opens with: the first of its first few lines that
    reads as words (eight letters or more), with CAD letter-spacing
    ("F I R E   R E S I S T A N C E") closed up. None when there is none —
    the finding then keeps the bare marker rather than inventing a name."""
    for line in text.split("\n")[:TABLE_TITLE_LINES]:
        line = line.strip()
        glyphs = line.split()
        if len(glyphs) >= 6 and sum(len(g) == 1 for g in glyphs) >= 0.8 * len(glyphs):
            # One glyph per "word": the real words are split by the wider gaps.
            line = " ".join(w.replace(" ", "") for w in re.split(r" {2,}", line))
        line = " ".join(line.split())
        if sum(c.isalpha() for c in line) >= 8 and not any(p.search(line.upper()) for p, *_ in _OPEN_ITEM_PATTERNS):
            return line
    return None


def _sentence(text: str, start: int, end: int) -> str:
    """The sentence (or note line) around a match: back to the previous full
    stop or line break, forward to the next."""
    left = max(text.rfind(".", 0, start), text.rfind("\n", 0, start))
    stops = [i for i in (text.find(".", end), text.find("\n", end)) if i != -1]
    return text[left + 1 : min(stops) if stops else len(text)]


# --- Running them -------------------------------------------------------------


# --- Check 4: one tag, two ratings ---------------------------------------------

# An equipment tag: letters, a hyphen, a number (RTU-3, CF-2, EF-12, IRH-1).
# The hyphen is required: without it a tag is indistinguishable from a sheet
# number, a grid crossing or a mark, and a mark's value is a schedule question.
_TAG = re.compile(r"(?<![A-Z0-9.\-/])([A-Z]{1,4}-\d{1,3}[A-Z]?)(?![A-Z0-9]|-\d|\.\d|,\d)")
# A rating printed next to it, in a unit that rates EQUIPMENT. Dimensions,
# pipe sizes and elevations are not on the list: a 3/4" pipe to RTU-3 and a
# 1" pipe to RTU-3 are two pipes, not a conflict.
_RATING = re.compile(
    r"(?<![\d./-])(\d{1,2}[- ]\d/\d|\d/\d|\d{1,5}(?:\.\d{1,2})?)\s*"
    r"(MBH|BTUH|HP|CFM|GPM|KVA|KW|TONS?)(?![A-Z])"
)
_UNIT_NOUN = {
    "MBH": "input", "BTUH": "capacity", "HP": "motor horsepower", "CFM": "airflow",
    "GPM": "flow", "KW": "power", "KVA": "load", "TON": "capacity", "TONS": "capacity",
}
# How far after the tag its rating may be printed: one label is a tag and one
# or two lines under it ("UP TO\nRTU-3\n80MBH"). A schedule row runs further,
# and is cut at the next tag anyway.
RATING_WINDOW_CHARS = 40


def _rating_value(text: str) -> float:
    text = text.replace(" ", "-")
    if "-" in text and "/" in text:
        whole, frac = text.split("-", 1)
        num, den = frac.split("/")
        return float(whole) + float(num) / float(den)
    if "/" in text:
        num, den = text.split("/")
        return float(num) / float(den)
    return float(text)


def tag_ratings(text: str) -> list[tuple[str, str, float, str, int, int]]:
    """(tag, unit, value, value as printed, start, end) for every tag with ONE
    rating of a unit printed straight after it. Two ratings of one unit after
    the same tag (a schedule row with INPUT and OUTPUT MBH) say nothing about
    which one belongs to it, so neither is taken."""
    upper = text.upper()
    tags = list(_TAG.finditer(upper))
    out = []
    for i, m in enumerate(tags):
        stop = min(m.end() + RATING_WINDOW_CHARS, tags[i + 1].start() if i + 1 < len(tags) else len(upper))
        window = upper[m.end() : stop]
        by_unit: dict[str, list[re.Match]] = {}
        for r in _RATING.finditer(window):
            unit = "TONS" if r.group(2) == "TON" else r.group(2)
            by_unit.setdefault(unit, []).append(r)
        for unit, found in by_unit.items():
            if len(found) != 1:
                continue
            r = found[0]
            try:
                value = _rating_value(r.group(1))
            except (ValueError, ZeroDivisionError):
                continue
            out.append((m.group(1), unit, value, f"{r.group(1)} {unit}", m.start(), m.end() + r.end()))
    return out


def tag_value_conflicts(pages: list[Page], chunks: list[Chunk]) -> tuple[list[Finding], list[str]]:
    """One tag, two ratings of one unit, on two different SHEETS.

    Entity matching first, comparison second: the tag is what makes the two
    numbers about the same piece of equipment — RTU-3 on the gas riser, RTU-3
    on the roof piping plan. Within one sheet a second value is more likely a
    second quantity (input and output) than a disagreement, so a conflict must
    cross sheets. Ratings in a unit that rates equipment only; a pipe size or
    a dimension beside a tag describes something else.

    Blind spot, stated: a schedule pasted into the sheet as a PICTURE has no
    text to read. JETRIGHT's M0.02 equipment schedules and E0.05 panel
    schedules are images, so CF-2's 2 HP against 2-1/2 HP (a reviewer's RFI on
    that set) is not visible to this check."""
    notes: list[str] = []
    by_page = {p.id: p for p in pages}
    seen: dict[tuple[str, str], dict[float, list[tuple[Page, Chunk, str, int, int]]]] = {}
    for chunk in chunks:
        page = by_page.get(chunk.page_id)
        if page is None:
            continue
        for tag, unit, value, printed, start, end in tag_ratings(chunk.text):
            seen.setdefault((tag, unit), {}).setdefault(round(value, 3), []).append((page, chunk, printed, start, end))

    findings: list[Finding] = []
    for (tag, unit), values in sorted(seen.items()):
        if len(values) < 2:
            continue
        sheets_of = {v: {page.id for page, *_ in places} for v, places in values.items()}
        all_sheets = set().union(*sheets_of.values())
        if len(all_sheets) < 2:
            continue  # one sheet: input and output, or two units of one kind
        # Most-repeated value first, so the odd one out reads last.
        ordered = sorted(values, key=lambda v: (-len(sheets_of[v]), v))
        evidence: list[dict] = []
        said: list[str] = []
        for v in ordered:
            places = values[v]
            sheets = sorted({page_label(page) for page, *_ in places})
            said.append(f"{places[0][2]} on {', '.join(sheets)}")
            for page, chunk, printed, start, end in places:
                if sum(1 for e in evidence if e.get("_value") == v) >= 2 or len(evidence) >= MAX_EVIDENCE:
                    continue
                item = _evidence(page, chunk, _quote(chunk.text, start, end),
                                 term=chunk.text[start:start + len(tag)], near=printed.split()[0])
                item["_value"] = v
                evidence.append(item)
        for item in evidence:
            item.pop("_value", None)
        # A value repeated on two sheets against one odd sheet is the shape of a
        # real disagreement (two drawings agree, one does not).
        confidence = "high" if max(len(s) for s in sheets_of.values()) >= 2 else "medium"
        noun = _UNIT_NOUN.get(unit, "rating")
        subject = f"{tag} {noun} differs between drawings"
        question = (
            f"{tag} is labelled {'; '.join(said)}. Please confirm the required {noun} for {tag} "
            "and coordinate the drawings."
        )
        findings.append(
            Finding(
                check_type="tag_value_conflict",
                fingerprint=fingerprint("tag_value_conflict", tag, unit),
                confidence=confidence,
                subject=subject,
                question=question,
                evidence=evidence,
                facts={"tag": tag, "unit": unit, "values": said},
            )
        )
    return _cap("tag_value_conflict", findings, notes), notes


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
    for check in (dangling_references, unscheduled_marks, open_item_notes, tag_value_conflicts):
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
