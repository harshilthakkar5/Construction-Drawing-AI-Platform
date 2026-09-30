"""Keep historical RFIs out of RFI review INPUT.

An RFI a person already issued is the ANSWER KEY for a review — the thing a
review should find by itself — and never its evidence. A review that reads
the answer and repeats it has measured nothing, and a candidate built on an
old RFI's wording is that RFI again with a new number.

The API excludes a document whose FILENAME says RFI at upload
(apps/api/src/rfiSources.ts). This is the second net, for a file called
"scan_0042.pdf": its first pages READ like an RFI form. Both set
`documents.includeInRfiAnalysis = false` with a reason, which every review
scope query filters on — retrieval, images and the exact checks alike.

It only ever EXCLUDES a document nobody has decided about: a person who put
one back (the Docs tab writes a reason either way) is never overruled by a
re-ingest.
"""

from __future__ import annotations

import re

import logutil

log = logutil.get("rfi_sources")

TEXT_EXCLUSION_REASON = "Reads like an RFI form (its first pages), so it is kept out of RFI review input"

# The heading an RFI form carries, and the fields that make it a form rather
# than a drawing that mentions RFIs in a note ("SUBMIT AN RFI FOR ...").
# "BIM RFI:" is the client's own form (RFI 015's first page).
_HEADING = re.compile(
    r"\bREQUEST\s+FOR\s+INFORMATION\b|\bRFI\s*(?:NO\.?|#|NUMBER)\s*:?\s*\d|\b(?:BIM\s+)?RFI\s*(?:DESCRIPTION\s*)?:",
    re.I,
)
_FORM_FIELDS = re.compile(
    r"\b(?:QUESTION|RESPONSE|ANSWER|REPLY|DATE\s+REQUIRED|DATE\s+ISSUED|AUTHOR|PLAN\s*/\s*SHEET|SUGGESTION|RECOMMENDATION)\b",
    re.I,
)


def looks_like_rfi_text(text: str | None) -> bool:
    """True for the first pages of an RFI form: the heading AND at least two
    distinct form fields. A drawing's general notes say "RFI" and even
    "REQUEST FOR INFORMATION"; they do not also carry a QUESTION box and a
    RESPONSE box."""
    if not text or not _HEADING.search(text):
        return False
    fields = {" ".join(m.group(0).upper().split()) for m in _FORM_FIELDS.finditer(text)}
    return len(fields) >= 2


def exclude_if_rfi(conn, document_id: str, pages: int = 2) -> bool:
    """Exclude the document when its first pages read like an RFI form and
    nobody has decided about it yet. Returns whether it was excluded."""
    rows = conn.execute(
        'SELECT text FROM pages WHERE "documentId" = %s AND "pageNumber" <= %s ORDER BY "pageNumber"',
        (document_id, pages),
    ).fetchall()
    if not looks_like_rfi_text("\n".join(r[0] or "" for r in rows)):
        return False
    cur = conn.execute(
        """
        UPDATE documents SET "includeInRfiAnalysis" = false, "rfiExclusionReason" = %s
         WHERE id = %s AND "includeInRfiAnalysis" AND "rfiExclusionReason" IS NULL
        """,
        (TEXT_EXCLUSION_REASON, document_id),
    )
    if cur.rowcount:
        log.info("document %s reads like an RFI form — kept out of RFI review input", document_id[:8])
    return bool(cur.rowcount)
