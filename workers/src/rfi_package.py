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
CALLOUT_FILL = (1.0, 1.0, 0.6)  # the yellow note the team uses (RFI 010)
QUESTION_FILL = (0.82, 0.87, 0.94)  # the cover form's shaded question box
MAX_SHEETS_PER_ITEM = 6
MAX_ITEMS = 50
MAX_SNIPPETS = 2
# Evidence covering more than this share of its page is "the whole sheet":
# clouding it would say nothing about where the problem is.
WHOLE_SHEET_SHARE = 0.5
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


def merge_rects(rects: list[fitz.Rect], gap: float) -> list[fitz.Rect]:
    """Clouds that touch or nearly touch become one: two clouds on one note
    read as two problems."""
    out = [fitz.Rect(r) for r in rects]
    changed = True
    while changed:
        changed = False
        for i in range(len(out)):
            for j in range(i + 1, len(out)):
                a, b = out[i], out[j]
                grown = fitz.Rect(a.x0 - gap, a.y0 - gap, a.x1 + gap, a.y1 + gap)
                if grown.intersects(b):
                    out[i] = a | b
                    del out[j]
                    changed = True
                    break
            if changed:
                break
    return out


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
) -> tuple[list[fitz.Rect], fitz.Rect]:
    """Cloud every piece of evidence on this page, write one callout with the
    question, and draw a leader from it to each cloud. Returns the clouds and
    the callout in DISPLAY space (for the cover's snippets)."""
    unrot = _unrotated(page)
    fs = font_size_for(page.rect)
    pad = fs * 1.5
    clouds_u = merge_rects(
        [r for r in (cloud_rect(m.bbox, unrot, pad) for m in marks) if r is not None], gap=pad
    )
    to_display, to_unrot = page.rotation_matrix, page.derotation_matrix
    clouds_d = [fitz.Rect(r * to_display).normalize() for r in clouds_u]

    width = max(2.0, round(fs / 3, 1))  # the team's clouds are ~5pt on a 36x24 sheet
    for r in clouds_u:
        cloud = page.add_polygon_annot([r.tl, r.tr, r.br, r.bl, r.tl])
        cloud.set_border(width=width, clouds=2)
        cloud.set_colors(stroke=RED)
        cloud.set_info(title="RFI review", content=item.callout_heading, subject="Cloud")
        cloud.update()

    body = f"{item.callout_heading}\n{item.question}"
    chars = 48
    lines = wrap(body, chars)
    w = fs * chars * 0.52 + fs
    h = (len(lines) + 0.6) * fs * 1.25
    callout_d = place_callout((w, h), clouds_d, page.rect, taken or [], gap=fs * 3)
    callout_u = fitz.Rect(callout_d * to_unrot).normalize()
    note = page.add_freetext_annot(
        callout_u,
        "\n".join(lines),
        fontsize=fs,
        fontname="helv",
        text_color=RED,
        fill_color=CALLOUT_FILL,
        border_color=RED,
        rotate=page.rotation,
    )
    # PyMuPDF 1.25 draws a FreeText only when its colours are given AGAIN on
    # update(), and set_info() blanks its appearance — so no title on this one.
    note.set_border(width=max(1.0, width * 0.5))
    note.update(fontsize=fs, text_color=RED, fill_color=CALLOUT_FILL, border_color=RED, rotate=page.rotation)

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
    return clouds_d, callout_d


# --- the cover form ------------------------------------------------------------------


def _text(page, x, y, text, size=10, bold=False, color=(0, 0, 0), right=False):
    font = "helv" if not bold else "hebo"
    if right:
        x -= fitz.get_text_length(text, fontname=font, fontsize=size)
    page.insert_text((x, y), text, fontsize=size, fontname=font, color=color)


def _box_text(page, rect, text, size=10, color=(0, 0, 0)) -> float:
    """Wrapped text inside rect; returns the height used."""
    chars = max(20, int(rect.width / (size * 0.5)))
    lines = wrap(text, chars)
    y = rect.y0 + size
    for line in lines:
        if y > rect.y1:
            break
        page.insert_text((rect.x0, y), line, fontsize=size, fontname="helv", color=color)
        y += size * 1.25
    return y - rect.y0


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
    _text(page, 376, 180, "Revision:", bold=True, right=True)

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
    if item.draft:
        note = "DRAFT — proposed by the RFI review and not issued. A person must accept it before it has an RFI number."
        if item.reasoning:
            note += f" Why flagged: {item.reasoning}"
        y += _box_text(page, fitz.Rect(40, y, 572, y + 60), note, size=8.5, color=(0.45, 0.45, 0.45)) + 6
    for m in missing:
        y += _box_text(page, fitz.Rect(40, y, 572, y + 20), m, size=8.5, color=(0.7, 0.1, 0.1)) + 2

    room = 760 - y
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
        snippets: list[tuple[bytes, str]] = []
        for doc_id, page_no in order[:MAX_SHEETS_PER_ITEM]:
            src = open_doc(doc_id)
            if src is None or not 1 <= page_no <= src.page_count:
                missing.append(f"Page {page_no} of a drawing could not be opened, so it is not marked up here.")
                continue
            sheets.insert_pdf(src, from_page=page_no - 1, to_page=page_no - 1, annots=False)
            page = sheets[-1]
            marks = by_page[(doc_id, page_no)]
            clouds, callout = mark_sheet(page, item, marks, taken_by_page.setdefault(len(sheets) - 1, []))
            label = marks[0].sheet_number or f"page {marks[0].combined_page_number or page_no}"
            for c in clouds:
                if len(snippets) < MAX_SNIPPETS:
                    snippets.append((snippet(sheets[-1], c, callout), label))
        if len(order) > MAX_SHEETS_PER_ITEM:
            missing.append(f"{len(order) - MAX_SHEETS_PER_ITEM} more sheet(s) are cited; only the first {MAX_SHEETS_PER_ITEM} are included.")
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
        out.append(Mark(e.get("documentId"), int(e["pageNumber"]), e.get("combinedPageNumber"),
                        e.get("sheetNumber"), e.get("bbox"), e.get("observation")))
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
            items.append(Item(
                "candidate", ref["id"], row[6] if accepted else None, row[0], row[1], row[7], row[4],
                "RFI review", None, project, draft=not accepted, marks=_marks_from_evidence(row[3]), reasoning=row[2],
            ))
    return items


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
