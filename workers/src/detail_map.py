"""The project map: each sheet's detail titles and the section/detail symbols it
prints, read off the PDF by code and saved on the page — and the check it makes
possible, a symbol pointing at a detail its sheet does not have.

Leland S4.10 prints "T.O.C. SEE 24/S4.02"; S4.02 titles details 6, 11, 21 and
26 and has no detail 24. `dangling_reference` cannot see it (S4.02 IS in the
set), and a review read the note as an answer. Measured over all 648 Leland +
UT Law pages before this shipped: 2 such references on Leland (24/S4.02 and
"SEE 0/S4.00"), 1 on UT Law.

A page is read once per MAP_VERSION and cached on `pages.detailMap`, because
reading a sheet's titles and symbols is ~1 s a page and every scan needs it.

The check trusts a sheet's titles only when it read at least MIN_TITLED of them,
and stays quiet when the number is printed on that sheet as large as its other
detail numbers — then the title is there and the reader missed it. Both guards
trade recall for precision: a missed finding is found the normal way.
"""

from __future__ import annotations

import json
import logging
import re
import statistics

log = logging.getLogger("worker.detail_map")

MAP_VERSION = 1
# Fewer titles read than this and the sheet's titles are not known well enough
# to say one is missing.
MIN_TITLED = 3
# A number printed at least this share of the sheet's detail-number size is a
# detail whose title the reader missed, not a missing detail.
LARGE_SHARE = 0.6
PAD_PT = 6.0
_NUMBER = re.compile(r"^\d{1,3}[A-Z]?$")


def read_page(page) -> dict:
    """The map entry of one fitz page: titled details, symbols, and the largest
    size each detail-number-shaped token is printed at."""
    import callouts
    import review_aids

    details = {n: t for n, t, _ in review_aids.detail_titles(review_aids._display_lines(page)) if n}
    printed: dict[str, float] = {}
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                token = span["text"].strip().upper()
                if _NUMBER.match(token):
                    printed[token] = max(printed.get(token, 0.0), round(span["size"], 2))
    links = [{"detail": c["detail"], "sheet": c["sheet"], "bbox": [round(v, 2) for v in c["rect"]]}
             for c in callouts.any_callouts(callouts.display_words(page))]
    return {"details": details, "links": links, "printed": printed}


def ensure(rows, open_page, progress=None) -> dict[tuple[str, int], dict]:
    """The map entry of every page in `rows` (rfi_checks.Page), reading and
    saving the pages not yet read at MAP_VERSION. Keyed (documentId, page)."""
    import db

    ids = [r.id for r in rows]
    with db.connect() as conn:
        stored = {pid: (m, v) for pid, m, v in conn.execute(
            'SELECT id, "detailMap", "detailMapVersion" FROM pages WHERE id = ANY(%s)', (ids,)).fetchall()}
    out, todo = {}, []
    for r in rows:
        m, v = stored.get(r.id, (None, None))
        if v == MAP_VERSION and m is not None:
            out[(r.document_id, r.page_number)] = json.loads(m) if isinstance(m, str) else m
        else:
            todo.append(r)
    for i, r in enumerate(todo, 1):
        entry = {"details": {}, "links": [], "printed": {}}
        try:
            page = open_page(r.document_id, r.page_number)
            if page is not None:
                entry = read_page(page)
        except Exception as exc:  # an unreadable page has no map entry worth trusting; read it again next time
            log.warning("detail map: could not read page %s: %s", r.id[:8], exc)
            out[(r.document_id, r.page_number)] = entry
            continue
        with db.connect() as conn:
            conn.execute('UPDATE pages SET "detailMap" = %s::jsonb, "detailMapVersion" = %s WHERE id = %s',
                         (json.dumps(entry), MAP_VERSION, r.id))
        out[(r.document_id, r.page_number)] = entry
        if progress:
            progress(i, len(todo))
    return out


