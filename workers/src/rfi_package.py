"""The rfi-package job: an RFI as the team sends it — a cover form and the
drawing sheets MARKED UP, not a text report.

Modelled on the client's own issued RFIs (RFI 001-019, UT Law Student
Housing). Every one of those is:

  1. a cover form: project, RFI number, date issued, author, discipline,
     description, plan/sheet, revision, the question in a shaded box, and
     cropped pictures of the problem areas captioned with their sheet;
  2. the drawing sheets themselves, full size, with a red revision CLOUD round
     each problem area, a callout carrying the question, and a leader line
     from the callout to the cloud.

The sheets are copied from the ORIGINAL PDF as vector pages (`insert_pdf`),
never screenshotted, so they print and zoom like the drawing they are. The
marks are real PDF annotations — Polygon with a cloudy border, FreeText, Line
— exactly the kinds the team's own markups are made of, so the output opens in
Bluebeam or Acrobat and every mark can be moved, edited or deleted. Existing
annotations on the source page are NOT copied: someone's earlier markup is not
the drawing as issued (the same rule as `grid.without_markup`).

A draft (a candidate nobody accepted yet) is marked DRAFT on every page it
touches and carries no RFI number: it is a proposal, not an issued RFI.

Where the marks go comes from the evidence the RFI or candidate already
stores — `rfi_locations` or the candidate's evidence list, each a
(document, page, bbox) in the page's UNROTATED space, which is also the space
PyMuPDF places annotations in. Only the callout's position is chosen here, in
DISPLAY space (what a person sees), and mapped back.
"""

from __future__ import annotations

import json
import re
import os
import tempfile
import textwrap
from dataclasses import dataclass, field
from datetime import datetime

import fitz

import db
import logutil
import storage
from generated import rfi_package_key

log = logutil.get("rfi_package")

RED = (0.9, 0.0, 0.0)
# Context (a supporting note, a typical detail, an occurrence nobody verified)
# is outlined in this, dashed, never clouded: a cloud says "the problem is
# HERE", and a referenced note is not where the problem is.
CONTEXT = (0.1, 0.35, 0.8)
CALLOUT_FILL = (1.0, 1.0, 0.6)  # the yellow note the team uses (RFI 010)
QUESTION_FILL = (0.82, 0.87, 0.94)  # the cover form's shaded question box
MAX_SHEETS_PER_ITEM = 6
MAX_ITEMS = 50
MAX_SNIPPETS = 2
# Evidence covering more than this share of its page is "the whole sheet":
# clouding it would say nothing about where the problem is.
WHOLE_SHEET_SHARE = 0.5
# A finding box larger than this share is a broad AREA, not a location: it is
# outlined and labelled "not pinpointed" instead of clouded, and clouds are
# never merged into anything larger. A forming plan's grouped column claim
# came out as one cloud over nearly the whole plan.
PRECISE_SHARE = 0.08
STATUSES = ("queued", "running", "ready", "failed")

LETTER = fitz.Rect(0, 0, 612, 792)


@dataclass
class Mark:
    document_id: str | None
    page_number: int
    combined_page_number: int | None
    sheet_number: str | None
    bbox: dict | None  # unrotated PDF points, like chunk bboxes
    note: str | None = None
    # "finding": where the problem is (clouded). "context": what it was
    # checked against, or an occurrence nobody verified (outlined, labelled).
    role: str = "finding"
    label: str | None = None
    kind: str | None = None  # the evidence kind (text, crop, page, occurrence, ...)


@dataclass
class Item:
    kind: str  # "rfi" | "candidate"
    item_id: str
    number: int | None  # the issued RFI number; None for a draft
    subject: str
    question: str
    discipline: str | None
    issued: datetime | None
    author: str | None
    company: str | None
    project: str
    draft: bool
    marks: list[Mark] = field(default_factory=list)
    reasoning: str | None = None
    # What the finding's sheets were issued as, read off their title blocks at
    # render time (`sheet_issues`). None when nothing could be read, and the
    # form then says so in words — a blank field reads as "no revision",
    # which is a claim.
    revision: str | None = None

    @property
    def title(self) -> str:
        return f"RFI: {self.number:03d}" if self.number is not None and not self.draft else "DRAFT RFI"

    @property
    def callout_heading(self) -> str:
        return f"RFI {self.number:03d}" if self.number is not None and not self.draft else "DRAFT RFI — not issued"

    @property
    def sheets(self) -> list[str]:
        out: list[str] = []
        for m in self.marks:
            name = m.sheet_number or (f"page {m.combined_page_number or m.page_number}")
            if name not in out:
                out.append(name)
        return out


# --- geometry (pure; tested) --------------------------------------------------------


