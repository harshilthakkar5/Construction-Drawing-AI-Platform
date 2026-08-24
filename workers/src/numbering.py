"""The combined-numbering rule (FR-6), as a pure function.

This is the SPECIFICATION that `db.recompute_combined_numbering`'s SQL
implements. The rule decides which page a citation resolves to, so it is the
correctness-critical path in this codebase — and it necessarily exists twice,
once here and once in SQL, because the renumber has to happen inside the
database in one statement under the project lock.

Keeping the rule ALSO as Python lets it be tested without a database, against
`packages/shared/fixtures/combined-numbering.json` — the same fixture
apps/api/src/manifest.test.ts reads. A change to the rule in either language
now fails the other language's tests.

Honest limit: this pins the RULE in both languages, not the SQL. A bug in the
SQL that the rule does not have still needs a database-backed test;
`verify_against_db` exists for that.
"""

from __future__ import annotations


def order_documents(documents: list[dict]) -> list[dict]:
    """(createdAt, id). The id tiebreak is not decoration: two documents
    uploaded in the same second must not renumber the project differently on
    each recompute."""
    return sorted(documents, key=lambda d: (d["createdAt"], d["id"]))


def combined_numbering(documents: list[dict]) -> list[tuple[str, int, int]]:
    """[(document_id, page_number, combined_page_number), ...] in page order.

    `documents` are dicts with id, createdAt, pages and an optional truthy
    `superseded` — a superseded revision is excluded entirely and consumes no
    numbers, or replacing a 5-page drawing would shift every later sheet.
    """
    entries: list[tuple[str, int, int]] = []
    offset = 0
    for document in order_documents([d for d in documents if not d.get("superseded")]):
        for page in range(1, int(document["pages"]) + 1):
            entries.append((document["id"], page, offset + page))
        offset += int(document["pages"])
    return entries


def verify_against_db(project_id: str) -> list[str]:
    """Compare the numbering the DB actually holds against the rule above.

    Returns a list of human-readable discrepancies; empty means they agree.
    This is the check that closes the gap the pure tests cannot: it runs the
    real SQL's OUTPUT past the specification.
    """
    import db

    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT d.id, d."createdAt", d.pages, p."pageNumber", p."combinedPageNumber"
            FROM pages p
            JOIN documents d ON p."documentId" = d.id
            WHERE d."projectId" = %s AND d."supersededAt" IS NULL
            ORDER BY p."combinedPageNumber"
            """,
            (project_id,),
        ).fetchall()

    documents = {
        doc_id: {"id": doc_id, "createdAt": created, "pages": pages}
        for doc_id, created, pages, _page, _combined in rows
    }
    expected = {
        (doc_id, page): combined
        for doc_id, page, combined in combined_numbering(list(documents.values()))
    }

    problems = []
    for doc_id, _created, _pages, page, combined in rows:
        want = expected.get((doc_id, page))
        if want != combined:
            problems.append(
                f"document {doc_id[:8]} page {page}: database says {combined}, "
                f"the rule says {want}"
            )
    return problems