def missing_details(sources, targets) -> list[dict]:
    """Symbols pointing at a detail their sheet does not have. Pure.

    `sources`: (page, entry) whose symbols are checked; `targets`: (page, entry)
    for every sheet a symbol may point at. Returns one dict per (sheet, detail)
    with the referring places."""
    by_sheet: dict[str, dict] = {}
    for page, entry in targets:
        if not page.sheet_number:
            continue
        t = by_sheet.setdefault(page.sheet_number.upper(), {"page": page, "details": {}, "printed": {}})
        t["details"].update(entry.get("details") or {})
        for k, v in (entry.get("printed") or {}).items():
            t["printed"][k] = max(t["printed"].get(k, 0.0), v)
    found: dict[tuple[str, str], dict] = {}
    for page, entry in sources:
        for link in entry.get("links") or []:
            num, sheet = link["detail"].upper(), link["sheet"].upper()
            t = by_sheet.get(sheet)
            # A sheet not in the set is dangling_reference's finding.
            if t is None or len(t["details"]) < MIN_TITLED or num in t["details"]:
                continue
            sizes = [t["printed"][n] for n in t["details"] if n in t["printed"]]
            if not sizes or t["printed"].get(num, 0.0) >= LARGE_SHARE * statistics.median(sizes):
                continue
            f = found.setdefault((sheet, num), {"sheet": sheet, "detail": num, "target": t["page"],
                                                "details": sorted(t["details"], key=_order), "places": []})
            f["places"].append((page, link["bbox"]))
    return list(found.values())


def _order(n: str):
    digits = re.match(r"\d+", n)
    return (int(digits.group()) if digits else 0, n)


def findings(pages, chunks, open_page, every=None, progress=None) -> tuple[list, list[str]]:
    """missing_detail findings for the symbols on `pages`; `every` is where
    their target sheets are looked up (a review's symbols may point at a sheet
    it did not pick). (findings, notes)."""
    from rfi_checks import MAX_EVIDENCE, Finding, fingerprint, page_label

    every = every or pages
    entries = ensure({p.id: p for p in [*pages, *every]}.values(), open_page, progress)
    entry = lambda p: entries.get((p.document_id, p.page_number), {})
    out = []
    for m in missing_details([(p, entry(p)) for p in pages], [(p, entry(p)) for p in every]):
        ref = f"{m['detail']}/{m['sheet']}"
        from_sheets = sorted({page_label(p) for p, _ in m["places"]})
        evidence = []
        for p, (x0, y0, x1, y1) in m["places"][:MAX_EVIDENCE - 1]:
            evidence.append({
                "documentId": p.document_id, "pageNumber": p.page_number,
                "combinedPageNumber": p.combined_page_number, "sheetNumber": p.sheet_number,
                "bbox": {"x": round(x0 - PAD_PT, 2), "y": round(y0 - PAD_PT, 2),
                         "width": round(x1 - x0 + 2 * PAD_PT, 2), "height": round(y1 - y0 + 2 * PAD_PT, 2)},
                "chunkId": None, "quote": ref, "role": "finding",
            })
        t = m["target"]
        listed = ", ".join(m["details"][:12]) + ("…" if len(m["details"]) > 12 else "")
        # The sheet itself, unhighlighted: what is MISSING from it has no box.
        evidence.append({
            "documentId": t.document_id, "pageNumber": t.page_number,
            "combinedPageNumber": t.combined_page_number, "sheetNumber": t.sheet_number,
            "bbox": None,
            "chunkId": None, "quote": f"{m['sheet']}: details {listed}", "role": "context",
        })
        many = len(from_sheets) > 1
        out.append(Finding(
            check_type="missing_detail",
            fingerprint=fingerprint("missing_detail", m["sheet"], m["detail"]),
            confidence="medium",
            subject=f"Detail {ref} referenced but not on {m['sheet']}"[:120],
            question=(f"{', '.join(from_sheets)} refer{'' if many else 's'} to detail {ref}, but {m['sheet']} "
                      f"has details {listed} and no detail {m['detail']}. Which detail is intended?"),
            evidence=evidence,
            facts={"sheet": m["sheet"], "detail": m["detail"], "details": m["details"][:12], "from": from_sheets},
        ))
    return out, []