def cloud_rect(bbox: dict | None, unrotated_page: fitz.Rect, pad: float) -> fitz.Rect | None:
    """The cloud round one piece of evidence, in UNROTATED space: its box
    grown by `pad` and clamped to the page. None when there is no box, or it
    is most of the sheet — a cloud round everything points at nothing."""
    if not bbox:
        return None
    try:
        x, y, w, h = (float(bbox[k]) for k in ("x", "y", "width", "height"))
    except (KeyError, TypeError, ValueError):
        return None
    if w <= 0 or h <= 0:
        return None
    if w * h > WHOLE_SHEET_SHARE * unrotated_page.width * unrotated_page.height:
        return None
    rect = fitz.Rect(x - pad, y - pad, x + w + pad, y + h + pad) & unrotated_page
    return None if rect.is_empty else rect


def merge_rects(rects: list[fitz.Rect], gap: float, max_area: float | None = None) -> list[fitz.Rect]:
    """Clouds that touch or nearly touch become one: two clouds on one note
    read as two problems. Never into a union larger than `max_area`: chained
    merges of nearby occurrences are how one cloud ended up over a whole plan,
    and a cloud that large points at nothing."""
    out = [fitz.Rect(r) for r in rects]
    changed = True
    while changed:
        changed = False
        for i in range(len(out)):
            for j in range(i + 1, len(out)):
                a, b = out[i], out[j]
                grown = fitz.Rect(a.x0 - gap, a.y0 - gap, a.x1 + gap, a.y1 + gap)
                union = a | b
                if max_area is not None and union.width * union.height > max_area:
                    continue
                if grown.intersects(b):
                    out[i] = a | b
                    del out[j]
                    changed = True
                    break
            if changed:
                break
    return out


def box_share(bbox: dict | None, unrotated_page: fitz.Rect) -> float | None:
    """How much of its page a stored box covers, or None for no usable box."""
    if not bbox:
        return None
    try:
        w, h = float(bbox["width"]), float(bbox["height"])
    except (KeyError, TypeError, ValueError):
        return None
    if w <= 0 or h <= 0:
        return None
    return w * h / (unrotated_page.width * unrotated_page.height)


def outline_rect(bbox: dict, unrotated_page: fitz.Rect, pad: float) -> fitz.Rect | None:
    """The box itself, padded and on the page, whatever its size (an
    outline, unlike a cloud, claims nothing about precision)."""
    try:
        x, y, w, h = (float(bbox[k]) for k in ("x", "y", "width", "height"))
    except (KeyError, TypeError, ValueError):
        return None
    rect = fitz.Rect(x - pad, y - pad, x + w + pad, y + h + pad) & unrotated_page
    return None if rect.is_empty else rect


def font_size_for(page_rect: fitz.Rect) -> float:
    """Callout text readable when the sheet is printed at size: the team's
    notes on a 36x24 sheet are ~15-18pt."""
    return max(9.0, round(min(page_rect.width, page_rect.height) / 110, 1))


def wrap(text: str, chars: int) -> list[str]:
    lines: list[str] = []
    for para in (text or "").splitlines() or [""]:
        lines += textwrap.wrap(para, chars) or [""]
    return lines


def _overlap(a: fitz.Rect, b: fitz.Rect) -> float:
    r = a & b
    return 0.0 if r.is_empty else r.width * r.height


def place_callout(
    size: tuple[float, float], clouds: list[fitz.Rect], page: fitz.Rect, taken: list[fitz.Rect], gap: float
) -> fitz.Rect:
    """Where a callout box goes, in DISPLAY space: beside the first cloud —
    right, left, below, above — fully on the sheet, clear of every cloud and
    of the callouts already placed. When nothing is clear, the least-covered
    spot wins: a note over the drawing beats a note off the page."""
    w, h = size
    margin = 0.02 * min(page.width, page.height)
    inside = fitz.Rect(page.x0 + margin, page.y0 + margin, page.x1 - margin, page.y1 - margin)
    if not clouds:
        return fitz.Rect(inside.x0, inside.y0, inside.x0 + w, inside.y0 + h)
    c = clouds[0]
    cy, cx = (c.y0 + c.y1) / 2, (c.x0 + c.x1) / 2
    options = [
        fitz.Rect(c.x1 + gap, cy - h / 2, c.x1 + gap + w, cy + h / 2),
        fitz.Rect(c.x0 - gap - w, cy - h / 2, c.x0 - gap, cy + h / 2),
        fitz.Rect(cx - w / 2, c.y1 + gap, cx + w / 2, c.y1 + gap + h),
        fitz.Rect(cx - w / 2, c.y0 - gap - h, cx + w / 2, c.y0 - gap),
    ]

    def clamp(r: fitz.Rect) -> fitz.Rect:
        dx = max(inside.x0 - r.x0, 0) - max(r.x1 - inside.x1, 0)
        dy = max(inside.y0 - r.y0, 0) - max(r.y1 - inside.y1, 0)
        return fitz.Rect(r.x0 + dx, r.y0 + dy, r.x1 + dx, r.y1 + dy)

    best, best_cost = None, None
    for i, opt in enumerate(options):
        fits = inside.contains(opt)
        r = opt if fits else clamp(opt)
        cost = sum(_overlap(r, o) for o in clouds + taken) + (0 if fits else 1.0) + i * 1e-3
        if best_cost is None or cost < best_cost:
            best, best_cost = r, cost
    return best


def leader(callout: fitz.Rect, cloud: fitz.Rect) -> tuple[fitz.Point, fitz.Point]:
    """From the callout's edge nearest the cloud to the cloud's nearest edge."""
    ccx, ccy = (cloud.x0 + cloud.x1) / 2, (cloud.y0 + cloud.y1) / 2
    start = fitz.Point(min(max(ccx, callout.x0), callout.x1), min(max(ccy, callout.y0), callout.y1))
    end = fitz.Point(min(max(start.x, cloud.x0), cloud.x1), min(max(start.y, cloud.y0), cloud.y1))
    return start, end


# --- marking one sheet ---------------------------------------------------------------


def _unrotated(page: fitz.Page) -> fitz.Rect:
    return fitz.Rect(page.rect * page.derotation_matrix).normalize()


def mark_sheet(
    page: fitz.Page, item: Item, marks: list[Mark], taken: list[fitz.Rect] | None = None
) -> tuple[list[fitz.Rect], fitz.Rect, list[str]]:
    """Cloud the FINDING evidence on this page, outline the CONTEXT, write one
    callout and draw a leader from it to each cloud. Returns the clouds and
    the callout in DISPLAY space (for the cover's snippets), and what could
    not be pinpointed, said in words for the cover.

    A finding box too broad to be a location (`PRECISE_SHARE`) is outlined
    and labelled "not pinpointed" rather than clouded; one that is most of the
    sheet is not drawn at all. Either way the callout and the cover say so —
    a missing cloud must never read as "nothing here"."""
    unrot = _unrotated(page)
    fs = font_size_for(page.rect)
    pad = fs * 1.5
    page_area = unrot.width * unrot.height
    sheet = marks[0].sheet_number or f"page {marks[0].combined_page_number or marks[0].page_number}"
    flags: list[str] = []
    precise: list[fitz.Rect] = []
    broad: list[fitz.Rect] = []
    whole = False
    context: list[tuple[fitz.Rect, str | None]] = []
    for m in marks:
        share = box_share(m.bbox, unrot)
        if m.role == "context":
            if share is not None and share <= WHOLE_SHEET_SHARE:
                r = outline_rect(m.bbox, unrot, pad / 2)
                if r is not None:
                    context.append((r, m.label))
            continue
        if share is None or share > WHOLE_SHEET_SHARE:
            whole = True
        elif share > PRECISE_SHARE:
            r = outline_rect(m.bbox, unrot, pad / 2)
            if r is not None:
                broad.append(r)
        else:
            r = cloud_rect(m.bbox, unrot, pad)
            if r is not None:
                precise.append(r)
    clouds_u = merge_rects(precise, gap=pad, max_area=PRECISE_SHARE * page_area)
    to_display, to_unrot = page.rotation_matrix, page.derotation_matrix
    clouds_d = [fitz.Rect(r * to_display).normalize() for r in clouds_u]
    findings = [m for m in marks if m.role != "context"]
    if findings and not clouds_u:
        flags.append(
            f"{sheet}: the cited evidence covers a broad area, so the exact location is NOT pinpointed — review the "
            + ("outlined area." if broad else "whole sheet.")
        )
    elif whole or broad:
        flags.append(f"{sheet}: part of the cited evidence covers a broad area and is outlined, not clouded.")

    width = max(2.0, round(fs / 3, 1))  # the team's clouds are ~5pt on a 36x24 sheet
    for r in clouds_u:
        cloud = page.add_polygon_annot([r.tl, r.tr, r.br, r.bl, r.tl])
        cloud.set_border(width=width, clouds=2)
        cloud.set_colors(stroke=RED)
        cloud.set_info(title="RFI review", content=item.callout_heading, subject="Cloud")
        cloud.update()
    for r in broad:
        _outline(page, r, RED, width, "AREA CITED — NOT PINPOINTED", fs)
    for r, label in context:
        _outline(page, r, CONTEXT, max(1.0, width * 0.6), label or "CONTEXT — referenced, not the issue location", fs * 0.7)

    if findings:
        heading = item.callout_heading
        body = f"{heading}\n{item.question}"
        if not clouds_u:
            body += "\n(Location on this sheet not pinpointed — see the cover.)"
    else:
        # A sheet that only carries context: a typical detail, a general
        # note. Saying the question here would read as though the column
        # were on this sheet.
        body = f"{item.callout_heading}\nCONTEXT ONLY: referenced by this RFI. The issue is not located on this sheet."
    chars = 48
    lines = wrap(body, chars)
    w = fs * chars * 0.52 + fs
    h = (len(lines) + 0.6) * fs * 1.25
    targets_d = clouds_d or [fitz.Rect(r * to_display).normalize() for r in broad] or [
        fitz.Rect(r * to_display).normalize() for r, _ in context
    ]
    # The callout must not cover an outlined occurrence or note either.
    others_d = [fitz.Rect(r * to_display).normalize() for r in broad] + [
        fitz.Rect(r * to_display).normalize() for r, _ in context
    ]
    callout_d = place_callout((w, h), targets_d, page.rect, (taken or []) + others_d, gap=fs * 3)
    callout_u = fitz.Rect(callout_d * to_unrot).normalize()
    colour = RED if findings else CONTEXT
    note = page.add_freetext_annot(
        callout_u,
        "\n".join(lines),
        fontsize=fs,
        fontname="helv",
        text_color=colour,
        fill_color=CALLOUT_FILL,
        border_color=colour,
        rotate=page.rotation,
    )
    # PyMuPDF 1.25 draws a FreeText only when its colours are given AGAIN on
    # update(), and set_info() blanks its appearance — so no title on this one.
    note.set_border(width=max(1.0, width * 0.5))
    note.update(fontsize=fs, text_color=colour, fill_color=CALLOUT_FILL, border_color=colour, rotate=page.rotation)

    for cd in clouds_d:
        a, b = leader(callout_d, cd)
        line = page.add_line_annot(a * to_unrot, b * to_unrot)
        line.set_border(width=max(1.0, width * 0.6))
        line.set_colors(stroke=RED)
        line.set_line_ends(fitz.PDF_ANNOT_LE_NONE, fitz.PDF_ANNOT_LE_OPEN_ARROW)
        line.set_info(title="RFI review", subject="Leader")
        line.update()
    if taken is not None:
        taken.append(callout_d)
    return clouds_d, callout_d, flags


def _outline(page: fitz.Page, r: fitz.Rect, colour, width: float, label: str, fs: float) -> None:
    """A dashed rectangle (a Square annotation, editable like the clouds)
    with its label written just above it."""
    box = page.add_rect_annot(r)
    box.set_border(width=width, dashes=[6, 4])
    box.set_colors(stroke=colour)
    box.set_info(title="RFI review", content=label, subject="Context" if colour == CONTEXT else "Area")
    box.update()
    shown = fitz.Rect(r * page.rotation_matrix).normalize()
    size = max(6.0, fs)
    tw = fitz.get_text_length(label, fontname="helv", fontsize=size) + size
    # Tall enough for the line to fit: a FreeText whose box is shorter than
    # its line draws nothing at all.
    tag_d = fitz.Rect(shown.x0, shown.y0 - size * 2.4, shown.x0 + tw + size, shown.y0) & page.rect
    if tag_d.is_empty or tag_d.width < tw * 0.5:
        return
    white = (1.0, 1.0, 1.0)
    tag = page.add_freetext_annot(
        fitz.Rect(tag_d * page.derotation_matrix).normalize(), label, fontsize=size, fontname="helv",
        text_color=colour, fill_color=white, rotate=page.rotation,
    )
    tag.update(fontsize=size, text_color=colour, fill_color=white, rotate=page.rotation)


# --- the cover form ------------------------------------------------------------------


def _plain(text: str) -> str:
    """The cover's base-14 font has no em dash (it printed as a dot)."""
    return (text or "").replace("\u2014", "-").replace("\u2013", "-")


def _text(page, x, y, text, size=10, bold=False, color=(0, 0, 0), right=False):
    font = "helv" if not bold else "hebo"
    text = _plain(text)
    if right:
        x -= fitz.get_text_length(text, fontname=font, fontsize=size)
    page.insert_text((x, y), text, fontsize=size, fontname=font, color=color)


def _box_text(page, rect, text, size=10, color=(0, 0, 0)) -> float:
    """Wrapped text inside rect; returns the height used."""
    chars = max(20, int(rect.width / (size * 0.5)))
    lines = wrap(_plain(text), chars)
    y = rect.y0 + size
    for line in lines:
        if y > rect.y1:
            break
        page.insert_text((rect.x0, y), line, fontsize=size, fontname="helv", color=color)
        y += size * 1.25
    return y - rect.y0


def _text_height(text: str, width: float, size: float) -> float:
    chars = max(20, int(width / (size * 0.5)))
    return len(wrap(text, chars)) * size * 1.25 + size


def _flow(out: fitz.Document, page: fitz.Page, y: float, text: str, item: "Item", size: float, color) -> fitz.Page:
    """Write wrapped text from `y` down, continuing on new pages of `out`
    for as long as it runs — nothing is dropped at the bottom of the form."""
    chars = max(20, int(532 / (size * 0.5)))
    y += size
    for line in wrap(_plain(text), chars):
        if y > 770:
            page = out.new_page(width=LETTER.width, height=LETTER.height)
            _text(page, 576, 40, f"{item.title} — continued", size=12, bold=True, right=True)
            y = 70
        page.insert_text((40, y), line, fontsize=size, fontname="helv", color=color)
        y += size * 1.25
    return page


def cover_page(out: fitz.Document, item: Item, snippets: list[tuple[bytes, str]], missing: list[str]) -> fitz.Page:
    page = out.new_page(width=LETTER.width, height=LETTER.height)
    if item.company:
        _text(page, 36, 50, item.company.upper(), size=16, bold=True, color=(0.7, 0.1, 0.1))
    _text(page, 576, 34, f"PROJECT - {item.project.upper()}", size=13, bold=True, right=True)
    _text(page, 576, 72, item.title, size=26, bold=True, right=True)
    for y in (86, 90):
        page.draw_line((18, y), (594, y), color=(0.9, 0.2, 0.2), width=1)

    box = fitz.Rect(18, 106, 594, 190)
    page.draw_rect(box, color=(0, 0, 0), width=0.8)
    left = [("Date Issued:", item.issued.strftime("%m/%d/%Y") if item.issued else ""),
            ("Author:", item.author or ""), ("Discipline:", (item.discipline or "").upper())]
    for i, (label, value) in enumerate(left):
        _text(page, 104, 126 + i * 18, label, bold=True, right=True)
        _text(page, 110, 126 + i * 18, value[:34])
    _text(page, 376, 126, "Description:", bold=True, right=True)
    _box_text(page, fitz.Rect(382, 117, 590, 150), item.subject, size=9)
    _text(page, 376, 162, "Plan/Sheet:", bold=True, right=True)
    _text(page, 382, 162, ", ".join(item.sheets)[:44])
    _text(page, 376, 180, "Issue:", bold=True, right=True)
    if item.revision:
        _box_text(page, fitz.Rect(382, 171, 590, 202), item.revision, size=8)
    else:
        _text(page, 382, 180, "Unknown - not read from the title block", size=9)

    heading = "BIM RFI Description:" if not item.draft else "Proposed RFI Description:"
    _text(page, 26, 220, heading, size=12, bold=True)
    page.draw_line((26, 223), (26 + fitz.get_text_length(heading, fontname="hebo", fontsize=12), 223), color=(0, 0, 0), width=0.8)
    q = fitz.Rect(40, 232, 572, 232 + 70)
    body = f"Q.1) {item.question}"
    chars = int(q.width / 5)
    need = (len(wrap(body, chars)) + 1) * 12.5
    q.y1 = q.y0 + max(60, need)
    page.draw_rect(q, color=None, fill=QUESTION_FILL)
    used = _box_text(page, fitz.Rect(q.x0 + 4, q.y0 + 2, q.x1 - 4, q.y1), body)
    y = max(q.y1, q.y0 + used) + 10
    for m in missing:
        y += _box_text(page, fitz.Rect(40, y, 572, 760), m, size=8.5, color=(0.7, 0.1, 0.1)) + 2
    note = None
    if item.draft:
        note = "DRAFT — proposed by the RFI review and not issued. A person must accept it before it has an RFI number."
        if item.reasoning:
            note += f" Why flagged: {item.reasoning}"

    # The pictures get the room the audit note leaves; the note itself is
    # never cut short. It used to sit in a fixed 60pt box and stopped
    # mid-list ("Checked: S2.106, S2.107,") — a list that ends in a comma
    # tells the reader nothing about what was or was not checked.
    note_h = _text_height(note, 532, 8.5) + 6 if note else 0
    room = min(760 - y - note_h, 420)
    if snippets and room > 120:
        each = room / len(snippets)
        for png, caption in snippets:
            img = fitz.Pixmap(png)
            cap_h = 26
            avail = fitz.Rect(56, y + 6, 556, y + each - cap_h)
            scale = min(avail.width / img.width, avail.height / img.height)
            w, h = img.width * scale, img.height * scale
            r = fitz.Rect(306 - w / 2, avail.y0, 306 + w / 2, avail.y0 + h)
            page.insert_image(r, stream=png)
            _text(page, 306 + fitz.get_text_length(caption, fontsize=16) / -2, r.y1 + 20, caption, size=16, color=RED)
            y += each
    if note:
        _flow(out, page, y + 4, note, item, size=8.5, color=(0.45, 0.45, 0.45))
    _text(page, 36, 780, "Clouds mark the evidence this question cites. Marks are PDF annotations and can be edited.",
          size=7, color=(0.5, 0.5, 0.5))
    return page


def snippet(page: fitz.Page, cloud_display: fitz.Rect, callout: fitz.Rect | None = None, edge: int = 1400) -> bytes:
    """The cloud, its callout, and enough drawing round them to see what the
    problem is about — the crop the team pastes onto the cover form."""
    area = cloud_display | callout if callout is not None else fitz.Rect(cloud_display)
    grow = max(area.width, area.height) * 0.15 + font_size_for(page.rect) * 4
    clip = fitz.Rect(area.x0 - grow, area.y0 - grow, area.x1 + grow, area.y1 + grow) & page.rect
    zoom = edge / max(clip.width, clip.height)
    return page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip, annots=True).tobytes("png")


# --- composing a package -------------------------------------------------------------


# What the title block says the sheet was issued AS, and when. A reviewer's
# critique of a JETRIGHT package: it printed "Revision: Unknown" over sheets
# whose title blocks plainly read "03/31/26 90% MNAA-AIR REVIEW SUBMITTAL" —
# and the issue status is evidence (an architectural set updated for pricing
# against a structural set still marked NOT FOR CONSTRUCTION explains a
# disagreement before anyone asks about it).
_ISSUE_LINE = re.compile(
    r"(?<!NOT )(?<!NOT  )\b(?:\d{1,3}%[A-Z0-9 &/.-]*(?:SUBMITTAL|SUBMISSION|SUB|SET|REVIEW|DOCUMENTS|DRAWINGS)"
    r"|ISSUED? FOR [A-Z][A-Z /&-]{2,40}|(?:BID|PERMIT|CONSTRUCTION|PRICING|TENDER) (?:SET|DOCUMENTS)"
    r"|(?:[A-Z]+ ){0,2}FOR (?:PERMIT|PRICING|BID|CONSTRUCTION|REVIEW)(?: / [A-Z]+)?"
    r"|ADDENDUM\s*#?\s*\d+|REVISION\s*#?\s*\d+)\b"
)
_ISSUE_DATE = re.compile(r"(?<![\d/])(\d{1,2}/\d{1,2}/\d{2,4})(?![\d/])(?!\s*\d{1,2}:\d{2})")
_NOT_FOR_CONSTRUCTION = re.compile(r"\bNOT\s+FOR\s+CONSTRUCTION\b")
TITLE_BLOCK_SHARE = 0.25  # the right-hand quarter (or bottom quarter) of the sheet


def title_block_issue(page) -> str | None:
    """"03/31/26 90% MNAA-AIR REVIEW SUBMITTAL" (plus "NOT FOR CONSTRUCTION"
    when the sheet says so), read from the title block, or None.

    Only lines in the title-block band — the right or bottom quarter of the
    sheet as displayed — count, so a general note saying "ISSUED FOR
    CONSTRUCTION DRAWINGS SHALL..." in the body is not read as the issue. A
    date followed by a clock time is the plot stamp, not the issue date. When
    the block lists several issues, the one with the latest date wins."""
    rect = page.rect
    lines = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            text = " ".join(s["text"] for s in line["spans"]).strip()
            if not text:
                continue
            box = fitz.Rect(line["bbox"]) * page.rotation_matrix
            if box.x0 < rect.width * (1 - TITLE_BLOCK_SHARE) and box.y0 < rect.height * (1 - TITLE_BLOCK_SHARE):
                continue
            lines.append((box, " ".join(text.upper().split())))
    # "NOT FOR CONSTRUCTION" is a status, never an issue: the issue pattern
    # would read "FOR CONSTRUCTION" out of it, the opposite of what it says.
    issues = [
        (box, m.group(0).strip())
        for box, text in lines
        for m in [_ISSUE_LINE.search(_NOT_FOR_CONSTRUCTION.sub(" ", text))]
        if m and m.group(0).strip()
    ]
    # The stamp is often set over two lines ("NOT FOR" / "CONSTRUCTION").
    band = " ".join(text for _, text in sorted(lines, key=lambda l: (round(l[0].y0), l[0].x0)))
    stamped = _NOT_FOR_CONSTRUCTION.search(band) is not None
    if not issues:
        return "NOT FOR CONSTRUCTION" if stamped else None
    dated = []
    for box, issue in issues:
        near = [
            (abs(b.y0 - box.y0) + abs(b.x0 - box.x0) / 10, d.group(1))
            for b, text in lines
            for d in [_ISSUE_DATE.search(text)]
            if d and abs((b.y0 + b.y1) / 2 - (box.y0 + box.y1) / 2) <= max(6.0, box.height)
        ]
        date = min(near)[1] if near else None
        dated.append((_date_key(date), date, issue))
    _, date, issue = max(dated)
    out = f"{date} {issue}" if date else issue
    if stamped and "NOT FOR CONSTRUCTION" not in out:
        out += " (NOT FOR CONSTRUCTION)"
    return out


def sheet_issues(item: Item, open_doc) -> str | None:
    """The issue the finding's sheets were printed under: one line when they
    agree, each sheet's own when they do not — that difference is itself
    evidence, and the reader should see it without opening the title blocks."""
    issues: dict[str, str | None] = {}
    for m in item.marks:
        if m.role != "finding" or not m.document_id:
            continue
        label = m.sheet_number or f"page {m.combined_page_number or m.page_number}"
        if label in issues:
            continue
        doc = open_doc(m.document_id)
        issue = None
        if doc is not None and 1 <= m.page_number <= doc.page_count:
            try:
                issue = title_block_issue(doc[m.page_number - 1])
            except Exception as exc:  # an unreadable title block is an unknown issue
                log.warning("rfi package: title block of %s unreadable: %s", label, exc)
        issues[label] = issue
    known = {k: v for k, v in issues.items() if v}
    if not known:
        return None
    if len(set(known.values())) == 1 and len(known) == len(issues):
        return next(iter(known.values()))
    return "; ".join(f"{k}: {v}" for k, v in known.items())


def _date_key(date: str | None) -> tuple[int, int, int]:
    if not date:
        return (0, 0, 0)
    m, d, y = (int(x) for x in date.split("/"))
    return (y + 2000 if y < 100 else y, m, d)


def render(items: list[Item], open_doc) -> tuple[fitz.Document, list[str]]:
    """The whole package. `open_doc(document_id)` returns a fitz.Document of
    the ORIGINAL PDF, or None when it cannot be had."""
    out = fitz.open()
    notes: list[str] = []
    for item in items:
        sheets = fitz.open()
        taken_by_page: dict[int, list[fitz.Rect]] = {}
        order: list[tuple[str, int]] = []
        by_page: dict[tuple[str, int], list[Mark]] = {}
        missing: list[str] = []
        for m in item.marks:
            if not m.document_id:
                missing.append(f"{m.sheet_number or 'A sheet'} is no longer in the project, so it is not marked up here.")
                continue
            key = (m.document_id, m.page_number)
            if key not in by_page:
                by_page[key] = []
                order.append(key)
            by_page[key].append(m)
        # A whole-sheet picture is context once a finding is pinned to an
        # occurrence. Without one, it is the only record that the problem is
        # on that sheet at all, so it stays a finding — drawn as "not
        # pinpointed", never as a cloud.
        anchored = any(m.kind == "occurrence" and m.role == "finding" for m in item.marks)
        if not anchored:
            for m in item.marks:
                if m.kind == "page":
                    m.role = "finding"
        snippets: list[tuple[bytes, str]] = []
        for doc_id, page_no in order[:MAX_SHEETS_PER_ITEM]:
            src = open_doc(doc_id)
            if src is None or not 1 <= page_no <= src.page_count:
                missing.append(f"Page {page_no} of a drawing could not be opened, so it is not marked up here.")
                continue
            sheets.insert_pdf(src, from_page=page_no - 1, to_page=page_no - 1, annots=False)
            page = sheets[-1]
            marks = by_page[(doc_id, page_no)]
            clouds, callout, flags = mark_sheet(page, item, marks, taken_by_page.setdefault(len(sheets) - 1, []))
            missing += flags
            label = marks[0].sheet_number or f"page {marks[0].combined_page_number or page_no}"
            for c in clouds:
                if len(snippets) < MAX_SNIPPETS:
                    snippets.append((snippet(sheets[-1], c, callout), label))
        if len(order) > MAX_SHEETS_PER_ITEM:
            missing.append(f"{len(order) - MAX_SHEETS_PER_ITEM} more sheet(s) are cited; only the first {MAX_SHEETS_PER_ITEM} are included.")
        if item.revision is None:
            item.revision = sheet_issues(item, open_doc)
        cover_page(out, item, snippets, missing)
        if sheets.page_count:  # inserting an empty document is an error
            out.insert_pdf(sheets, annots=True)
        sheets.close()
        notes += missing
    return out, notes


# --- loading -------------------------------------------------------------------------


def _marks_from_evidence(evidence) -> list[Mark]:
    out = []
    for e in evidence if isinstance(evidence, list) else []:
        if not isinstance(e, dict) or e.get("pageNumber") is None:
            continue
        # A model's account and a server measurement point at no place on
        # the drawing; the cloud marks the drawing's own evidence.
        if e.get("kind") in ("description", "aid"):
            continue
        role = "context" if e.get("role") == "context" else "finding"
        label = None
        if role == "context":
            label = "NOT VERIFIED — check this occurrence" if e.get("verification") == "not_verified" else None
        out.append(Mark(e.get("documentId"), int(e["pageNumber"]), e.get("combinedPageNumber"),
                        e.get("sheetNumber"), e.get("bbox"), e.get("observation"), role=role, label=label,
                        kind=e.get("kind")))
    return out


def load_items(conn, project_id: str, refs: list[dict]) -> list[Item]:
    project, = conn.execute("SELECT name FROM projects WHERE id = %s", (project_id,)).fetchone()
    items: list[Item] = []
    for ref in refs[:MAX_ITEMS]:
        if ref.get("type") == "rfi":
            row = conn.execute(
                """
                SELECT r.number, r.subject, r.question, r.discipline, r."createdAt", u.name, u.company, r.status::text
                  FROM rfis r LEFT JOIN users u ON u.id = r."createdById"
                 WHERE r.id = %s AND r."projectId" = %s
                """,
                (ref["id"], project_id),
            ).fetchone()
            if not row:
                continue
            locs = conn.execute(
                """
                SELECT "documentId", "pageNumber", "combinedPageNumber", "sheetNumber", bbox
                  FROM rfi_locations WHERE "rfiId" = %s AND "supersededById" IS NULL ORDER BY "createdAt"
                """,
                (ref["id"],),
            ).fetchall()
            items.append(Item(
                "rfi", ref["id"], row[0], row[1], row[2], row[3], row[4], _initials(row[5]), row[6], project,
                draft=row[7] == "draft",
                marks=[Mark(l[0], l[1], l[2], l[3], l[4]) for l in locs],
            ))
        elif ref.get("type") == "candidate":
            row = conn.execute(
                """
                SELECT c.subject, c.question, c.reasoning, c.evidence, c."createdAt", c.status::text, r.number, r.discipline
                  FROM rfi_candidates c LEFT JOIN rfis r ON r.id = c."rfiId"
                 WHERE c.id = %s AND c."projectId" = %s
                """,
                (ref["id"], project_id),
            ).fetchone()
            if not row:
                continue
            accepted = row[5] == "accepted" and row[6] is not None
            marks = _marks_from_evidence(row[3])
            items.append(Item(
                "candidate", ref["id"], row[6] if accepted else None, row[0], row[1],
                row[7] or evidence_discipline(conn, project_id, marks), row[4],
                "RFI review", None, project, draft=not accepted, marks=marks, reasoning=row[2],
            ))
    return items


def cover_discipline(disciplines: list[str | None]) -> str | None:
    """What the cover's Discipline field says for a finding not yet filed:
    the discipline of the sheets it is ON, in the order they are cited.
    A draft has no RFI row to carry one, and the field stood empty on every
    project-check package. Two disciplines are both named ("architectural /
    structural" for a grid naming dispute); none read leaves it empty."""
    seen: list[str] = []
    for d in disciplines:
        if d and d != "other" and d not in seen:
            seen.append(d)
    return " / ".join(seen[:3]) or None


def evidence_discipline(conn, project_id: str, marks: list[Mark]) -> str | None:
    finding = [m for m in marks if m.role == "finding" and m.document_id] or [m for m in marks if m.document_id]
    if not finding:
        return None
    rows = conn.execute(
        """
        SELECT p."documentId", p."pageNumber", p.discipline::text
          FROM pages p JOIN documents d ON d.id = p."documentId"
         WHERE d."projectId" = %s AND p."documentId" = ANY(%s::text[])
        """,
        (project_id, sorted({m.document_id for m in finding})),
    ).fetchall()
    by_page = {(r[0], r[1]): r[2] for r in rows}
    return cover_discipline([by_page.get((m.document_id, m.page_number)) for m in finding])


def _initials(name: str | None) -> str | None:
    """The form's Author field carries initials ("AA", "AD")."""
    if not name:
        return None
    parts = [p for p in name.split() if p]
    return "".join(p[0].upper() for p in parts[:3]) or None


# --- the job -------------------------------------------------------------------------


def _set(package_id: str, **fields) -> None:
    columns = ", ".join(f'"{k}" = %s' for k in fields)
    with db.connect() as conn:
        conn.execute(f"UPDATE rfi_packages SET {columns} WHERE id = %s", (*fields.values(), package_id))


def run(package_id: str) -> dict:
    with db.connect() as conn:
        row = conn.execute('SELECT "projectId", items, status FROM rfi_packages WHERE id = %s', (package_id,)).fetchone()
    if not row:
        return {"skipped": "no such package"}
    project_id, refs, status = row
    if status == "ready":
        return {"skipped": "ready"}
    _set(package_id, status="running", error=None)
    try:
        with db.connect() as conn:
            items = load_items(conn, project_id, refs or [])
            doc_ids = sorted({m.document_id for i in items for m in i.marks if m.document_id})
            keys = dict(conn.execute(
                'SELECT id, "spacesKey" FROM documents WHERE id = ANY(%s::text[]) AND "projectId" = %s',
                (doc_ids, project_id),
            ).fetchall())
        if not items:
            raise ValueError("none of the requested RFIs or findings exist in this project")
        with tempfile.TemporaryDirectory() as tmp:
            opened: dict[str, fitz.Document | None] = {}

            def open_doc(doc_id: str):
                if doc_id not in opened:
                    path = os.path.join(tmp, f"{doc_id}.pdf")
                    try:
                        storage.download_to_file(keys[doc_id], path)
                        opened[doc_id] = fitz.open(path)
                    except Exception as exc:
                        log.warning("rfi package %s: could not open %s: %s", package_id[:8], doc_id[:8], exc)
                        opened[doc_id] = None
                return opened[doc_id]

            try:
                out, notes = render(items, open_doc)
                data = out.tobytes(garbage=3, deflate=True)
                pages = out.page_count
                out.close()
            finally:
                for d in opened.values():
                    if d is not None:
                        d.close()
        key = rfi_package_key(project_id, package_id)
        storage.put_bytes(key, data, "application/pdf")
        _set(package_id, status="ready", key=key, pages=pages, notes=json.dumps(notes), finishedAt=datetime.utcnow())
        log.info("rfi package %s: %d item(s), %d page(s), %.1f MB", package_id[:8], len(items), pages, len(data) / 1e6)
        return {"items": len(items), "pages": pages, "bytes": len(data)}
    except Exception as exc:
        _set(package_id, status="failed", error=str(exc)[:500], finishedAt=datetime.utcnow())
        log.exception("rfi package %s failed", package_id[:8])
        return {"failed": str(exc)}
